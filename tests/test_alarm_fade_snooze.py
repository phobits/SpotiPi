"""Tests for the snooze-aware fade-in (src/core/alarm.py fade loop).

Silencing the alarm device mid-ramp (a pause, or the hardware snooze button
which mutes) must stop the fade — instead of the next volume step un-muting
the device again — so the caller can turn the press into an immediate snooze.
"""

from typing import Any, Dict, List, Optional

import pytest

import src.core.alarm as alarm

DEVICE_ID = "dev1"
DEVICE_NAME = "Forte"
TARGET_VOLUME = 45
ALL_STEPS = [5, 10, 15, 20, 25, 30, 35, 40, 45]


def _playing(volume: Optional[int]) -> Dict[str, Any]:
    return {
        "is_playing": True,
        "device": {"id": DEVICE_ID, "name": DEVICE_NAME, "volume_percent": volume},
        "context": {"uri": "spotify:playlist:p"},
    }


def _paused() -> Dict[str, Any]:
    return {
        "is_playing": False,
        "device": {"id": DEVICE_ID, "name": DEVICE_NAME, "volume_percent": 20},
    }


def _foreign() -> Dict[str, Any]:
    return {
        "is_playing": True,
        "device": {"id": "other", "name": "Kitchen", "volume_percent": 50},
        "context": {"uri": "spotify:playlist:zzz"},
    }


@pytest.fixture
def fade_env(monkeypatch):
    """No-op sleeps, record set_volume calls, feed scripted playback reads."""
    env: Dict[str, Any] = {"volumes": [], "reads": []}

    def fake_set_volume(token, volume, device_id):
        env["volumes"].append(volume)
        return True

    def fake_get_current_playback(token):
        reads: List[Any] = env["reads"]
        # Hold the last read once the script is exhausted (steady state).
        return reads.pop(0) if len(reads) > 1 else reads[0]

    monkeypatch.setattr(alarm.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(alarm, "set_volume", fake_set_volume)
    monkeypatch.setattr(alarm, "get_current_playback", fake_get_current_playback)
    return env


def _run(env) -> bool:
    return alarm._run_fade_in("tok", DEVICE_ID, DEVICE_NAME, TARGET_VOLUME, probe=None, detect_silence=True)


def test_fade_completes_while_playing(fade_env):
    fade_env["reads"] = [_playing(0), _playing(5)]
    assert _run(fade_env) is False
    assert fade_env["volumes"] == ALL_STEPS


def test_fade_aborts_on_mute_after_audible(fade_env):
    # Preset echo (0%), one audible read, then the hardware button mutes.
    fade_env["reads"] = [_playing(0), _playing(5), _playing(0)]
    assert _run(fade_env) is True
    # Detection holds the ramp: no further step un-mutes the device.
    assert fade_env["volumes"] == [5, 10]


def test_fade_aborts_on_pause(fade_env):
    # A pause counts after a confirmed playing read (no audible volume needed).
    fade_env["reads"] = [_playing(0), _paused()]
    assert _run(fade_env) is True
    assert fade_env["volumes"] == [5]


def test_fade_ignores_pause_before_first_playing_read(fade_env):
    # Right after start the transport may report paused for a while (stale):
    # without a single confirmed playing read this must never count as a press.
    fade_env["reads"] = [_paused()]
    assert _run(fade_env) is False
    assert fade_env["volumes"] == ALL_STEPS


def test_fade_detection_disabled_skips_reads(fade_env, monkeypatch):
    # Without an armed snooze session nothing could ever resume an aborted
    # ramp, so detection is off and the player is never read.
    def fail(token):
        pytest.fail("player must not be read when detection is disabled")

    monkeypatch.setattr(alarm, "get_current_playback", fail)
    result = alarm._run_fade_in(
        "tok", DEVICE_ID, DEVICE_NAME, TARGET_VOLUME, probe=None, detect_silence=False
    )
    assert result is False
    assert fade_env["volumes"] == ALL_STEPS


def test_fade_survives_single_stale_silence(fade_env):
    # One stale paused read must not abort the alarm ramp.
    fade_env["reads"] = [_playing(0), _playing(5), _paused(), _playing(15)]
    assert _run(fade_env) is False
    assert fade_env["volumes"] == ALL_STEPS


def test_fade_ignores_mute_without_prior_audible_read(fade_env):
    # Stale reads may echo the 0% preset for a while: never a button press.
    fade_env["reads"] = [_playing(0)]
    assert _run(fade_env) is False
    assert fade_env["volumes"] == ALL_STEPS


def test_fade_ignores_empty_and_foreign_reads(fade_env):
    fade_env["reads"] = [None, _foreign(), _playing(10)]
    assert _run(fade_env) is False
    assert fade_env["volumes"] == ALL_STEPS


def test_fade_verdict_read_error_is_unknown(monkeypatch):
    def boom(token):
        raise RuntimeError("network down")

    monkeypatch.setattr(alarm, "get_current_playback", boom)
    verdict, seen, heard = alarm._fade_playback_verdict("tok", DEVICE_ID, DEVICE_NAME, True, True)
    assert verdict == "unknown"
    assert (seen, heard) == (True, True)


def test_fade_verdict_device_without_volume_control(monkeypatch):
    # volume_percent None -> no mute signal; only a pause can silence.
    monkeypatch.setattr(alarm, "get_current_playback", lambda token: _playing(None))
    verdict, seen, heard = alarm._fade_playback_verdict("tok", DEVICE_ID, DEVICE_NAME, False, False)
    assert verdict == "active"
    assert seen is True
    assert heard is False
