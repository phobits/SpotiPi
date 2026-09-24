#!/usr/bin/env python3
"""
💤 Snooze Module for SpotiPi

Turns *silencing* the alarm device into a snooze: while a SpotiPi alarm is
active, making the alarm go quiet is interpreted as "snooze". "Silenced" means
either a **pause** (``is_playing == False``) or a **mute** (the device volume
drops to ``MUTE_THRESHOLD`` or below) — the Argon/Forte hardware "snooze" button
mutes rather than pauses, so both paths must count. The alarm playback
automatically resumes after ``snooze_minutes`` (raising the volume back up to
override the mute) and keeps doing so for a ``snooze_window_minutes`` window
after the alarm fired.

Spotify pushes no playback events, and the rest of the backend never polls the
player on its own — only the frontend does, and only while a tab is open. So
this module runs a dedicated background daemon thread (modeled on
``src/core/sleep.py``) that polls ``/me/player`` and drives a small state
machine:

    armed     -> alarm is playing, watching for a pause or mute
    snoozing  -> silence detected, playback paused, waiting until resume_at

When silence is detected the monitor actively pauses playback (so the Pi does
not churn silently through the playlist for the whole snooze window) and resumes
at full volume when ``resume_at`` is reached.

The session is *context-aware*: a pause/mute only counts as snooze while the
alarm's own playlist is the active context on the alarm device. If the user
actively plays something else (different context/device), the session ends
(= dismissed). The window also auto-expires after ``snooze_window_minutes`` and
can be stopped explicitly via :func:`stop_snooze_session`.
"""

import datetime
import json
import logging
import os
import time
from threading import Lock, Thread
from typing import Any, Dict, Optional

from ..api.spotify import (get_access_token, get_current_playback,
                           refresh_access_token, set_volume, start_playback,
                           stop_playback)
from .alarm_logging import log_snooze_probe

# Detect low-power mode once for module-wide optimisations
LOW_POWER_MODE = os.getenv('SPOTIPI_LOW_POWER', '').lower() in ('1', 'true', 'yes', 'on')


def _get_app_data_dir() -> str:
    """Get application data directory path-agnostically."""
    app_name = os.getenv("SPOTIPI_APP_NAME", "spotipi")
    return os.path.expanduser(f"~/.{app_name}")


LOG_DIR = _get_app_data_dir()
STATUS_PATH = os.path.join(LOG_DIR, "snooze_status.json")

# Ensure data directory exists
os.makedirs(LOG_DIR, exist_ok=True)

logger = logging.getLogger('snooze')

# Number of consecutive "silenced" polls required before arming the countdown.
# Guards against transient is_playing=false / volume-dip reads at track boundaries.
PAUSE_DEBOUNCE_READS = 2

# Right after an alarm starts, the first /me/player reads are often stale: the
# device is audibly playing but the API still reports paused / 204 / the previous
# device for some seconds. Counting those as "silenced" would snooze the alarm
# seconds after it rang. A pause/mute/takeover only counts once the monitor has
# confirmed the alarm playing at least once, OR this settle window has elapsed
# (the fallback so a very fast mute still snoozes even if we never caught a clean
# "playing" read).
STARTUP_SETTLE_SECONDS = max(0, int(os.getenv("SPOTIPI_SNOOZE_SETTLE_SECONDS", "45")))

# Device volume at/below this counts as "muted" (the hardware snooze button mutes
# instead of pausing). The alarm plays at alarm_volume (e.g. 50), so a drop into
# this band is unambiguous; the small non-zero floor tolerates devices whose mute
# bottoms out at 1-2% rather than exactly 0.
MUTE_THRESHOLD = 3

# Poll cadence (seconds)
ARMED_INTERVAL = 30 if LOW_POWER_MODE else 15
SNOOZING_INTERVAL = 30

_STATUS_LOCK = Lock()
_STATUS_CACHE: Optional[Dict[str, Any]] = None
_STATUS_CACHE_TS: float = 0.0
_STATUS_CACHE_TTL = max(1.0, float(os.getenv('SPOTIPI_SNOOZE_STATUS_TTL', '5.0')))

# Monitor coordination: only the thread holding the current epoch keeps running.
_MONITOR_LOCK = Lock()
_monitor_epoch = 0
_monitor_thread: Optional[Thread] = None


# ---------------------------------------------------------------------------
# Status persistence (mirrors src/core/sleep.py)
# ---------------------------------------------------------------------------

