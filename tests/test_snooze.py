"""Tests for the snooze-on-pause feature.

Covers the core state machine (src/core/snooze.py), the snooze config schema
fields, the /api/snooze/stop route and the snooze surface on the dashboard
status contract.
"""

import time

import pytest

import src.core.snooze as snooze
from src.config_schema import SpotiPiConfig


@pytest.fixture
def temp_status(tmp_path, monkeypatch):
    """Point the snooze status file at a temp path and reset the cache."""
    path = tmp_path / "snooze_status.json"
    monkeypatch.setattr(snooze, "STATUS_PATH", str(path))
    snooze._STATUS_CACHE = None
    snooze._STATUS_CACHE_TS = 0.0
    yield path
    snooze._STATUS_CACHE = None
    snooze._STATUS_CACHE_TS = 0.0


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

def test_device_matches_by_id_and_name():
    pb = {"device": {"id": "dev1", "name": "Forte"}}
    assert snooze._device_matches(pb, "dev1", "Other")
    assert snooze._device_matches(pb, "wrong", "Forte")
    assert not snooze._device_matches(pb, "wrong", "Nope")


def test_context_matches_playlist_track_and_empty():
    playlist_pb = {"context": {"uri": "spotify:playlist:p"}, "item": {"uri": "spotify:track:x"}}
    assert snooze._context_matches(playlist_pb, "spotify:playlist:p")
    assert not snooze._context_matches(playlist_pb, "spotify:playlist:other")

    track_pb = {"item": {"uri": "spotify:track:x"}}
    assert snooze._context_matches(track_pb, "spotify:track:x")
    assert not snooze._context_matches(track_pb, "spotify:track:y")

    # Empty alarm URI matches any playing item
    assert snooze._context_matches({"item": {"uri": "spotify:track:z"}}, "")
    assert not snooze._context_matches({}, "")


def test_classify_armed():
    playing = {"is_playing": True, "device": {"id": "dev1", "volume_percent": 50}, "context": {"uri": "spotify:playlist:p"}}
    paused = {"is_playing": False, "device": {"id": "dev1"}}
    foreign = {"is_playing": True, "device": {"id": "other", "volume_percent": 50}, "context": {"uri": "spotify:playlist:zzz"}}
    # Hardware snooze button mutes (volume -> 0) without pausing: still "our" alarm.
    muted_ours = {"is_playing": True, "device": {"id": "dev1", "volume_percent": 0}, "context": {"uri": "spotify:playlist:p"}}
    muted_low = {"is_playing": True, "device": {"id": "dev1", "volume_percent": 2}, "context": {"uri": "spotify:playlist:p"}}
    # A muted FOREIGN device is a takeover, not a snooze.
    muted_foreign = {"is_playing": True, "device": {"id": "other", "volume_percent": 0}, "context": {"uri": "spotify:playlist:zzz"}}

    assert snooze._classify_armed(playing, "dev1", "Forte", "spotify:playlist:p") == "playing"
    assert snooze._classify_armed(paused, "dev1", "Forte", "spotify:playlist:p") == "paused"
    assert snooze._classify_armed(None, "dev1", "Forte", "spotify:playlist:p") == "paused"
    assert snooze._classify_armed(foreign, "dev1", "Forte", "spotify:playlist:p") == "takeover"
    assert snooze._classify_armed(muted_ours, "dev1", "Forte", "spotify:playlist:p") == "paused"
    assert snooze._classify_armed(muted_low, "dev1", "Forte", "spotify:playlist:p") == "paused"
    assert snooze._classify_armed(muted_foreign, "dev1", "Forte", "spotify:playlist:p") == "takeover"


def test_classify_armed_mute_threshold_boundary():
    at = {"is_playing": True, "device": {"id": "dev1", "volume_percent": snooze.MUTE_THRESHOLD}, "context": {"uri": "spotify:playlist:p"}}
    above = {"is_playing": True, "device": {"id": "dev1", "volume_percent": snooze.MUTE_THRESHOLD + 1}, "context": {"uri": "spotify:playlist:p"}}
    assert snooze._classify_armed(at, "dev1", "Forte", "spotify:playlist:p") == "paused"
    assert snooze._classify_armed(above, "dev1", "Forte", "spotify:playlist:p") == "playing"
    # Threshold is overridable (used by the module default in production).
    low = {"is_playing": True, "device": {"id": "dev1", "volume_percent": 5}, "context": {"uri": "spotify:playlist:p"}}
    assert snooze._classify_armed(low, "dev1", "Forte", "spotify:playlist:p", mute_threshold=10) == "paused"


