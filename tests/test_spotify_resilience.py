import builtins
import json
import time

import pytest
import requests

from src.api import spotify


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Avoid real sleeping inside spotify module during tests."""
    monkeypatch.setattr(spotify.time, "sleep", lambda _: None)


@pytest.fixture
def temp_token_path(tmp_path, monkeypatch):
    """Redirect token persistence to a temporary path."""
    token_path = tmp_path / "token_state.json"
    monkeypatch.setattr(spotify, "TOKEN_STATE_PATH", token_path)
    return token_path


def test_refresh_access_token_retries_success(monkeypatch, temp_token_path):
    """Refresh should retry on timeouts and eventually succeed."""
    attempts = []

    def fake_post(*args, **kwargs):
        attempt = len(attempts) + 1
        attempts.append(attempt)
        if attempt < 3:
            raise requests.exceptions.ReadTimeout("timeout")

        class _Resp:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"access_token": "token123", "expires_in": 3600}

        return _Resp()

    monkeypatch.setattr(spotify, "CLIENT_ID", "client")
    monkeypatch.setattr(spotify, "CLIENT_SECRET", "secret")
    monkeypatch.setattr(spotify, "REFRESH_TOKEN", "refresh")
    monkeypatch.setattr(spotify.SESSION, "post", fake_post)

    token = spotify.refresh_access_token(with_retries=True)

    assert token == "token123"
    assert len(attempts) == 3
    saved = json.loads(temp_token_path.read_text(encoding="utf-8"))
    assert saved["access_token"] == "token123"


def test_ensure_token_valid_triggers_cache_refresh(monkeypatch):
    """ensure_token_valid should delegate to cache when refresh is needed."""
    windows = []

    def fake_token_needs_refresh(window):
        windows.append(window)
        return True

    def fake_cache_ensure(window):
        windows.append(f"ensure:{window}")
        return "fresh-token"

    monkeypatch.setattr(spotify, "token_needs_refresh", fake_token_needs_refresh)
    monkeypatch.setattr(spotify, "cache_ensure_token_valid", fake_cache_ensure)

    result = spotify.ensure_token_valid(180)

    assert result == "fresh-token"
    assert windows == [180, "ensure:180"]


def test_play_with_retry_uses_fallback_device(monkeypatch):
    """When primary device fails, fallback device should be attempted."""
    attempts = []

    def fake_start(token, device_id, playlist_uri, volume_percent, shuffle, **kwargs):
        attempts.append(device_id)
        return device_id != "primary"

    def fake_verify(token, device_id, playlist_uri, attempts=2, wait_seconds=0.5, **kwargs):
        return device_id == "fallback"

    monkeypatch.setattr(spotify, "_start_playback_on_device", fake_start)
    monkeypatch.setattr(spotify, "_verify_playback_state", fake_verify)
    monkeypatch.setattr(spotify, "ensure_token_valid", lambda *args, **kwargs: "token")
    monkeypatch.setattr(spotify, "get_device_id", lambda token, name: "fallback")
    monkeypatch.setattr(spotify, "PLAYER_MAX_ATTEMPTS", 1)

    success = spotify.play_with_retry(
        "token",
        "primary",
        playlist_uri="spotify:playlist:abc",
        volume_percent=40,
        shuffle=False,
        fallback_device="FallbackDevice",
    )

    assert success is True
    assert attempts == ["primary", "fallback"]


class _Resp:
    def __init__(self, status_code=204):
        self.status_code = status_code
        self.text = ""


def _record_start_calls(monkeypatch):
    """Stub the HTTP layer of _start_playback_on_device and record the call order."""
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append("play" if "/me/player/play" in url else "transfer")
        return _Resp(204)

    def fake_set_volume(token, volume_percent, device_id=None):
        calls.append(("volume", volume_percent))
        return True

    monkeypatch.setattr(spotify, "_spotify_request", fake_request)
    monkeypatch.setattr(spotify, "set_volume", fake_set_volume)
    return calls


def test_start_playback_enforce_volume_resets_right_after_play(monkeypatch):
    # Some speakers ignore the preset and start at their own last volume:
    # the fade alarm re-asserts 0% immediately after /play.
    calls = _record_start_calls(monkeypatch)
    events = []
    ok = spotify._start_playback_on_device(
        "tok", "dev1", "spotify:track:x", 0, False,
        enforce_volume=True, trace=lambda e, d: events.append((e, d)),
    )
    assert ok is True
    assert calls == ["transfer", ("volume", 0), "play", ("volume", 0)]
    assert [e for e, _ in events] == ["play_sent", "volume_enforced"]
    assert events[1][1]["phase"] == "after_play"


def test_start_playback_without_enforce_keeps_single_preset(monkeypatch):
    calls = _record_start_calls(monkeypatch)
    assert spotify._start_playback_on_device("tok", "dev1", "spotify:track:x", 50, False) is True
    assert calls == ["transfer", ("volume", 50), "play"]


def _verify_reads(monkeypatch, reads):
    script = list(reads)
    monkeypatch.setattr(spotify, "get_current_playback", lambda token: script.pop(0) if len(script) > 1 else script[0])


def test_verify_enforces_expected_volume_and_traces(monkeypatch):
    volumes = []
    monkeypatch.setattr(spotify, "set_volume", lambda t, v, d=None: volumes.append((v, d)) or True)
    loud = {"is_playing": True, "device": {"id": "dev1", "volume_percent": 45},
            "context": {"uri": "spotify:playlist:p"}, "progress_ms": 900}
    _verify_reads(monkeypatch, [None, loud])
    events = []
    ok = spotify._verify_playback_state(
        "tok", "dev1", "spotify:playlist:p", attempts=3, wait_seconds=0.2,
        expected_volume=0, trace=lambda e, d: events.append((e, d)),
    )
    assert ok is True
    assert volumes == [(0, "dev1")]
    assert [e for e, _ in events] == ["verify_read", "verify_read", "volume_enforced"]
    assert events[0][1]["empty"] is True
    assert events[1][1]["volume"] == 45
    assert events[2][1]["seen"] == 45


def test_verify_leaves_volume_alone_when_matching_or_not_requested(monkeypatch):
    volumes = []
    monkeypatch.setattr(spotify, "set_volume", lambda t, v, d=None: volumes.append(v) or True)
    quiet = {"is_playing": True, "device": {"id": "dev1", "volume_percent": 0},
             "context": {"uri": "spotify:playlist:p"}}
    _verify_reads(monkeypatch, [quiet])
    assert spotify._verify_playback_state("tok", "dev1", "spotify:playlist:p", expected_volume=0) is True
    loud = dict(quiet, device={"id": "dev1", "volume_percent": 45})
    _verify_reads(monkeypatch, [loud])
    assert spotify._verify_playback_state("tok", "dev1", "spotify:playlist:p") is True
    # Foreign device reads never trigger an enforce either.
    foreign = dict(quiet, device={"id": "other", "volume_percent": 45})
    _verify_reads(monkeypatch, [foreign])
    assert spotify._verify_playback_state("tok", "dev1", "spotify:playlist:p", attempts=2, expected_volume=0) is False
    assert volumes == []


def test_play_with_retry_threads_enforce_and_trace(monkeypatch):
    seen = {}

    def fake_start(token, device_id, playlist_uri, volume_percent, shuffle, **kwargs):
        seen["start"] = kwargs
        return True

    def fake_verify(token, device_id, playlist_uri, **kwargs):
        seen["verify"] = kwargs
        return True

    monkeypatch.setattr(spotify, "_start_playback_on_device", fake_start)
    monkeypatch.setattr(spotify, "_verify_playback_state", fake_verify)
    monkeypatch.setattr(spotify, "ensure_token_valid", lambda *a, **k: "token")
    monkeypatch.setattr(spotify, "set_volume", lambda *a, **k: True)
    events = []
    trace = lambda e, d: events.append(e)

    assert spotify.start_playback("tok", "dev1", "spotify:playlist:p", volume_percent=0,
                                  enforce_volume=True, trace=trace) is True
    assert seen["start"] == {"enforce_volume": True, "trace": trace}
    assert seen["verify"] == {"expected_volume": 0, "trace": trace}
    assert "verified" in events

    assert spotify.start_playback("tok", "dev1", "spotify:playlist:p", volume_percent=50) is True
    assert seen["verify"]["expected_volume"] is None


def test_start_playback_without_volume_leaves_device_volume(monkeypatch):
    # UI play passes no volume: the speaker keeps whatever the user set,
    # instead of the old hardcoded 50% default.
    calls = _record_start_calls(monkeypatch)
    assert spotify._start_playback_on_device("tok", "dev1", "spotify:track:x", None, False) is True
    assert calls == ["transfer", "play"]


def test_play_with_retry_default_never_sets_volume(monkeypatch):
    volumes = []
    monkeypatch.setattr(spotify, "_start_playback_on_device", lambda *a, **k: True)
    monkeypatch.setattr(spotify, "_verify_playback_state", lambda *a, **k: True)
    monkeypatch.setattr(spotify, "ensure_token_valid", lambda *a, **k: "token")
    monkeypatch.setattr(spotify, "set_volume", lambda t, v, d=None: volumes.append(v) or True)
    assert spotify.start_playback("tok", "dev1", "spotify:playlist:p") is True
    assert volumes == []
    assert spotify.start_playback("tok", "dev1", "spotify:playlist:p", volume_percent=30) is True
    assert volumes == [30]


def test_trace_errors_never_break_playback(monkeypatch):
    _record_start_calls(monkeypatch)

    def broken_trace(event, data):
        raise RuntimeError("probe logger down")

    assert spotify._start_playback_on_device(
        "tok", "dev1", "spotify:track:x", 0, False, enforce_volume=True, trace=broken_trace,
    ) is True


def test_save_token_atomically_sets_permissions_in_plaintext_mode(monkeypatch, temp_token_path):
    chmod_calls = []
    monkeypatch.setenv("SPOTIPI_TOKEN_PLAINTEXT", "1")
    monkeypatch.setattr(spotify.os, "chmod", lambda path, mode: chmod_calls.append((path, mode)))

    spotify._save_token_atomically({
        "access_token": "token",
        "expires_at": int(time.time()) + 60
    })

    assert temp_token_path.exists()
    assert chmod_calls
    assert chmod_calls[-1][1] == 0o600


def test_save_token_atomically_sets_permissions_in_encrypted_mode(monkeypatch, temp_token_path):
    chmod_calls = []
    monkeypatch.delenv("SPOTIPI_TOKEN_PLAINTEXT", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(spotify.os, "chmod", lambda path, mode: chmod_calls.append((path, mode)))

    spotify._save_token_atomically({
        "access_token": "token",
        "expires_at": int(time.time()) + 60
    })

    assert temp_token_path.exists()
    assert chmod_calls
    assert chmod_calls[-1][1] == 0o600


class _FakeTxn:
    def __init__(self, base):
        import copy
        self._base = copy.deepcopy(base)
        self.saved = None

    def load(self):
        import copy
        return copy.deepcopy(self._base)

    def save(self, cfg):
        import copy
        self.saved = copy.deepcopy(cfg)
        return True


def _install_fake_config(monkeypatch, initial):
    """Redirect spotify's config helpers to an in-memory store; record txns."""
    from contextlib import contextmanager

    state = {"cfg": initial}
    txns = []

    def fake_load_safe():
        import copy
        return copy.deepcopy(state["cfg"])

    @contextmanager
    def fake_txn():
        txn = _FakeTxn(state["cfg"])
        txns.append(txn)
        yield txn
        if txn.saved is not None:
            state["cfg"] = txn.saved

    monkeypatch.setattr(spotify, "load_config_safe", fake_load_safe)
    monkeypatch.setattr(spotify, "config_transaction", fake_txn)
    return state, txns


