#!/usr/bin/env python3
"""
🚨 Alarm Execution Logic for SpotiPi
Handles alarm triggering, weekday scheduling, and playback management with:
- Precise time-based triggering
- Weekday scheduling support  
- Volume fade-in functionality
- Automatic alarm disable after triggering
- Comprehensive error handling and logging
"""

import datetime
import os
import time
from typing import List, Optional, Tuple

from ..api.spotify import (get_access_token, get_current_playback,
                           get_device_id, set_volume, start_playback)
from ..config import load_config
from ..constants import ALARM_TRIGGER_WINDOW_MINUTES
from ..utils.logger import setup_logger
from ..utils.thread_safety import config_transaction
from ..utils.timezone import get_local_timezone
from .alarm_logging import AlarmProbeContext, log_alarm_probe
from .snooze import (MUTE_THRESHOLD, _device_matches, start_snooze_session,
                     trigger_snooze)

# Get logger for alarm module
logger = setup_logger(__name__)
LOCAL_TZ = get_local_timezone()

# Fade-in silence detection: between volume steps the player is read once; a
# pause or (post-audible) mute on the alarm device counts as the user hitting
# the snooze button. One silenced read holds the ramp (so the next step does
# not un-mute the device again), a second consecutive read confirms it.
FADE_STEP_SECONDS = 5
FADE_SILENCE_CONFIRM_SECONDS = 3
FADE_SILENCE_CONFIRM_READS = 2

def log(message: str) -> None:
    """Log message using centralized logger.

    Args:
        message: Message to log
    """
    logger.info(message)


def _fade_playback_verdict(
    token: str,
    device_id: str,
    device_name: str,
    seen_playing: bool,
    heard_audible: bool,
) -> Tuple[str, bool, bool]:
    """Read the player once mid-fade and classify it.

    Returns ``(verdict, seen_playing, heard_audible)`` with verdict one of:
        "silenced" -> the alarm device was paused (after having been seen
                      playing) or muted (after having been heard audible —
                      the hardware snooze button mutes)
        "active"   -> our alarm is (still) playing
        "unknown"  -> no usable signal (fetch error, empty or foreign read,
                      or a silence for which the latch below hasn't confirmed
                      real playback yet)

    Both silence paths are gated on a latch, because right after playback
    starts /me/player reads are often stale: ``seen_playing`` sticks once the
    device reported ``is_playing`` (until then a paused read is likely the
    pre-start transport state, not a button press), and ``heard_audible``
    sticks once the device reported a volume above ``MUTE_THRESHOLD`` (until
    then a low-volume read may echo the 0% fade preset). Empty/foreign reads
    are ignored (unlike in the armed snooze monitor) because seconds after a
    verified playback start they are almost always stale; the monitor still
    catches real takeovers once the fade is over.
    """
    try:
        playback = get_current_playback(token)
    except Exception as exc:
        logger.debug("Fade silence check failed: %s", exc)
        return "unknown", seen_playing, heard_audible
    if not playback:
        return "unknown", seen_playing, heard_audible
    if not _device_matches(playback, device_id, device_name):
        return "unknown", seen_playing, heard_audible
    if not bool(playback.get("is_playing")):
        if seen_playing:
            return "silenced", seen_playing, heard_audible
        return "unknown", seen_playing, heard_audible
    volume = (playback.get("device") or {}).get("volume_percent")
    if volume is not None and volume > MUTE_THRESHOLD:
        return "active", True, True
    if heard_audible and volume is not None:
        return "silenced", True, heard_audible
    return "active", True, heard_audible


