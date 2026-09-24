"""Structured alarm logging helpers for reliability diagnostics."""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import socket
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from ..utils.logger import setup_logger
from ..utils.token_cache import get_token_cache_info

PROBE_WINDOW_SECONDS = 5 * 60
NETWORK_PROBE_TIMEOUT = float(os.getenv("SPOTIPI_NETWORK_PROBE_TIMEOUT", "1.5"))
NETWORK_PROBE_HOST = os.getenv("SPOTIPI_NETWORK_PROBE_HOST", "api.spotify.com")
NETWORK_PROBE_PORT = int(os.getenv("SPOTIPI_NETWORK_PROBE_PORT", "443"))

_probe_logger = setup_logger("alarm_probe")
_probe_logger.setLevel(logging.INFO)
for _handler in _probe_logger.handlers:
    _handler.setLevel(logging.INFO)
_ntp_cache: Dict[str, Any] = {"ts": 0.0, "value": None}
_network_cache: Dict[str, Any] = {"ts": 0.0, "value": None}


def _build_alarm_id(scheduled: _dt.datetime) -> str:
    utc_ts = scheduled.astimezone(_dt.timezone.utc)
    return utc_ts.strftime("%Y%m%dT%H%M%SZ")


def _parse_offset_to_ms(raw: str) -> Optional[float]:
    match = re.match(r"([+-]?[0-9]*\.?[0-9]+)\s*(us|µs|ms|s)?", raw)
    if not match:
        return None
    value = float(match.group(1))
    unit = (match.group(2) or "").lower()
    if unit in ("us", "µs"):
        return value / 1000.0
    if unit == "ms" or unit == "":
        return value
    if unit == "s":
        return value * 1000.0
    return None


def get_ntp_offset_ms() -> Optional[float]:
    """Best-effort NTP offset from systemd-timesyncd (cached for 60s)."""
    now = time.monotonic()
    cached = _ntp_cache.get("value")
    cached_ts = _ntp_cache.get("ts", 0.0)
    if cached is not None and (now - cached_ts) < 60:
        return cached
    try:
        proc = subprocess.run(
            ["timedatectl", "timesync-status"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        _ntp_cache.update(ts=now, value=None)
        return None
    offset_ms: Optional[float] = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("Offset:"):
            raw_value = line.split(":", 1)[1].strip()
            offset_ms = _parse_offset_to_ms(raw_value)
            break
    _ntp_cache.update(ts=now, value=offset_ms)
    return offset_ms


def check_network_ready() -> Optional[bool]:
    """Best-effort TCP connect check (cached for 15s)."""
    now = time.monotonic()
    cached = _network_cache.get("value")
    cached_ts = _network_cache.get("ts", 0.0)
    if cached is not None and (now - cached_ts) < 15:
        return bool(cached)
    if os.getenv("SPOTIPI_SKIP_NETWORK_PROBE") == "1":
        _network_cache.update(ts=now, value=None)
        return None
    try:
        with socket.create_connection(
            (NETWORK_PROBE_HOST, NETWORK_PROBE_PORT),
            timeout=NETWORK_PROBE_TIMEOUT,
        ):
            result: Optional[bool] = True
    except OSError:
        result = False
    _network_cache.update(ts=now, value=result)
    return result


def summarize_token_state() -> Dict[str, Any]:
    info = get_token_cache_info()
    metrics = info.get("cache_metrics", {})
    token_info = info.get("token_info", {}) or {}
    return {
        "has_token": info.get("has_cached_token"),
        "expires_in_sec": token_info.get("time_until_expiry_seconds"),
        "is_nearly_expired": token_info.get("is_nearly_expired"),
        "refresh_attempts": metrics.get("refresh_attempts"),
        "refresh_successes": metrics.get("refresh_successes"),
    }


@dataclass
class AlarmProbeContext:
    scheduled_at: _dt.datetime
    timezone: ZoneInfo
    alarm_time: str = ""
    config_snapshot: Dict[str, Any] = field(default_factory=dict)
    ntp_offset_ms: Optional[float] = None
    network_ready: Optional[bool] = None
    spotify_auth_state: Optional[Dict[str, Any]] = None
    device_discovery_result: Optional[Any] = None
    env_sampled: bool = False

    def alarm_id(self) -> str:
        return _build_alarm_id(self.scheduled_at)

    def device_name(self) -> Optional[str]:
        value = self.config_snapshot.get("device_name")
        if isinstance(value, str):
            return value
        return None

    def ensure_environment(self) -> None:
        if self.env_sampled:
            return
        self.ntp_offset_ms = get_ntp_offset_ms()
        self.network_ready = check_network_ready()
        self.spotify_auth_state = summarize_token_state()
        self.env_sampled = True

    def set_device_result(self, phase: str, status: str, **detail: Any) -> None:
        payload = {"phase": phase, "status": status}
        if detail:
            payload.update(detail)
        self.device_discovery_result = payload


def log_alarm_probe(
    context: Optional[AlarmProbeContext],
    state: str,
    *,
    extra: Optional[Dict[str, Any]] = None,
    force: bool = False,
) -> None:
    if context is None:
        return
    now_utc = _dt.datetime.now(tz=_dt.timezone.utc)
    scheduled_utc = context.scheduled_at.astimezone(_dt.timezone.utc)
    delta_sec = (scheduled_utc - now_utc).total_seconds()
    if not force and abs(delta_sec) > PROBE_WINDOW_SECONDS:
        return
    context.ensure_environment()
    event: Dict[str, Any] = {
        "kind": "alarm_probe",
        "scheduler_state": state,
        "alarm_id": context.alarm_id(),
        "scheduled_utc": scheduled_utc.isoformat(),
        "scheduled_local": context.scheduled_at.astimezone(context.timezone).isoformat(),
        "now_utc": now_utc.isoformat(),
        "now_local": now_utc.astimezone(context.timezone).isoformat(),
        "monotonic_now": time.monotonic(),
        "delta_sec": delta_sec,
        "alarm_time": context.alarm_time,
        "ntp_offset_ms": context.ntp_offset_ms,
        "network_ready": context.network_ready,
        "spotify_auth_state": context.spotify_auth_state,
        "device_discovery_result": context.device_discovery_result,
    }
    if extra:
        event.update(extra)
    try:
        _probe_logger.info(json.dumps(event, ensure_ascii=True, sort_keys=True))
    except TypeError:
        # Fallback: coerce non-serializable entries
        serializable_event = {}
        for key, value in event.items():
            try:
                json.dumps(value, ensure_ascii=False)
                serializable_event[key] = value
            except TypeError:
                serializable_event[key] = str(value)
        _probe_logger.info(json.dumps(serializable_event, ensure_ascii=True, sort_keys=True))


def log_snooze_probe(state: str, extra: Optional[Dict[str, Any]] = None) -> None:
    """Emit a snooze-monitor event on the probe logger.

    The snooze module runs without an ``AlarmProbeContext`` (its monitor
    outlives the alarm run), but its events must reach journald too — the
    regular 'snooze' logger does not on the Pi. Never raises.
    """
    try:
        now_utc = _dt.datetime.now(tz=_dt.timezone.utc)
        event: Dict[str, Any] = {
            "kind": "snooze_probe",
            "snooze_state": state,
            "now_utc": now_utc.isoformat(),
            "now_local": now_utc.astimezone().isoformat(),
            "monotonic_now": time.monotonic(),
        }
        if extra:
            event.update(extra)
        _probe_logger.info(json.dumps(event, ensure_ascii=True, sort_keys=True, default=str))
    except Exception:
        pass