def test_remember_devices_seen_caches_visible_device_ids(monkeypatch):
    state, txns = _install_fake_config(monkeypatch, {"last_known_devices": {}})

    spotify._remember_devices_seen([
        {"id": "dev1", "name": "Schlafzimmer"},
        {"id": "dev2", "name": "Wohnzimmer"},
        {"id": None, "name": "No Id"},  # ignored
    ])

    cache = state["cfg"]["last_known_devices"]
    assert cache["schlafzimmer"]["id"] == "dev1"
    assert cache["wohnzimmer"]["id"] == "dev2"
    assert "no id" not in cache
    assert len(txns) == 1  # exactly one write


def test_remember_devices_seen_skips_write_when_unchanged(monkeypatch):
    initial = {
        "last_known_devices": {
            "schlafzimmer": {
                "id": "dev1",
                "name": "Schlafzimmer",
                "requested_name": "Schlafzimmer",
                "updated_at": 1.0,
            }
        }
    }
    state, txns = _install_fake_config(monkeypatch, initial)

    spotify._remember_devices_seen([{"id": "dev1", "name": "Schlafzimmer"}])

    # No mapping changed -> no transaction opened -> no disk write (SD wear).
    assert txns == []
    assert state["cfg"]["last_known_devices"]["schlafzimmer"]["id"] == "dev1"