def test_classify_armed_none_volume_falls_back_to_pause_only():
    # Device without volume control (volume_percent None) -> no mute signal.
    playing_no_vol = {"is_playing": True, "device": {"id": "dev1"}, "context": {"uri": "spotify:playlist:p"}}
    paused_no_vol = {"is_playing": False, "device": {"id": "dev1"}}
    assert snooze._classify_armed(playing_no_vol, "dev1", "Forte", "spotify:playlist:p") == "playing"
    assert snooze._classify_armed(paused_no_vol, "dev1", "Forte", "spotify:playlist:p") == "paused"


# ---------------------------------------------------------------------------
# Status accessor
# ---------------------------------------------------------------------------

def test_get_snooze_status_inactive(temp_status):
    assert snooze.get_snooze_status() == {"active": False}


def test_get_snooze_status_window_elapsed(temp_status):
    snooze._write_status({
        "active": True,
        "state": "armed",
        "window_end": time.time() - 1,
    })
    assert snooze.get_snooze_status() == {"active": False}


def test_get_snooze_status_snoozing_countdown(temp_status):
    now = time.time()
    snooze._write_status({
        "active": True,
        "state": "snoozing",
        "window_end": now + 3600,
        "resume_at": now + 300,
        "snooze_count": 2,
        "snooze_minutes": 9,
        "device_name": "Forte",
    })
    status = snooze.get_snooze_status()
    assert status["active"] is True
    assert status["state"] == "snoozing"
    assert status["snooze_count"] == 2
    assert 290 <= status["resume_in_seconds"] <= 300
    assert status["device_name"] == "Forte"


# ---------------------------------------------------------------------------
# Session lifecycle + transition writers
# ---------------------------------------------------------------------------

def test_start_snooze_session_writes_status(temp_status, monkeypatch):
    monkeypatch.setattr(snooze, "_spawn_monitor", lambda: None)
    ok = snooze.start_snooze_session(
        device_id="dev1",
        device_name="Forte",
        playlist_uri="spotify:playlist:p",
        volume=20,
        shuffle=True,
        window_minutes=120,
        snooze_minutes=9,
    )
    assert ok is True
    status = snooze.get_snooze_status()
    assert status["active"] is True
    assert status["state"] == "armed"
    assert status["snooze_minutes"] == 9
    assert status["device_name"] == "Forte"
    assert status["window_remaining_seconds"] > 7000  # ~120 min


def test_start_snooze_session_rejects_bad_durations(temp_status, monkeypatch):
    monkeypatch.setattr(snooze, "_spawn_monitor", lambda: None)
    assert snooze.start_snooze_session(
        device_id="d", device_name="F", playlist_uri="", volume=20,
        window_minutes=0, snooze_minutes=9,
    ) is False


def test_stop_snooze_session(temp_status, monkeypatch):
    monkeypatch.setattr(snooze, "_spawn_monitor", lambda: None)
    snooze.start_snooze_session(
        device_id="dev1", device_name="Forte", playlist_uri="spotify:playlist:p", volume=20,
    )
    assert snooze.get_snooze_status()["active"] is True
    assert snooze.stop_snooze_session() is True
    assert snooze.get_snooze_status() == {"active": False}


def test_arm_snooze_sets_resume_at(temp_status):
    base = {
        "active": True, "state": "armed", "window_end": time.time() + 3600,
        "snooze_minutes": 9, "snooze_count": 0,
    }
    snooze._write_status(base)
    snooze._arm_snooze("tok", base)  # no device_id -> no pause call
    status = snooze.get_snooze_status()
    assert status["state"] == "snoozing"
    assert 530 <= status["resume_in_seconds"] <= 540  # ~9 min