def _run_fade_in(
    token: str,
    device_id: str,
    device_name: str,
    target_volume: int,
    probe: Optional[AlarmProbeContext],
    detect_silence: bool = True,
) -> bool:
    """Ramp the alarm volume in steps, watching for a pause/mute in between.

    Returns True when the user silenced the alarm device mid-fade (pause or
    hardware mute): the ramp stops right away so the next volume step does not
    un-mute the device again, and the caller turns the press into a snooze.

    ``detect_silence`` must only be True while a snooze session is armed:
    aborting the ramp is only safe when the snooze monitor exists to resume
    playback later — without a session the old always-ramp behavior is the
    only recovery path the alarm has.
    """
    if target_volume <= 0:
        return False

    fade_step = max(1, min(5, target_volume))
    volumes: List[int] = list(range(fade_step, target_volume, fade_step))
    volumes.append(target_volume)

    seen_playing = False
    heard_audible = False
    silence_streak = 0
    idx = 0
    while idx < len(volumes):
        if silence_streak:
            time.sleep(FADE_SILENCE_CONFIRM_SECONDS)
        else:
            time.sleep(1 if idx == 0 else FADE_STEP_SECONDS)

        if detect_silence:
            verdict, seen_playing, heard_audible = _fade_playback_verdict(
                token, device_id, device_name, seen_playing, heard_audible
            )
            if verdict == "silenced":
                silence_streak += 1
                if silence_streak >= FADE_SILENCE_CONFIRM_READS:
                    log("💤 Alarm silenced during fade-in - handing over to snooze")
                    return True
                continue  # hold this step and re-read shortly to confirm
            silence_streak = 0

        v = volumes[idx]
        if set_volume(token, v, device_id):
            log(f"🎚️ Volume increased to {v}%")
            log_alarm_probe(
                probe,
                "execute_fade_step",
                extra={"volume": v, "step_index": idx, "total_steps": len(volumes)},
            )
        else:
            log(f"⚠️ Volume set attempt to {v}% - Spotify API refused value")
        idx += 1

    return False