def test_remember_devices_seen_updates_changed_id(monkeypatch):
    initial = {
        "last_known_devices": {
            "schlafzimmer": {
                "id": "stale",
                "name": "Schlafzimmer",
                "requested_name": "Schlafzimmer",
                "updated_at": 1.0,
            }
        }
    }
    state, txns = _install_fake_config(monkeypatch, initial)

    spotify._remember_devices_seen([{"id": "fresh", "name": "Schlafzimmer"}])

    assert state["cfg"]["last_known_devices"]["schlafzimmer"]["id"] == "fresh"
    assert len(txns) == 1


def test_save_token_atomically_sets_permissions_on_importerror_fallback(monkeypatch, temp_token_path):
    chmod_calls = []
    real_import = builtins.__import__

    def _import_with_forced_failure(name, globals=None, locals=None, fromlist=(), level=0):
        if name.endswith("token_encryption"):
            raise ImportError("forced import error")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.delenv("SPOTIPI_TOKEN_PLAINTEXT", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(builtins, "__import__", _import_with_forced_failure)
    monkeypatch.setattr(spotify.os, "chmod", lambda path, mode: chmod_calls.append((path, mode)))

    spotify._save_token_atomically({
        "access_token": "token",
        "expires_at": int(time.time()) + 60
    })

    assert temp_token_path.exists()
    assert chmod_calls
    assert chmod_calls[-1][1] == 0o600