def test_arm_snooze_pauses_playback(temp_status, monkeypatch):
    paused_calls = []
    monkeypatch.setattr(
        snooze, "stop_playback",
        lambda token, device_id=None: paused_calls.append((token, device_id)) or True,
    )
    base = {
        "active": True, "state": "armed", "window_end": time.time() + 3600,
        "device_id": "dev1", "snooze_minutes": 9, "snooze_count": 0,
    }
    snooze._write_status(base)
    snooze._arm_snooze("tok", base)
    # Arming a snooze actively pauses the (possibly only muted) stream.
    assert paused_calls == [("tok", "dev1")]
    assert snooze.get_snooze_status()["state"] == "snoozing"


def test_arm_snooze_noop_when_already_snoozing(temp_status, monkeypatch):
    # Fade trigger and monitor debounce can race on the same press: the loser
    # must not pause again or push resume_at later.
    monkeypatch.setattr(
        snooze, "stop_playback",
        lambda *args, **kwargs: pytest.fail("must not pause again while snoozing"),
    )
    now = time.time()
    live = {
        "active": True, "state": "snoozing", "window_end": now + 3600,
        "device_id": "dev1", "resume_at": now + 120, "snooze_minutes": 9,
    }
    snooze._write_status(live)
    stale_armed = dict(live, state="armed", resume_at=0)
    snooze._arm_snooze("tok", stale_armed)
    assert snooze._read_status_from_disk()["resume_at"] == pytest.approx(now + 120, abs=1)


def test_trigger_snooze_arms_immediately(temp_status, monkeypatch):
    paused_calls = []
    monkeypatch.setattr(
        snooze, "stop_playback",
        lambda token, device_id=None: paused_calls.append((token, device_id)) or True,
    )
    now = time.time()
    snooze._write_status({
        "active": True, "state": "armed", "window_end": now + 3600,
        "device_id": "dev1", "device_name": "Forte", "snooze_minutes": 9, "snooze_count": 0,
    })
    assert snooze.trigger_snooze("tok") is True
    status = snooze.get_snooze_status()
    assert status["state"] == "snoozing"
    assert 530 <= status["resume_in_seconds"] <= 540  # ~9 min from the press
    assert paused_calls == [("tok", "dev1")]


def test_trigger_snooze_without_session(temp_status):
    assert snooze.trigger_snooze("tok") is False


def test_trigger_snooze_window_elapsed(temp_status):
    snooze._write_status({
        "active": True, "state": "armed", "window_end": time.time() - 1,
    })
    assert snooze.trigger_snooze("tok") is False


def test_trigger_snooze_already_snoozing_keeps_resume_at(temp_status, monkeypatch):
    monkeypatch.setattr(
        snooze, "stop_playback",
        lambda *args, **kwargs: pytest.fail("must not pause again while snoozing"),
    )
    now = time.time()
    snooze._write_status({
        "active": True, "state": "snoozing", "window_end": now + 3600,
        "resume_at": now + 120, "snooze_minutes": 9,
    })
    assert snooze.trigger_snooze("tok") is True
    assert snooze._read_status_from_disk()["resume_at"] == pytest.approx(now + 120, abs=1)


def test_do_resume_full_volume_and_rearm(temp_status, monkeypatch):
    calls = {}
    sequence = []

    def fake_set_volume(token, volume_percent, device_id=None):
        sequence.append("set_volume")
        calls["volume"] = (token, volume_percent, device_id)
        return True

    def fake_start(token, device_id, playlist_uri, volume_percent=50, shuffle=False):
        sequence.append("start_playback")
        calls["args"] = (token, device_id, playlist_uri, volume_percent, shuffle)
        return True

    monkeypatch.setattr(snooze, "set_volume", fake_set_volume)
    monkeypatch.setattr(snooze, "start_playback", fake_start)
    status = {
        "active": True, "state": "snoozing", "window_end": time.time() + 3600,
        "resume_at": time.time() - 1, "device_id": "dev1",
        "playlist_uri": "spotify:playlist:p", "volume": 20, "shuffle": False,
        "snooze_minutes": 9, "snooze_count": 1,
    }
    snooze._write_status(status)
    snooze._do_resume("tok", status)

    # Resume must override the mute (raise volume) BEFORE starting playback,
    # and use the full alarm volume (no fade-in).
    assert calls["volume"] == ("tok", 20, "dev1")
    assert calls["args"] == ("tok", "dev1", "spotify:playlist:p", 20, False)
    assert sequence == ["set_volume", "start_playback"]
    after = snooze.get_snooze_status()
    assert after["state"] == "armed"
    assert after["snooze_count"] == 2