def _read_status_from_disk() -> Dict[str, Any]:
    try:
        with open(STATUS_PATH, "r", encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {"active": False}
    except (json.JSONDecodeError, OSError) as err:
        logger.warning(f"Error reading snooze status: {err}")
        return {"active": False}


def _get_status_snapshot(force_refresh: bool = False) -> Dict[str, Any]:
    global _STATUS_CACHE, _STATUS_CACHE_TS
    now = time.monotonic()
    with _STATUS_LOCK:
        if not force_refresh and _STATUS_CACHE is not None and (now - _STATUS_CACHE_TS) < _STATUS_CACHE_TTL:
            return dict(_STATUS_CACHE)

        data = _read_status_from_disk()
        _STATUS_CACHE = dict(data)
        _STATUS_CACHE_TS = now
        return data


def _write_status(data: Dict[str, Any]) -> None:
    global _STATUS_CACHE, _STATUS_CACHE_TS
    with _STATUS_LOCK:
        tmp_path = f"{STATUS_PATH}.tmp"
        try:
            with open(tmp_path, "w", encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            os.replace(tmp_path, STATUS_PATH)
        except Exception as exc:  # pragma: no cover - file system errors
            logger.error("Failed to persist snooze status: %s", exc)
            try:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            except OSError:
                pass
            return
        _STATUS_CACHE = dict(data)
        _STATUS_CACHE_TS = time.monotonic()


# ---------------------------------------------------------------------------
# Public status accessor
# ---------------------------------------------------------------------------

def get_snooze_status() -> Dict[str, Any]:
    """Return the current snooze session status for the API/UI.

    Returns ``{"active": False}`` when no session is running (or the 2h window
    has elapsed). When active, exposes the state machine state and countdowns.
    """
    try:
        data = _get_status_snapshot()
        if not data.get("active", False):
            return {"active": False}

        now = time.time()
        window_end = data.get("window_end") or 0
        # Window elapsed -> report inactive (monitor will clean up on its next loop).
        if window_end and now >= window_end:
            return {"active": False}

        state = data.get("state", "armed")
        resume_at = data.get("resume_at") or 0
        resume_in = max(0, int(resume_at - now)) if (state == "snoozing" and resume_at) else 0
        window_remaining = max(0, int(window_end - now)) if window_end else 0

        return {
            "active": True,
            "state": state,
            "snooze_count": int(data.get("snooze_count", 0)),
            "resume_in_seconds": resume_in,
            "resume_at": resume_at,
            "window_end": window_end,
            "window_remaining_seconds": window_remaining,
            "snooze_minutes": data.get("snooze_minutes"),
            "device_name": data.get("device_name"),
            "device_id": data.get("device_id"),
            "playlist_uri": data.get("playlist_uri"),
            "volume": data.get("volume"),
        }
    except Exception:
        logger.exception("Unexpected error getting snooze status")
        return {"active": False}


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

def start_snooze_session(
    *,
    device_id: Optional[str],
    device_name: str,
    playlist_uri: str,
    volume: int,
    shuffle: bool = False,
    window_minutes: int = 120,
    snooze_minutes: int = 9,
) -> bool:
    """Start (or replace) a snooze session and spawn the monitor thread.

    Called right after an alarm successfully started playback. Captures the
    alarm context so the monitor can recognise "our" playback later.
    """
    try:
        if snooze_minutes <= 0 or window_minutes <= 0:
            logger.warning("Invalid snooze durations (snooze=%s, window=%s)", snooze_minutes, window_minutes)
            return False

        now = time.time()
        status = {
            "active": True,
            "state": "armed",
            "alarm_started_at": now,
            "window_end": now + window_minutes * 60,
            "resume_at": 0,
            "device_id": device_id,
            "device_name": device_name,
            "playlist_uri": playlist_uri,
            "volume": volume,
            "shuffle": bool(shuffle),
            "snooze_minutes": snooze_minutes,
            "window_minutes": window_minutes,
            "snooze_count": 0,
        }
        _write_status(status)
        _spawn_monitor()
        logger.info(
            "💤 Snooze session armed: device=%s window=%dmin snooze=%dmin",
            device_name, window_minutes, snooze_minutes,
        )
        log_snooze_probe("session_started", {
            "device_name": device_name,
            "window_minutes": window_minutes,
            "snooze_minutes": snooze_minutes,
            "volume": volume,
        })
        return True
    except Exception:
        logger.exception("Error starting snooze session")
        return False


def stop_snooze_session(reason: str = "user") -> bool:
    """Stop the active snooze session (dismiss). Idempotent."""
    try:
        _write_status({"active": False})
        logger.info("🛑 Snooze session stopped (%s)", reason)
        log_snooze_probe("session_stopped", {"reason": reason})
        return True
    except Exception:
        logger.exception("Error stopping snooze session")
        return False


def trigger_snooze(token: Optional[str] = None) -> bool:
    """Put the active session into "snoozing" immediately (external trigger).

    The alarm's fade-in loop calls this when it detects the user silencing the
    alarm mid-ramp: the snooze countdown then starts at the button press
    instead of up to ``ARMED_INTERVAL`` * ``PAUSE_DEBOUNCE_READS`` later on
    the monitor's polls. Returns True if a session is now snoozing.
    """
    try:
        data = _get_status_snapshot(force_refresh=True)
        if not data.get("active"):
            return False
        window_end = data.get("window_end") or 0
        if window_end and time.time() >= window_end:
            return False
        if data.get("state") == "snoozing":
            return True
        _arm_snooze(token or get_access_token() or refresh_access_token(), data, source="fade_trigger")
        return True
    except Exception:
        logger.exception("Error triggering snooze")
        return False


def maybe_resume_snooze_monitor() -> bool:
    """Re-spawn the monitor on app startup if a session survived a restart.

    Returns True if a monitor was (re)started.
    """
    try:
        data = _get_status_snapshot(force_refresh=True)
        if not data.get("active"):
            return False
        window_end = data.get("window_end") or 0
        if window_end and time.time() >= window_end:
            _write_status({"active": False})
            return False
        _spawn_monitor()
        logger.info("💤 Snooze session restored after restart")
        return True
    except Exception:
        logger.exception("Error restoring snooze session")
        return False


def _spawn_monitor() -> None:
    """Start a fresh monitor thread, invalidating any previous one via epoch."""
    global _monitor_epoch, _monitor_thread
    with _MONITOR_LOCK:
        _monitor_epoch += 1
        epoch = _monitor_epoch
        thread = Thread(target=_monitor_snooze, args=(epoch,), daemon=True)
        _monitor_thread = thread
        thread.start()


# ---------------------------------------------------------------------------
# Playback context matching
# ---------------------------------------------------------------------------

def _device_matches(playback: Dict[str, Any], device_id: Optional[str], device_name: str) -> bool:
    """True if the active playback device is our alarm device (by id or name)."""
    device = playback.get("device") or {}
    if device_id and device.get("id") == device_id:
        return True
    if device_name and device.get("name") == device_name:
        return True
    # If we never resolved a device id and none configured, don't block matching.
    return not device_id and not device_name


def _classify_armed(
    playback: Optional[Dict[str, Any]],
    device_id: Optional[str],
    device_name: str,
    playlist_uri: str,
    mute_threshold: int = MUTE_THRESHOLD,
) -> str:
    """Classify the playback state while armed.

    Returns one of:
        "playing"  -> our alarm is still playing audibly (keep watching)
        "paused"   -> alarm silenced via pause OR mute (snooze candidate)
        "takeover" -> something else is actively playing (dismiss)

    "paused" is the SILENCED verdict: it covers both a real pause
    (``is_playing == False``) and a mute (the alarm keeps "playing" but the
    device volume dropped to ``mute_threshold`` or below — what the hardware
    snooze button does). Mute is only counted on *our* alarm device/context;
    a muted foreign device is a takeover, not a snooze.
    """
    if playback is None:
        # Nothing active anywhere -> alarm device most likely paused/dropped off.
        return "paused"
    if not bool(playback.get("is_playing")):
        return "paused"
    if _device_matches(playback, device_id, device_name) and _context_matches(playback, playlist_uri):
        # Our alarm is "playing" — but a mute (volume <= threshold) is a snooze.
        # volume_percent is None on devices without volume control: treat as
        # "no mute signal" and fall back to pause-only detection (don't snooze).
        volume = (playback.get("device") or {}).get("volume_percent")
        if volume is not None and volume <= mute_threshold:
            return "paused"
        return "playing"
    return "takeover"


def _context_matches(playback: Dict[str, Any], playlist_uri: str) -> bool:
    """True if the active context/track corresponds to the alarm playlist.

    Mirrors the matching logic in ``_verify_playback_state`` (src/api/spotify.py):
    a context URI matches playlists/albums, a ``spotify:track:`` URI matches the
    played item. An empty alarm URI matches any playing item.
    """
    if not playlist_uri:
        return bool((playback.get("item") or {}).get("uri"))
    if playlist_uri.startswith("spotify:track:"):
        return (playback.get("item") or {}).get("uri") == playlist_uri
    context_uri = (playback.get("context") or {}).get("uri")
    return bool(context_uri) and context_uri == playlist_uri


# ---------------------------------------------------------------------------
# Monitor thread
# ---------------------------------------------------------------------------

def _monitor_snooze(epoch: int) -> None:
    """Background loop driving the armed/snoozing state machine."""
    logger.info("💤 Snooze monitor thread started (epoch=%d)", epoch)
    pause_streak = 0
    seen_playing = False
    try:
        while True:
            # A newer session/monitor superseded this one.
            with _MONITOR_LOCK:
                if epoch != _monitor_epoch:
                    logger.info("💤 Snooze monitor superseded (epoch=%d), exiting", epoch)
                    return

            status = _get_status_snapshot(force_refresh=True)
            if not status.get("active", False):
                logger.info("💤 Snooze monitor: session inactive, exiting")
                return

            now = time.time()
            window_end = status.get("window_end") or 0
            if window_end and now >= window_end:
                logger.info("💤 Snooze window elapsed - ending session")
                stop_snooze_session("window_elapsed")
                return

            token = get_access_token() or refresh_access_token()
            if not token:
                logger.debug("💤 Snooze monitor: no Spotify token, retrying later")
                time.sleep(ARMED_INTERVAL)
                continue

            state = status.get("state", "armed")
            device_id = status.get("device_id")
            device_name = status.get("device_name") or ""
            playlist_uri = status.get("playlist_uri") or ""

            # Read current playback; treat API/network errors as transient.
            try:
                playback = get_current_playback(token)
            except Exception as exc:
                logger.debug("💤 Snooze monitor: playback fetch failed: %s", exc)
                time.sleep(ARMED_INTERVAL)
                continue

            if state == "snoozing":
                resume_at = status.get("resume_at") or 0
                _pb_device = (playback or {}).get("device") or {}
                log_snooze_probe("snoozing_poll", {
                    "is_playing": (playback or {}).get("is_playing"),
                    "volume": _pb_device.get("volume_percent"),
                    "device": _pb_device.get("name"),
                    "context_uri": ((playback or {}).get("context") or {}).get("uri"),
                    "resume_in_s": round(resume_at - now, 1),
                })
                if now >= resume_at:
                    _do_resume(token, status)
                    pause_streak = 0
                    continue
                # Waiting to resume: detect manual resume / takeover. Keyed on
                # is_playing only (NOT volume): we paused on arm, so during snooze
                # is_playing is False and a stale volume_percent must not trigger a
                # spurious re-arm/dismiss. Only a real play action matters here.
                if playback and bool(playback.get("is_playing")):
                    if _device_matches(playback, device_id, device_name) and _context_matches(playback, playlist_uri):
                        logger.info("💤 Manual resume detected during snooze - re-arming")
                        log_snooze_probe("manual_resume_rearm", {"resume_in_s": round(resume_at - now, 1)})
                        _set_state_armed(status)
                        pause_streak = 0
                    else:
                        logger.info("💤 Other playback during snooze - dismissing snooze session")
                        stop_snooze_session("other_playback_during_snooze")
                        return
                # else: still paused, keep waiting.
                interval = min(SNOOZING_INTERVAL, max(5, int(resume_at - now)))
                time.sleep(interval)
                continue

            # state == "armed": watch for the alarm being silenced (pause or mute).
            verdict = _classify_armed(playback, device_id, device_name, playlist_uri)
            # Settle phase: right after the alarm starts, /me/player reads are often
            # stale (device audibly playing but API reports paused/204/old device).
            # Only act on a silence/takeover once we've confirmed the alarm playing,
            # or the settle window has elapsed (so a very fast mute still snoozes).
            alarm_started_at = status.get("alarm_started_at") or 0
            settled = (
                seen_playing
                or alarm_started_at <= 0
                or (now - alarm_started_at) >= STARTUP_SETTLE_SECONDS
            )
            _device = (playback or {}).get("device") or {}
            logger.info(
                "💤 Snooze poll: verdict=%s is_playing=%s volume=%s device=%s settled=%s seen_playing=%s",
                verdict, (playback or {}).get("is_playing"),
                _device.get("volume_percent"), _device.get("name"), settled, seen_playing,
            )
            log_snooze_probe("armed_poll", {
                "verdict": verdict,
                "is_playing": (playback or {}).get("is_playing"),
                "volume": _device.get("volume_percent"),
                "device": _device.get("name"),
                "context_uri": ((playback or {}).get("context") or {}).get("uri"),
                "settled": settled,
                "seen_playing": seen_playing,
                "pause_streak": pause_streak,
            })
            if verdict == "playing":
                seen_playing = True
                pause_streak = 0
            elif not settled:
                # Still settling after alarm start: ignore stale pause/mute/takeover.
                pause_streak = 0
            elif verdict == "takeover":
                logger.info("💤 Foreign playback detected (device/context changed) - dismissing snooze")
                stop_snooze_session("takeover")
                return
            else:  # "paused" – silenced via pause or mute
                pause_streak += 1
                if pause_streak >= PAUSE_DEBOUNCE_READS:
                    _arm_snooze(token, status)
                    pause_streak = 0

            time.sleep(ARMED_INTERVAL)
    except Exception:
        logger.exception("Error in snooze monitor")
    finally:
        logger.info("💤 Snooze monitor thread exiting (epoch=%d)", epoch)


def _arm_snooze(token: Optional[str], status: Dict[str, Any], source: str = "monitor") -> None:
    """Transition armed -> snoozing, pause playback, and schedule the next resume.

    Writes the snoozing status first (authoritative) so the resume still fires
    even if the pause request fails. Then actively pauses playback: the hardware
    snooze button mutes rather than pauses, so without this the stream would keep
    churning silently through the playlist for the whole snooze window. Pausing an
    already-paused stream (legacy pause button) is a harmless no-op.
    """
    # The fade-loop trigger and the monitor's own debounce can race on the
    # same button press: re-check the live state so the loser no-ops instead
    # of pausing twice and pushing resume_at later (or resurrecting a session
    # that was dismissed in the meantime).
    current = _get_status_snapshot(force_refresh=True)
    if not current.get("active") or current.get("state") == "snoozing":
        return

    snooze_minutes = int(status.get("snooze_minutes", 9) or 9)
    resume_at = time.time() + snooze_minutes * 60
    new_status = dict(status)
    new_status["state"] = "snoozing"
    new_status["resume_at"] = resume_at
    _write_status(new_status)
    logger.info("💤 Silence detected (pause/mute) - snoozing for %d min", snooze_minutes)

    device_id = status.get("device_id")
    pause_ok: Optional[bool] = None
    if device_id and token:
        try:
            pause_ok = bool(stop_playback(token, device_id))
        except Exception as exc:
            pause_ok = False
            logger.debug("💤 Snooze arm: pause request failed: %s", exc)
    elif device_id:
        logger.warning("💤 Snooze arm: no token - stream stays un-paused (muted playback keeps running)")
    log_snooze_probe("snoozed", {
        "source": source,
        "snooze_minutes": snooze_minutes,
        "resume_at_local": _local_iso(resume_at),
        "snooze_count": int(status.get("snooze_count", 0)),
        "pause_ok": pause_ok,
    })


def _local_iso(ts: float) -> str:
    """Epoch seconds -> local ISO time for probe events."""
    return datetime.datetime.fromtimestamp(ts).astimezone().isoformat()


def _set_state_armed(status: Dict[str, Any]) -> None:
    """Transition back to armed (manual resume) without bumping snooze_count."""
    new_status = dict(status)
    new_status["state"] = "armed"
    new_status["resume_at"] = 0
    _write_status(new_status)


def _do_resume(token: str, status: Dict[str, Any]) -> None:
    """Resume alarm playback at full alarm volume (no fade-in) and re-arm.

    Explicitly raises the volume *before* starting playback to override a mute
    (the hardware snooze button mutes the device). start_playback also re-asserts
    the volume internally, but only after its playback verification succeeds — the
    pre-call guarantees the device is audible even if that verification degrades.
    """
    device_id = status.get("device_id")
    playlist_uri = status.get("playlist_uri") or ""
    # `or 50` (not `or 0`): a missing/zero stored volume must never resume muted.
    volume = int(status.get("volume") or 50)
    shuffle = bool(status.get("shuffle", False))

    started = False
    if device_id:
        try:
            set_volume(token, volume, device_id)  # override the mute before play
        except Exception as exc:
            logger.debug("💤 Snooze resume: pre-volume set failed: %s", exc)
        try:
            started = start_playback(token, device_id, playlist_uri, volume_percent=volume, shuffle=shuffle)
        except Exception as exc:
            logger.warning("💤 Snooze resume failed: %s", exc)

    new_status = dict(status)
    new_status["state"] = "armed"
    new_status["resume_at"] = 0
    new_status["snooze_count"] = int(status.get("snooze_count", 0)) + 1
    _write_status(new_status)
    resume_at = status.get("resume_at") or 0
    log_snooze_probe("resumed", {
        "volume": volume,
        "started": bool(started),
        "snooze_count": new_status["snooze_count"],
        "late_s": round(time.time() - resume_at, 1) if resume_at else None,
    })

    if started:
        logger.info("💤 Snooze resume: playback restarted at %d%% (count=%d)", volume, new_status["snooze_count"])
    else:
        logger.warning("💤 Snooze resume could not start playback (device=%s)", status.get("device_name"))