def execute_alarm(
    *,
    force: bool = False,
    probe: Optional[AlarmProbeContext] = None,
    catchup_grace_seconds: float = 0.0,
) -> bool:
    """Main alarm execution function.

    Args:
        force: Skip enable/time window guards (used for manual triggers).

    Returns:
        bool: True if playback was started, False otherwise
    """
    now = datetime.datetime.now(tz=LOCAL_TZ)
    log_alarm_probe(probe, "execute_enter", extra={"force": force})

    try:
        config = load_config()
    except Exception as e:
        logger.error(f"❌ Failed to load config: {e}")
        log_alarm_probe(probe, "execute_config_error", extra={"error": str(e)}, force=True)
        return False

    if probe is not None:
        probe.config_snapshot = dict(config)

    # Helper for conditional debug logging (only when config.debug True)
    def debug(reason: str):
        try:
            if config and config.get("debug"):
                logger.info(f"[ALARM DEBUG] {reason}")
        except Exception:
            pass

    if not config:
        debug("Config is empty or could not be loaded")
        log_alarm_probe(probe, "execute_config_empty", force=True)
        return False

    if not config.get("enabled", False) and not force:
        debug("Alarm not enabled -> skip")
        log_alarm_probe(probe, "execute_disabled", force=True)
        return False

    # From here on we log normal info
    log("🚀 SpotiPi Wakeup started")
    log(f"👤 User: {os.getenv('USER', 'Unknown')}")
    log(f"🏠 Home directory (expanduser): {os.path.expanduser('~')}")
    log("📁 Using centralized config system")

    now_str = now.strftime("%H:%M")
    log(f"⏰ Current time: {now_str}")
    # Log only key fields to reduce noise
    safe_cfg = {
        k: config.get(k)
        for k in ["enabled", "time", "device_name", "playlist_uri", "alarm_volume", "fade_in", "shuffle", "weekdays"]
    }
    safe_cfg["has_cached_device"] = bool(config.get("last_known_devices"))
    log(f"📄 Loaded config (sanitized): {safe_cfg}")

    # Time check
    try:
        time_str = config["time"]
        target_time = datetime.datetime.strptime(time_str, "%H:%M")
    except (ValueError, KeyError) as e:
        if not force:
            logger.error(f"❌ Invalid time format in config: {config.get('time')} - {e}")
            log_alarm_probe(
                probe,
                "execute_invalid_time",
                extra={"configured_time": config.get("time"), "error": str(e)},
                force=True,
            )
            return False
        debug(f"Invalid or missing time '{config.get('time')}' – forcing execution anyway")
        target_time = now

    target_today = now.replace(
        hour=target_time.hour,
        minute=target_time.minute,
        second=0,
        microsecond=0
    )
    diff_minutes = (now - target_today).total_seconds() / 60

    log(f"🎯 Target time: {config.get('time', 'unset')}")
    log(f"📏 Time difference: {diff_minutes:.2f} minutes")
    tolerance_minutes = ALARM_TRIGGER_WINDOW_MINUTES
    catchup_grace_minutes = catchup_grace_seconds / 60.0 if catchup_grace_seconds else 0.0
    log_alarm_probe(
        probe,
        "execute_time_check",
        extra={
            "diff_minutes": diff_minutes,
            "tolerance": tolerance_minutes,
            "force": force,
            "catchup_grace_minutes": catchup_grace_minutes,
        },
    )

    if diff_minutes < 0 and not force:
        # Alarm is still in the future: it is not due yet. Catch-up grace is for
        # MISSED (late) alarms only and must never fire early — an early fire also
        # re-fires until the wall clock passes the scheduled time, because the
        # occurrence dedup cannot record a pre-scheduled execution as done.
        debug(f"Not yet due (diff={diff_minutes:.2f}m) – waiting for the alarm time.")
        log_alarm_probe(
            probe,
            "execute_before_window",
            extra={"diff_minutes": diff_minutes},
            force=True,
        )
        return False

    if diff_minutes > ALARM_TRIGGER_WINDOW_MINUTES and not force:
        if catchup_grace_seconds > 0 and diff_minutes <= catchup_grace_minutes:
            log_alarm_probe(
                probe,
                "execute_catchup_after_window",
                extra={
                    "diff_minutes": diff_minutes,
                    "catchup_grace_minutes": catchup_grace_minutes,
                },
                force=True,
            )
        else:
            debug(f"Not within trigger window (+{ALARM_TRIGGER_WINDOW_MINUTES}m). diff={diff_minutes:.2f}m")
            log_alarm_probe(
                probe,
                "execute_after_window",
                extra={"diff_minutes": diff_minutes},
                force=True,
            )
            return False

    # Weekday check (recurring alarms): only fire on selected days.
    # Empty/None weekdays = single-use alarm, no restriction.
    weekdays = config.get("weekdays")
    if weekdays and not force and now.weekday() not in weekdays:
        debug(f"Not a scheduled weekday (today={now.weekday()}, weekdays={weekdays}) -> skip")
        log_alarm_probe(
            probe,
            "execute_wrong_weekday",
            extra={"weekday": now.weekday(), "weekdays": weekdays},
            force=True,
        )
        return False

    # Start playback
    try:
        token = get_access_token()
        if not token:
            logger.warning("❌ Failed to retrieve token for alarm execution.")
            log_alarm_probe(probe, "execute_token_missing", force=True)
            return False

        device_name = config.get("device_name", "")
        if not device_name:
            logger.warning("❌ No device name configured for the alarm.")
            log_alarm_probe(probe, "execute_device_unset", force=True)
            return False

        device_id = get_device_id(token, device_name)
        if not device_id:
            logger.warning(f"❌ Device '{device_name}' not found.")
            if probe:
                probe.set_device_result("execute", "missing", device_name=device_name)
            log_alarm_probe(
                probe,
                "execute_device_not_found",
                extra={"device_name": device_name},
                force=True,
            )
            return False
        if probe:
            probe.set_device_result("execute", "found", device_name=device_name, device_id=device_id)

        target_volume = config.get("alarm_volume", 50)
        fade_in = config.get("fade_in", False)
        shuffle = config.get("shuffle", False)
        log(f"🎚️ Alarm volume: {target_volume}%, Fade-In: {fade_in}, Shuffle: {shuffle}")

        if not config.get("playlist_uri"):
            debug("No playlist_uri configured; playback may fail")

        initial_volume = 0 if fade_in else target_volume

        preset_success = False
        try:
            preset_success = set_volume(token, initial_volume, device_id)
        except Exception as preset_err:
            logger.warning(f"⚠️ Error presetting volume: {preset_err}")

        if not preset_success:
            logger.warning(f"⚠️ Could not preset volume to {initial_volume}% before playback")
            if fade_in:
                fade_in = False
                initial_volume = target_volume
                logger.warning("⚠️ Fade-in disabled because initial volume could not be set safely")
        log_alarm_probe(
            probe,
            "execute_start_playback",
            extra={
                "device_name": device_name,
                "device_id": device_id,
                "fade_in": fade_in,
                "target_volume": target_volume,
                "initial_volume": initial_volume,
                "shuffle": shuffle,
            },
        )

        if not fade_in:
            start_playback(
                token,
                device_id,
                config.get("playlist_uri", ""),
                volume_percent=target_volume,
                shuffle=shuffle
            )
            log(f"▶️ Playback started directly with {target_volume}% alarm volume")
        else:
            start_playback(
                token,
                device_id,
                config.get("playlist_uri", ""),
                volume_percent=initial_volume,
                shuffle=shuffle
            )
            log("▶️ Playback started at 0% volume (Fade-In active)")

        log("✅ Playback started.")
        log_alarm_probe(probe, "execute_playback_started", extra={"fade_in": fade_in})

        # Snooze-on-pause: while the alarm is playing, silencing the alarm
        # device (a pause, or the Argon/Forte hardware button which mutes)
        # acts as snooze. Armed BEFORE the fade-in so a button press during
        # the ramp already has a session to land in and the monitor's settle
        # window starts at the real playback start.
        snooze_armed = False
        if config.get("snooze_enabled", True):
            try:
                snooze_armed = start_snooze_session(
                    device_id=device_id,
                    device_name=device_name,
                    playlist_uri=config.get("playlist_uri", ""),
                    volume=target_volume,
                    shuffle=shuffle,
                    window_minutes=int(config.get("snooze_window_minutes", 120) or 120),
                    snooze_minutes=int(config.get("snooze_minutes", 9) or 9),
                )
                # Forced probe so arming is visible in the journal (regular 'snooze'
                # logs are suppressed in production – only alarm_probe is captured).
                log_alarm_probe(
                    probe,
                    "execute_snooze_armed",
                    extra={"armed": bool(snooze_armed)},
                    force=True,
                )
            except Exception as snooze_err:
                log(f"⚠️ Could not start snooze session: {snooze_err}")
                log_alarm_probe(
                    probe,
                    "execute_snooze_error",
                    extra={"error": str(snooze_err)},
                    force=True,
                )
        else:
            log_alarm_probe(probe, "execute_snooze_disabled", force=True)

        if fade_in:
            fade_silenced = False
            try:
                # Silence detection only while a snooze session is armed:
                # without one, nothing would ever resume an aborted ramp.
                fade_silenced = _run_fade_in(
                    token, device_id, device_name, target_volume, probe,
                    detect_silence=snooze_armed,
                )
            except Exception as e:
                log(f"❌ Error during fade-in: {e}")
                log_alarm_probe(probe, "execute_fade_error", extra={"error": str(e)}, force=True)
            if fade_silenced:
                # The user hit pause/mute mid-ramp: don't fight it — start the
                # snooze countdown now instead of on the monitor's next poll.
                log_alarm_probe(probe, "execute_fade_snoozed", force=True)
                try:
                    trigger_snooze(token)
                except Exception as snooze_err:
                    log(f"⚠️ Could not trigger snooze after silenced fade: {snooze_err}")

        # Auto-disable single-use alarms only. Recurring alarms (weekdays set)
        # stay enabled so they fire again on the next selected day.
        if not force and not config.get("weekdays"):
            try:
                with config_transaction() as transaction:
                    current_config = transaction.load()
                    current_config["enabled"] = False
                    transaction.save(current_config)
                log("🔄 Alarm automatically disabled after triggering (thread-safe)")
            except Exception as e:
                log(f"❌ Error disabling the alarm: {e}")
                log_alarm_probe(probe, "execute_disable_error", extra={"error": str(e)}, force=True)
        elif config.get("weekdays"):
            log("🔁 Recurring alarm stays enabled for the next selected weekday")

        log_alarm_probe(probe, "execute_complete", extra={"success": True}, force=True)
        return True

    except Exception as e:
        log(f"❌ Unexpected error during alarm execution: {e}")
        logger.exception("Full traceback for alarm error:")
        log_alarm_probe(probe, "execute_exception", extra={"error": str(e)}, force=True)
        return False
    finally:
        debug("Alarm evaluation finished")