def test_do_resume_overrides_mute_even_if_start_fails(temp_status, monkeypatch):
    volume_calls = []
    monkeypatch.setattr(
        snooze, "set_volume",
        lambda token, volume_percent, device_id=None: volume_calls.append((token, volume_percent, device_id)) or True,
    )
    monkeypatch.setattr(snooze, "start_playback", lambda *a, **k: False)
    status = {
        "active": True, "state": "snoozing", "window_end": time.time() + 3600,
        "resume_at": time.time() - 1, "device_id": "dev1",
        "playlist_uri": "spotify:playlist:p", "volume": 20, "shuffle": False,
        "snooze_minutes": 9, "snooze_count": 0,
    }
    snooze._write_status(status)
    snooze._do_resume("tok", status)

    # Volume is raised even when playback fails to start, and the session
    # re-arms (anti-stuck) so the next poll retries instead of hanging snoozing.
    assert volume_calls == [("tok", 20, "dev1")]
    after = snooze.get_snooze_status()
    assert after["state"] == "armed"
    assert after["snooze_count"] == 1


def test_maybe_resume_monitor(temp_status, monkeypatch):
    spawned = {"count": 0}
    monkeypatch.setattr(snooze, "_spawn_monitor", lambda: spawned.__setitem__("count", spawned["count"] + 1))

    # No active session -> no spawn
    assert snooze.maybe_resume_snooze_monitor() is False
    assert spawned["count"] == 0

    # Active within window -> spawn
    snooze._write_status({"active": True, "state": "armed", "window_end": time.time() + 3600})
    assert snooze.maybe_resume_snooze_monitor() is True
    assert spawned["count"] == 1

    # Active but window elapsed -> cleaned up, no spawn
    snooze._write_status({"active": True, "state": "armed", "window_end": time.time() - 1})
    assert snooze.maybe_resume_snooze_monitor() is False
    assert snooze.get_snooze_status() == {"active": False}


# ---------------------------------------------------------------------------
# Monitor loop (driven deterministically)
# ---------------------------------------------------------------------------

def _drive_monitor(monkeypatch, temp_status, initial_status, playback_script):
    """Run _monitor_snooze in-thread with a scripted playback sequence.

    Captures every status write so transitions can be asserted. The script is
    consumed one entry per loop iteration; when exhausted the session is
    stopped so the loop terminates.
    """
    monkeypatch.setattr(snooze.time, "sleep", lambda *_: None)
    monkeypatch.setattr(snooze, "get_access_token", lambda: "tok")
    monkeypatch.setattr(snooze, "refresh_access_token", lambda: "tok")
    monkeypatch.setattr(snooze, "start_playback", lambda *a, **k: True)
    monkeypatch.setattr(snooze, "set_volume", lambda *a, **k: True)
    monkeypatch.setattr(snooze, "stop_playback", lambda *a, **k: True)

    writes = []
    original_write = snooze._write_status

    def spy_write(data):
        writes.append(dict(data))
        original_write(data)

    monkeypatch.setattr(snooze, "_write_status", spy_write)

    state = {"i": 0}

    def fake_playback(token):
        i = state["i"]
        state["i"] += 1
        if i >= len(playback_script):
            snooze.stop_snooze_session()
            return None
        if i > 50:  # safety against runaway loops
            raise AssertionError("monitor loop did not terminate")
        return playback_script[i]

    monkeypatch.setattr(snooze, "get_current_playback", fake_playback)

    snooze._monitor_epoch = 1
    original_write(initial_status)
    writes.clear()
    snooze._monitor_snooze(1)
    return writes


def test_monitor_arms_after_debounce(temp_status, monkeypatch):
    playing = {"is_playing": True, "device": {"id": "dev1"}, "context": {"uri": "spotify:playlist:p"}}
    paused = {"is_playing": False, "device": {"id": "dev1"}}
    initial = {
        "active": True, "state": "armed", "window_end": time.time() + 3600,
        "device_id": "dev1", "device_name": "Forte", "playlist_uri": "spotify:playlist:p",
        "volume": 20, "shuffle": False, "snooze_minutes": 9, "snooze_count": 0,
    }
    # A lone pause (then playing) must NOT arm; two consecutive pauses must arm.
    script = [paused, playing, paused, paused]
    writes = _drive_monitor(monkeypatch, temp_status, initial, script)

    snoozing_writes = [w for w in writes if w.get("state") == "snoozing"]
    assert len(snoozing_writes) == 1


def test_monitor_arms_on_mute(temp_status, monkeypatch):
    # Hardware snooze button mutes (volume 0) without flipping is_playing.
    muted = {"is_playing": True, "device": {"id": "dev1", "volume_percent": 0}, "context": {"uri": "spotify:playlist:p"}}
    initial = {
        "active": True, "state": "armed", "window_end": time.time() + 3600,
        "device_id": "dev1", "device_name": "Forte", "playlist_uri": "spotify:playlist:p",
        "volume": 20, "shuffle": False, "snooze_minutes": 9, "snooze_count": 0,
    }
    # Two consecutive muted-but-playing reads must arm the snooze (debounce).
    writes = _drive_monitor(monkeypatch, temp_status, initial, [muted, muted])
    snoozing_writes = [w for w in writes if w.get("state") == "snoozing"]
    assert len(snoozing_writes) == 1


def test_monitor_mute_debounce_single_read_no_arm(temp_status, monkeypatch):
    muted = {"is_playing": True, "device": {"id": "dev1", "volume_percent": 0}, "context": {"uri": "spotify:playlist:p"}}
    full = {"is_playing": True, "device": {"id": "dev1", "volume_percent": 50}, "context": {"uri": "spotify:playlist:p"}}
    initial = {
        "active": True, "state": "armed", "window_end": time.time() + 3600,
        "device_id": "dev1", "device_name": "Forte", "playlist_uri": "spotify:playlist:p",
        "volume": 20, "shuffle": False, "snooze_minutes": 9, "snooze_count": 0,
    }
    # A lone mute (then full volume) must NOT arm; only two consecutive mutes do.
    writes = _drive_monitor(monkeypatch, temp_status, initial, [muted, full, muted, muted])
    snoozing_writes = [w for w in writes if w.get("state") == "snoozing"]
    assert len(snoozing_writes) == 1


def test_monitor_resumes_when_due(temp_status, monkeypatch):
    initial = {
        "active": True, "state": "snoozing", "window_end": time.time() + 3600,
        "resume_at": time.time() - 1, "device_id": "dev1", "device_name": "Forte",
        "playlist_uri": "spotify:playlist:p", "volume": 20, "shuffle": False,
        "snooze_minutes": 9, "snooze_count": 0,
    }
    # First iteration is due to resume; subsequent reads end the session.
    writes = _drive_monitor(monkeypatch, temp_status, initial, [None])
    rearmed = [w for w in writes if w.get("state") == "armed" and w.get("snooze_count") == 1]
    assert len(rearmed) == 1


def test_monitor_dismisses_on_takeover(temp_status, monkeypatch):
    foreign = {"is_playing": True, "device": {"id": "other"}, "context": {"uri": "spotify:playlist:zzz"}}
    initial = {
        "active": True, "state": "armed", "window_end": time.time() + 3600,
        "device_id": "dev1", "device_name": "Forte", "playlist_uri": "spotify:playlist:p",
        "volume": 20, "shuffle": False, "snooze_minutes": 9, "snooze_count": 0,
    }
    writes = _drive_monitor(monkeypatch, temp_status, initial, [foreign])
    # Takeover -> session ended, never snoozed
    assert not any(w.get("state") == "snoozing" for w in writes)
    assert snooze.get_snooze_status() == {"active": False}


def _armed_initial(**overrides):
    base = {
        "active": True, "state": "armed", "window_end": time.time() + 3600,
        "device_id": "dev1", "device_name": "Forte", "playlist_uri": "spotify:playlist:p",
        "volume": 20, "shuffle": False, "snooze_minutes": 9, "snooze_count": 0,
    }
    base.update(overrides)
    return base


def test_monitor_ignores_stale_startup_silence(temp_status, monkeypatch):
    # Right after the alarm starts, /me/player can report stale pause/204 reads
    # while the device is audibly playing. Two such reads, before we ever saw the
    # alarm playing and within the settle window, must NOT arm a snooze.
    paused = {"is_playing": False, "device": {"id": "dev1"}}
    initial = _armed_initial(alarm_started_at=time.time())
    writes = _drive_monitor(monkeypatch, temp_status, initial, [paused, paused])
    assert not any(w.get("state") == "snoozing" for w in writes)


def test_monitor_arms_after_confirmed_play(temp_status, monkeypatch):
    # Once the monitor has confirmed the alarm playing, a subsequent mute counts
    # even inside the settle window.
    playing = {"is_playing": True, "device": {"id": "dev1", "volume_percent": 45}, "context": {"uri": "spotify:playlist:p"}}
    muted = {"is_playing": True, "device": {"id": "dev1", "volume_percent": 0}, "context": {"uri": "spotify:playlist:p"}}
    initial = _armed_initial(alarm_started_at=time.time())
    writes = _drive_monitor(monkeypatch, temp_status, initial, [playing, muted, muted])
    assert len([w for w in writes if w.get("state") == "snoozing"]) == 1


def test_monitor_arms_after_settle_window_without_confirmed_play(temp_status, monkeypatch):
    # Fallback: even if we never caught a clean "playing" read, once the settle
    # window has elapsed a sustained silence still snoozes (fast mute case).
    paused = {"is_playing": False, "device": {"id": "dev1"}}
    initial = _armed_initial(alarm_started_at=time.time() - (snooze.STARTUP_SETTLE_SECONDS + 5))
    writes = _drive_monitor(monkeypatch, temp_status, initial, [paused, paused])
    assert len([w for w in writes if w.get("state") == "snoozing"]) == 1


# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------

def test_snooze_config_defaults():
    c = SpotiPiConfig()
    assert c.snooze_enabled is True
    assert c.snooze_minutes == 9
    assert c.snooze_window_minutes == 120


def test_snooze_config_ranges():
    with pytest.raises(Exception):
        SpotiPiConfig(snooze_minutes=0)
    with pytest.raises(Exception):
        SpotiPiConfig(snooze_minutes=61)
    with pytest.raises(Exception):
        SpotiPiConfig(snooze_window_minutes=0)
    with pytest.raises(Exception):
        SpotiPiConfig(snooze_window_minutes=481)


# ---------------------------------------------------------------------------
# Routes + dashboard contract
# ---------------------------------------------------------------------------

def test_snooze_stop_no_active(client, monkeypatch):
    import src.services.snooze_service as svc
    monkeypatch.setattr(svc, "get_snooze_status", lambda: {"active": False})
    resp = client.post("/api/snooze/stop", headers={"Accept": "application/json"})
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False
    assert data["error_code"] == "NO_ACTIVE_SNOOZE"


def test_snooze_stop_active(client, monkeypatch):
    import src.services.snooze_service as svc
    stopped = {}
    monkeypatch.setattr(svc, "get_snooze_status", lambda: {"active": True, "state": "snoozing"})
    monkeypatch.setattr(svc, "stop_snooze_session", lambda: stopped.setdefault("done", True) or True)
    resp = client.post("/api/snooze/stop", headers={"Accept": "application/json"})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert stopped.get("done") is True


def test_dashboard_status_includes_snooze(client):
    resp = client.get("/api/dashboard/status")
    assert resp.status_code in (200, 202, 503)
    payload = resp.get_json()["data"]
    assert "snooze" in payload
    assert isinstance(payload["snooze"], dict)
    assert "snooze_enabled" in payload["alarm"]


def test_save_alarm_persists_snooze_toggle(client):
    resp = client.post("/save_alarm", data={
        "time": "07:30",
        "enabled": "true",
        "alarm_volume": "40",
        "snooze_enabled": "off",
    })
    data = resp.get_json()
    assert data["success"] is True
    assert data["data"]["snooze_enabled"] is False
