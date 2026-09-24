#!/usr/bin/env python3
"""
🎵 Spotify API Integration for SpotiPi
Provides comprehensive Spotify Web API functionality including:
- Authentication and token management
- Music library access (playlists, albums, tracks, artists)  
- Playback control with device management
- Parallel data loading for performance
"""

import copy
import json
import logging
import os
import random
import re
import socket
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import requests
from dotenv import load_dotenv

# Use the new centralized config system
from ..config import load_config
from ..utils.cache_migration import get_cache_migration_layer
from ..utils.library_utils import compute_library_hash
from ..utils.perf_monitor import perf_monitor
from ..utils.thread_safety import config_transaction, load_config_safe
# Import token caching system
from ..utils.token_cache import TokenResponse
from ..utils.token_cache import ensure_token_valid as cache_ensure_token_valid
from ..utils.token_cache import (force_token_refresh, get_cached_token,
                                 initialize_token_cache,
                                 invalidate_token_cache, seed_token_cache,
                                 token_needs_refresh)
from ..utils.spotify_secrets import get_spotify_credentials
from .http import DEFAULT_TIMEOUT, SESSION

# Use the new central#  Exportable functions - Updated for new config system
__all__ = [
    "refresh_access_token", "ensure_token_valid", "get_access_token", "force_refresh_token",
    "get_playlists", "get_devices", "get_device_id",
    "start_playback", "play_with_retry", "stop_playback", "resume_playback", "toggle_playback",
    "get_current_playback", "get_current_track", "get_current_spotify_volume", 
    "get_saved_albums", "get_user_saved_tracks", "get_followed_artists",
    "get_recently_played_tracks", "get_user_top_items", "get_artist_albums", "search_items",
    "get_playback_queue", "SpotifyScopeError",
    "set_volume", "get_playback_status", "get_user_library", "get_combined_playback",
    "load_music_library_parallel", "spotify_network_health", "get_user_profile",
    "get_runtime_credentials", "reset_spotify_auth_state"
]


class SpotifyScopeError(RuntimeError):
    """Raised when Spotify rejects a request due to missing OAuth scopes."""

    def __init__(self, required_scope: str, message: Optional[str] = None):
        self.required_scope = required_scope
        super().__init__(message or f"Spotify scope required: {required_scope}")

# 🔧 File paths - Use path-agnostic configuration
def _get_app_config_dir():
    """Get application configuration directory path-agnostically"""
    app_name = os.getenv("SPOTIPI_APP_NAME", "spotipi")
    return os.path.expanduser(f"~/.{app_name}")

def _get_env_path():
    """Get environment file path dynamically"""
    return os.path.join(_get_app_config_dir(), ".env")

ENV_PATH = _get_env_path()

# 🌍 Load environment variables
# Load local project defaults first, then let canonical runtime secrets win.
load_dotenv()
if os.path.exists(ENV_PATH):
    load_dotenv(dotenv_path=ENV_PATH, override=True)
_LOW_POWER_MODE = os.getenv('SPOTIPI_LOW_POWER', '').lower() in ('1', 'true', 'yes', 'on')

TOKEN_STATE_PATH = Path(_get_app_config_dir()) / "spotify_token.json"

# Backward-compatible overrides used by legacy tests and tooling.
# Keep default ``None`` so runtime credentials are always read dynamically.
CLIENT_ID: Optional[str] = None
CLIENT_SECRET: Optional[str] = None
REFRESH_TOKEN: Optional[str] = None
USERNAME: Optional[str] = None


def get_runtime_credentials(use_cache: bool = True) -> Dict[str, str]:
    """Return current Spotify credentials from the runtime secret store."""
    return get_spotify_credentials(use_cache=use_cache)


def _resolve_runtime_credentials(use_cache: bool = True) -> Dict[str, str]:
    """Return runtime credentials with optional compatibility overrides applied."""
    credentials = get_runtime_credentials(use_cache=use_cache)

    if CLIENT_ID is not None:
        credentials["client_id"] = str(CLIENT_ID).strip()
    if CLIENT_SECRET is not None:
        credentials["client_secret"] = str(CLIENT_SECRET).strip()
    if REFRESH_TOKEN is not None:
        credentials["refresh_token"] = str(REFRESH_TOKEN).strip()
    if USERNAME is not None:
        credentials["username"] = str(USERNAME).strip()

    return credentials


def _enforce_token_permissions() -> None:
    """Ensure token state file permissions are owner read/write only."""
    try:
        os.chmod(TOKEN_STATE_PATH, 0o600)
    except OSError:
        pass


def _coerce_float(value: Optional[str], default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT = DEFAULT_TIMEOUT
DEFAULT_HTTP_TIMEOUT = _coerce_float(os.getenv('SPOTIPI_HTTP_TIMEOUT'), DEFAULT_READ_TIMEOUT)
LONG_HTTP_TIMEOUT = _coerce_float(
    os.getenv('SPOTIPI_HTTP_LONG_TIMEOUT'),
    max(DEFAULT_HTTP_TIMEOUT * 1.5, DEFAULT_READ_TIMEOUT * 1.5, 20.0)
)
SPOTIFY_API_TIMEOUT = max(DEFAULT_HTTP_TIMEOUT, LONG_HTTP_TIMEOUT, DEFAULT_READ_TIMEOUT)

TOKEN_REFRESH_MAX_ATTEMPTS = max(1, int(os.getenv('SPOTIPI_TOKEN_REFRESH_ATTEMPTS', '3')))
TOKEN_REFRESH_BACKOFF_BASE = _coerce_float(os.getenv('SPOTIPI_TOKEN_REFRESH_BACKOFF', None), 0.5)
TOKEN_REFRESH_BACKOFF_JITTER = _coerce_float(os.getenv('SPOTIPI_TOKEN_REFRESH_JITTER', None), 0.4)
TOKEN_SAFETY_WINDOW = max(30, int(os.getenv('SPOTIPI_TOKEN_SAFETY_WINDOW', '120')))
PREWARM_SECONDS = max(30, int(os.getenv('SPOTIPI_PREWARM_SECONDS', str(TOKEN_SAFETY_WINDOW))))

PLAYER_MAX_ATTEMPTS = max(1, int(os.getenv('SPOTIPI_PLAYER_RETRIES', '3')))
PLAYER_BACKOFF_BASE = _coerce_float(os.getenv('SPOTIPI_PLAYER_BACKOFF', None), 1.0)
PLAYER_BACKOFF_JITTER = _coerce_float(os.getenv('SPOTIPI_PLAYER_JITTER', None), 0.35)
FALLBACK_DEVICE_NAME = os.getenv('SPOTIPI_FALLBACK_DEVICE')

PLAYBACK_VERIFY_ATTEMPTS = max(2, int(os.getenv('SPOTIPI_PLAYBACK_VERIFY_ATTEMPTS', '6')))
PLAYBACK_VERIFY_WAIT = max(0.2, _coerce_float(os.getenv('SPOTIPI_PLAYBACK_VERIFY_WAIT', None), 1.0))
SHUFFLE_RETRY_ATTEMPTS = max(1, int(os.getenv('SPOTIPI_SHUFFLE_RETRY_ATTEMPTS', '2')))
SHUFFLE_RETRY_DELAY = max(0.2, _coerce_float(os.getenv('SPOTIPI_SHUFFLE_RETRY_DELAY', None), 0.75))

def _max_concurrency() -> int:
    override = os.getenv('SPOTIPI_MAX_CONCURRENCY')
    if override:
        try:
            value = max(1, int(override))
            return value
        except ValueError:
            logging.getLogger('spotify').warning(
                "Invalid SPOTIPI_MAX_CONCURRENCY=%s; using safe default",
                override
            )
    return 2 if _LOW_POWER_MODE else 3


MAX_CONCURRENCY = _max_concurrency()
_REQUEST_SEMAPHORE = threading.Semaphore(MAX_CONCURRENCY)
_SINGLE_FLIGHT_LOCK = threading.Lock()
_IN_FLIGHT: Dict[Tuple[str, str, Tuple[Any, ...]], "_SingleFlight"] = {}

# Global ThreadPoolExecutor for library loading - reused instead of creating per-call
# This reduces thread creation overhead, especially on Pi Zero W
_LIBRARY_EXECUTOR: Optional[ThreadPoolExecutor] = None
_LIBRARY_EXECUTOR_LOCK = threading.Lock()


def _get_library_executor() -> ThreadPoolExecutor:
    """Get or create the global library loading executor.
    
    Reuses a single ThreadPoolExecutor instance for all library loading
    operations to reduce thread creation overhead.
    
    Returns:
        ThreadPoolExecutor: Shared executor for library operations
    """
    global _LIBRARY_EXECUTOR
    if _LIBRARY_EXECUTOR is None:
        with _LIBRARY_EXECUTOR_LOCK:
            if _LIBRARY_EXECUTOR is None:
                max_workers = _get_library_worker_limit()
                _LIBRARY_EXECUTOR = ThreadPoolExecutor(
                    max_workers=max_workers,
                    thread_name_prefix="spotipi-library"
                )
                logging.getLogger('spotify').debug(
                    "Library executor initialized with %d workers", max_workers
                )
    return _LIBRARY_EXECUTOR


def _coerce_timeout(value: Optional[Union[int, float, Tuple[float, float]]]) -> Optional[Union[float, Tuple[float, float]]]:
    if value is None:
        return None
    if isinstance(value, tuple):
        try:
            connect = max(0.2, float(value[0]))
            read = max(0.2, float(value[1]))
            return connect, read
        except (TypeError, ValueError, IndexError):
            return None
    try:
        return max(0.2, float(value))
    except (TypeError, ValueError):
        return None


def _get_library_worker_limit() -> int:
    """Determine how many worker threads to use for library fetches."""
    override = os.getenv('SPOTIPI_LIBRARY_WORKERS')
    if override:
        try:
            value = max(1, int(override))
            return value
        except ValueError:
            logging.getLogger('spotify').warning(
                "Invalid SPOTIPI_LIBRARY_WORKERS=%s; falling back to default",
                override
            )
    base = 2 if _LOW_POWER_MODE else 3
    return max(1, min(base, MAX_CONCURRENCY))


def _compute_backoff(base: float, attempt: int, jitter: float, cap: float = 15.0) -> float:
    """Compute exponential backoff with jitter."""
    delay = base * (2 ** max(attempt - 1, 0))
    jitter_value = random.uniform(0, delay * max(jitter, 0.0))
    return min(delay + jitter_value, cap)


def _save_token_atomically(payload: Dict[str, Any]) -> None:
    """Persist token payload atomically with encryption to avoid corruption.
    
    Uses token encryption module for secure storage at rest.
    Falls back to obfuscation if cryptography library is not available.
    """
    if os.getenv("SPOTIPI_TOKEN_PLAINTEXT") == "1" or os.getenv("PYTEST_CURRENT_TEST"):
        try:
            TOKEN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = TOKEN_STATE_PATH.with_suffix(".tmp")
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
            os.replace(tmp_path, TOKEN_STATE_PATH)
            _enforce_token_permissions()
            return
        except Exception as exc:
            logging.getLogger('spotify').warning("Failed to persist token payload: %s", exc)
            return

    try:
        from ..utils.token_encryption import encrypt_token_payload, is_encryption_available
        
        TOKEN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = TOKEN_STATE_PATH.with_suffix(".tmp")
        
        # Encrypt or obfuscate the payload
        encrypted_data = encrypt_token_payload(payload)
        
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(encrypted_data)
        os.replace(tmp_path, TOKEN_STATE_PATH)
        _enforce_token_permissions()
            
        encryption_type = "encrypted" if is_encryption_available() else "obfuscated"
        logging.getLogger('spotify').debug(f"Token saved ({encryption_type})")
    except ImportError:
        # Fallback to legacy plain JSON if encryption module not available
        try:
            TOKEN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = TOKEN_STATE_PATH.with_suffix(".tmp")
            with tmp_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
            os.replace(tmp_path, TOKEN_STATE_PATH)
            _enforce_token_permissions()
        except Exception as exc:
            logging.getLogger('spotify').warning("Failed to persist token payload: %s", exc)
    except Exception as exc:
        logging.getLogger('spotify').warning("Failed to persist token payload: %s", exc)


def _load_persisted_token() -> Optional[TokenResponse]:
    """Load persisted token data from disk if available and not expired.
    
    Supports both encrypted and legacy plain JSON formats.
    """
    try:
        if not TOKEN_STATE_PATH.exists():
            return None
            
        with TOKEN_STATE_PATH.open("r", encoding="utf-8") as handle:
            raw_data = handle.read()
        
        # Try decryption first
        data = None
        try:
            from ..utils.token_encryption import decrypt_token_payload
            data = decrypt_token_payload(raw_data)
        except ImportError:
            pass  # Encryption module not available
        
        # Fall back to plain JSON if decryption failed or not encrypted
        if data is None:
            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                logging.getLogger('spotify').warning("Could not parse persisted token (neither encrypted nor JSON)")
                return None

        access_token = data.get("access_token")
        expires_at = float(data.get("expires_at", 0))
        received_at = float(data.get("received_at", data.get("created_at", time.time())))
        refresh_token = data.get("refresh_token")
        scope = data.get("scope")
        token_type = data.get("token_type")

        if not access_token or not expires_at:
            return None
        if expires_at <= time.time():
            return None

        expires_in = int(max(60, expires_at - received_at))
        return TokenResponse(
            access_token=access_token,
            expires_in=expires_in,
            refresh_token=refresh_token,
            scope=scope,
            token_type=token_type,
            received_at=received_at,
        )
    except Exception as exc:
        logging.getLogger('spotify').debug("Could not load persisted token: %s", exc)
        return None


def reset_spotify_auth_state(clear_persisted_token: bool = True) -> None:
    """Invalidate in-memory token cache and optionally remove persisted token state."""
    invalidate_token_cache()
    if not clear_persisted_token:
        return

    try:
        if TOKEN_STATE_PATH.exists():
            TOKEN_STATE_PATH.unlink()
    except OSError:
        logging.getLogger('spotify').debug("token.state.remove_failed")

# 🔑 Token functions
_TOKEN_ENDPOINT = "https://accounts.spotify.com/api/token"


def _refresh_access_token_impl(with_retries: bool = True) -> Optional[TokenResponse]:
    """Internal helper performing the token refresh with retries and persistence."""
    logger = logging.getLogger('spotify')

    if os.getenv("SPOTIPI_OFFLINE") == "1":
        logger.debug("token.refresh.skip_offline")
        return None

    credentials = _resolve_runtime_credentials(use_cache=True)
    client_id = credentials.get("client_id", "")
    client_secret = credentials.get("client_secret", "")
    refresh_token = credentials.get("refresh_token", "")

    if not all([client_id, client_secret, refresh_token]):
        raise RuntimeError("Spotify credentials missing for token refresh")

    attempts = 0
    last_exc: Optional[Exception] = None

    while True:
        attempts += 1
        start = time.perf_counter()

        try:
            with _REQUEST_SEMAPHORE:
                response = SESSION.post(
                    _TOKEN_ENDPOINT,
                    data={"grant_type": "refresh_token", "refresh_token": refresh_token},
                    auth=(client_id, client_secret),
                    timeout=SPOTIFY_API_TIMEOUT,
                )

            elapsed = time.perf_counter() - start
            status = response.status_code

            if status in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"retryable status {status}", response=response)

            response.raise_for_status()
            payload = response.json()
            token = payload.get("access_token")
            if not token:
                raise RuntimeError("Token response missing access_token")

            expires_in = int(payload.get("expires_in", 3600))

            token_response = TokenResponse(
                access_token=token,
                expires_in=expires_in,
                refresh_token=payload.get("refresh_token"),
                scope=payload.get("scope"),
                token_type=payload.get("token_type"),
                received_at=time.time(),
            )

            persist_payload = dict(payload)
            persist_payload["received_at"] = token_response.received_at
            persist_payload["expires_at"] = token_response.received_at + expires_in
            if refresh_token and "refresh_token" not in persist_payload:
                persist_payload["refresh_token"] = refresh_token

            _save_token_atomically(persist_payload)

            logger.info(
                "token.refresh.ok",
                extra={"attempts": attempts, "elapsed": round(elapsed, 3)},
            )
            return token_response

        except (requests.ReadTimeout, requests.ConnectionError) as exc:
            last_exc = exc
            elapsed = time.perf_counter() - start
            logger.warning(
                "token.refresh.retry",
                extra={
                    "attempt": attempts,
                    "elapsed": round(elapsed, 3),
                    "reason": exc.__class__.__name__,
                },
            )

        except requests.HTTPError as exc:
            last_exc = exc
            status_code = exc.response.status_code if exc.response is not None else None
            elapsed = time.perf_counter() - start
            retryable = status_code in (429, 500, 502, 503, 504)
            level = logging.WARNING if retryable else logging.ERROR
            logger.log(
                level,
                "token.refresh.http_error",
                extra={
                    "attempt": attempts,
                    "status": status_code,
                    "elapsed": round(elapsed, 3),
                },
            )
            if not retryable:
                raise

        except (ValueError, json.JSONDecodeError, RuntimeError) as exc:
            last_exc = exc
            logger.error(
                "token.refresh.parse_error",
                extra={"attempt": attempts, "cause": str(exc)},
            )
            raise

        if not with_retries or attempts >= TOKEN_REFRESH_MAX_ATTEMPTS:
            break

        delay = _compute_backoff(TOKEN_REFRESH_BACKOFF_BASE, attempts, TOKEN_REFRESH_BACKOFF_JITTER)
        time.sleep(delay)

    if last_exc:
        logger.error(
            "token.refresh.fail",
            extra={"attempts": attempts, "error": str(last_exc)},
        )
        raise last_exc

    raise RuntimeError("Token refresh failed without specific error")


def refresh_access_token(with_retries: bool = True) -> Optional[str]:
    """Public wrapper returning the raw token string for legacy callers."""
    try:
        token_response = _refresh_access_token_impl(with_retries=with_retries)
        return token_response.access_token if token_response else None
    except Exception as exc:
        logging.getLogger('spotify').debug("token.refresh.wrapper_failure: %s", exc)
        return None

# 🎟️ Cached token functions
def get_access_token() -> Optional[str]:
    """Get a valid access token using the cache system.
    
    Returns:
        Optional[str]: Valid access token or None if unavailable
    """
    return get_cached_token()

def force_refresh_token() -> Optional[str]:
    """Force refresh the access token bypassing cache.
    
    Returns:
        Optional[str]: New access token or None if refresh fails
    """
    return force_token_refresh()


def ensure_token_valid(min_ttl: int = TOKEN_SAFETY_WINDOW) -> Optional[str]:
    """Ensure the cached token remains valid for at least ``min_ttl`` seconds."""
    window = max(30, min_ttl)
    try:
        if token_needs_refresh(window):
            logging.getLogger('spotify').info(
                "token.refresh.prewarm",
                extra={"window": window},
            )
        return cache_ensure_token_valid(window)
    except Exception as exc:
        logging.getLogger('spotify').error(
            "token.refresh.ensure_failed",
            extra={"error": str(exc)},
        )
        return None

# Initialize token cache when module is imported
initialize_token_cache(lambda: _refresh_access_token_impl(with_retries=True))
_persisted = _load_persisted_token()
if _persisted:
    seed_token_cache(_persisted)
    logging.getLogger('spotify').debug(
        "token.cache.seeded",
        extra={"expires_in": _persisted.expires_in, "received_at": _persisted.received_at},
    )

# Initialize cache migration layer
cache_migration = get_cache_migration_layer()

class _SingleFlight:
    """Track in-flight GET requests to deduplicate identical calls."""

    __slots__ = ("event", "response", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.response: Optional[requests.Response] = None
        self.error: Optional[Exception] = None


def _freeze_value(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze_value(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple, set)):
        return tuple(_freeze_value(v) for v in value)
    return str(value)


def _build_singleflight_key(method: str, url: str, params: Optional[Any]) -> Tuple[str, str, Tuple[Any, ...]]:
    if not params:
        frozen: Tuple[Any, ...] = tuple()
    elif isinstance(params, dict):
        frozen = tuple(sorted((k, _freeze_value(v)) for k, v in params.items()))
    else:
        try:
            frozen = tuple((str(k), _freeze_value(v)) for k, v in params)  # type: ignore[assignment]
        except Exception:
            frozen = (_freeze_value(params),)
    return method.upper(), url, frozen


def _claim_singleflight(key: Tuple[str, str, Tuple[Any, ...]]) -> Tuple[_SingleFlight, bool]:
    with _SINGLE_FLIGHT_LOCK:
        slot = _IN_FLIGHT.get(key)
        if slot:
            return slot, False
        slot = _SingleFlight()
        _IN_FLIGHT[key] = slot
        return slot, True


def _release_singleflight(key: Tuple[str, str, Tuple[Any, ...]]) -> None:
    with _SINGLE_FLIGHT_LOCK:
        _IN_FLIGHT.pop(key, None)


_PLAYBACK_CACHE: Dict[str, Any] | None = None
_PLAYBACK_CACHE_EXPIRY: float = 0.0


def _invalidate_playback_cache() -> None:
    global _PLAYBACK_CACHE, _PLAYBACK_CACHE_EXPIRY
    _PLAYBACK_CACHE = None
    _PLAYBACK_CACHE_EXPIRY = 0.0

_BREAKER = {
    'consecutive_failures': 0,
    'open_until': 0.0,
    'threshold': int(os.getenv('SPOTIPI_BREAKER_THRESHOLD', '3')),
    'cooldown': int(os.getenv('SPOTIPI_BREAKER_COOLDOWN', '30')),
}

def _breaker_open() -> bool:
    return time.time() < _BREAKER['open_until']

def _breaker_on_success() -> None:
    _BREAKER['consecutive_failures'] = 0
    _BREAKER['open_until'] = 0.0

def _breaker_on_failure() -> None:
    _BREAKER['consecutive_failures'] += 1
    if _BREAKER['consecutive_failures'] >= _BREAKER['threshold']:
        _BREAKER['open_until'] = time.time() + _BREAKER['cooldown']

def _spotify_request(
    method: str,
    url: str,
    *,
    headers: Dict[str, str],
    params: Optional[Dict[str, Any]] = None,
    json: Optional[Dict[str, Any]] = None,
    timeout: Optional[Union[int, float, Tuple[float, float]]] = None
) -> requests.Response:
    if _breaker_open():
        raise RuntimeError("Spotify API temporarily unavailable (circuit breaker open)")

    method_upper = method.upper()
    request_timeout = _coerce_timeout(timeout)

    sf_key: Optional[Tuple[str, str, Tuple]] = None
    sf_slot: Optional[_SingleFlight] = None
    is_leader = False

    if method_upper == 'GET':
        sf_key = _build_singleflight_key(method_upper, url, params)
        sf_slot, is_leader = _claim_singleflight(sf_key)
        if not is_leader:
            sf_slot.event.wait()
            if sf_slot.error:
                raise sf_slot.error
            if sf_slot.response is None:
                raise RuntimeError("Single-flight response missing")
            return sf_slot.response

    path_fragment = url.split("/v1/")[-1].split("?")[0]
    metric_label = f"spotify.http.{method_upper.lower()}.{path_fragment.replace('/', '.') or 'root'}"

    request_params: Dict[str, Any] = {
        "method": method_upper,
        "url": url,
        "headers": headers,
        "params": params,
        "json": json,
    }
    if request_timeout is not None:
        request_params["timeout"] = request_timeout

    start = time.perf_counter()

    try:
        with _REQUEST_SEMAPHORE:
            with perf_monitor.time_block(metric_label):
                resp = SESSION.request(**request_params)
        _breaker_on_success()
        if method_upper == 'GET' and is_leader and sf_slot:
            sf_slot.response = resp
        return resp
    except requests.exceptions.RequestException as exc:
        _breaker_on_failure()
        elapsed = time.perf_counter() - start
        logging.getLogger('spotify').warning(
            "spotify.request.error",
            extra={
                "method": method_upper,
                "url": url,
                "elapsed": round(elapsed, 3),
                "error": exc.__class__.__name__,
            },
        )
        if method_upper == 'GET' and is_leader and sf_slot:
            sf_slot.error = exc
        raise
    finally:
        if method_upper == 'GET' and sf_slot and is_leader:
            sf_slot.event.set()
            if sf_key is not None:
                _release_singleflight(sf_key)

# 🎵 Spotify API

def _pick_image_url(images: Any) -> Optional[str]:
    """Select a medium-sized image when possible, otherwise first available."""
    if not isinstance(images, list):
        return None
    for img in images:
        width = img.get("width") if isinstance(img, dict) else None
        if width and 200 <= int(width) <= 400:
            return img.get("url") if isinstance(img, dict) else None
    if images and isinstance(images[0], dict):
        return images[0].get("url")
    return None


def _format_track_item(track: Dict[str, Any]) -> Dict[str, Any]:
    album = track.get("album", {}) if isinstance(track, dict) else {}
    artists = track.get("artists", []) if isinstance(track, dict) else []
    artist_names = [artist.get("name", "") for artist in artists if isinstance(artist, dict)]
    image_url = _pick_image_url(album.get("images", []))
    return {
        "name": track.get("name", "Unknown Song"),
        "artist": ", ".join(name for name in artist_names if name) or "Unknown Artist",
        "uri": track.get("uri", ""),
        "image_url": image_url,
        "track_count": 1,
        "type": "track",
        "album": album.get("name", "") if isinstance(album, dict) else "",
        "duration_ms": track.get("duration_ms", 0),
    }


def _format_queue_item(item: Dict[str, Any]) -> Dict[str, Any]:
    item_type = item.get("type") if isinstance(item, dict) else None
    if item_type == "track":
        return _format_track_item(item)

    if item_type == "episode":
        images = item.get("images", []) if isinstance(item, dict) else []
        show = item.get("show", {}) if isinstance(item, dict) else {}
        return {
            "name": item.get("name", "Unknown Episode"),
            "artist": show.get("name", "Podcast") if isinstance(show, dict) else "Podcast",
            "uri": item.get("uri", ""),
            "image_url": _pick_image_url(images),
            "track_count": 1,
            "type": "episode",
            "duration_ms": item.get("duration_ms", 0),
        }

    return {
        "name": item.get("name", "Unknown Item") if isinstance(item, dict) else "Unknown Item",
        "artist": "",
        "uri": item.get("uri", "") if isinstance(item, dict) else "",
        "image_url": _pick_image_url(item.get("images", [])) if isinstance(item, dict) else None,
        "track_count": 1,
        "type": item_type or "item",
    }


def _raise_scope_error_if_needed(
    response: requests.Response,
    required_scope: str,
    *,
    logger: Optional[logging.Logger] = None,
    context: str = "spotify.request",
) -> None:
    if response.status_code != 403:
        return
    if logger:
        logger.warning("%s.insufficient_scope status=403 body=%s", context, response.text)
    raise SpotifyScopeError(required_scope=required_scope)

def get_user_profile(token: str) -> Optional[Dict[str, Any]]:
    """Fetch the current user's Spotify profile.
    
    Args:
        token: Spotify access token
        
    Returns:
        Optional[Dict[str, Any]]: User profile dict with display_name, email, 
        product (premium/free), images, etc. or None on error.
    """
    logger = logging.getLogger('spotify')
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        r = _spotify_request('GET', "https://api.spotify.com/v1/me", headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            images = data.get("images", [])
            avatar_url = None
            if images:
                # Prefer medium-sized avatar (around 300px)
                for img in images:
                    width = img.get("width")
                    if width and 200 <= width <= 400:
                        avatar_url = img.get("url")
                        break
                if not avatar_url and images:
                    avatar_url = images[0].get("url")
            
            return {
                "id": data.get("id"),
                "display_name": data.get("display_name") or data.get("id"),
                "email": data.get("email"),
                "product": data.get("product"),  # "premium", "free", "open"
                "country": data.get("country"),
                "avatar_url": avatar_url,
                "followers": data.get("followers", {}).get("total", 0),
                "uri": data.get("uri"),
                "external_url": data.get("external_urls", {}).get("spotify"),
            }
        else:
            logger.error(f"❌ Error fetching user profile: {r.status_code} - {r.text}")
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Network error fetching user profile: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Unexpected error fetching user profile: {e}")
        return None


def get_playlists(token: str) -> List[Dict[str, Any]]:
    """Fetch user's playlists from Spotify API.
    
    Args:
        token: Spotify access token
        
    Returns:
        List[Dict[str, Any]]: List of playlist dictionaries
    """
    logger = logging.getLogger('app')  # Use same logger as app.py
    playlists = []
    spotify_username = str(_resolve_runtime_credentials(use_cache=True).get("username", "") or "")
    url = (
        "https://api.spotify.com/v1/me/playlists?limit=50&fields="
        "items(name,uri,images,owner(display_name),tracks(total)),next"
    )
    headers = {"Authorization": f"Bearer {token}"}
    
    while url:
        try:
            r = _spotify_request('GET', url, headers=headers, timeout=10)
            if r.status_code == 200:
                for item in r.json().get("items", []):
                    # Filter out playlists whose name starts with [Felix] (optional, personal setting)
                    playlist_name = item.get("name", "")
                    if playlist_name.startswith("[Felix]"):
                        logger.debug(f"⏭️  Skipping playlist '{playlist_name}' - filtered out (Felix playlist)")
                        continue
                        
                    # Get the best available image (prefer medium size)
                    images = item.get("images", [])
                    image_url = None
                    if images:
                        # Sort by size and pick medium sized image (around 300px)
                        # If no medium, pick the first available
                        for img in images:
                            width = img.get("width")
                            if width and width <= 300 and width >= 200:
                                image_url = img.get("url")
                                break
                        if not image_url and images:
                            image_url = images[0].get("url")
                            
                    # Skip playlists without cover images (usually local files)
                    if not image_url:
                        logger.debug(f"⏭️  Skipping playlist '{item['name']}' - no cover image (likely local files)")
                        continue
                        
                    # Determine creator display name
                    owner_display_name = item.get("owner", {}).get("display_name", "Spotify")
                    if spotify_username and owner_display_name == spotify_username:
                        creator = "Eigene Playlist"
                    elif owner_display_name == "Spotify":
                        creator = "Spotify"
                    else:
                        creator = owner_display_name
                        
                    playlists.append({
                        "name": item["name"], 
                        "artist": creator,  # Playlist creator as artist
                        "uri": item["uri"],
                        "image_url": image_url,
                        "track_count": item.get("tracks", {}).get("total", 0),
                        "type": "playlist"  # Marking as Playlist
                    })
                url = r.json().get("next")
            else:
                logger.error(f"❌ Error fetching playlists: {r.text}")
                break
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Network error fetching playlists: {e}")
            break
        except Exception as e:
            logger.error(f"❌ Unexpected error fetching playlists: {e}")
            break
            
    playlists.sort(key=lambda p: p["name"].lower())
    return playlists

def get_saved_albums(token: str) -> List[Dict[str, Any]]:
    """Fetch the user's saved albums from Spotify.
    
    Args:
        token: Spotify access token
        
    Returns:
        List[Dict[str, Any]]: List of album dictionaries
    """
    logger = logging.getLogger('app')
    albums = []
    url = (
        "https://api.spotify.com/v1/me/albums?limit=50&fields="
        "items(album(name,uri,images,artists(name),total_tracks)),next"
    )
    headers = {"Authorization": f"Bearer {token}"}
    
    while url:
        try:
            r = _spotify_request('GET', url, headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                for item in data.get("items", []):
                    album = item.get("album", {})
                    if album:
                        # Get the best available image (prefer medium size)
                        images = album.get("images", [])
                        image_url = None
                        if images:
                            # Sort by size and pick medium sized image (around 300px)
                            for img in images:
                                width = img.get("width")
                                if width and width <= 300 and width >= 200:
                                    image_url = img.get("url")
                                    break
                            if not image_url and images:
                                image_url = images[0].get("url")
                        
                        # Format artist names
                        artists = album.get("artists", [])
                        artist_names = [artist.get("name", "") for artist in artists]
                        artist_string = ", ".join(artist_names) if artist_names else "Unknown Artist"
                        
                        albums.append({
                            "name": album.get('name', 'Unknown Album'),
                            "artist": artist_string,  # Separate Artist field for UI
                            "uri": album.get("uri", ""),
                            "image_url": image_url,
                            "track_count": album.get("total_tracks", 0),
                            "type": "album"  # Marking as Album
                        })
                
                url = data.get("next")
            else:
                logger.error(f"❌ Error fetching albums: {r.text}")
                break
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Network error fetching albums: {e}")
            break
        except Exception as e:
            logger.error(f"❌ Unexpected error fetching albums: {e}")
            break

    # Sort albums alphabetically
    albums.sort(key=lambda a: a["name"].lower())
    return albums

def get_user_saved_tracks(token: str) -> List[Dict[str, Any]]:
    """Holt die gespeicherten Songs (Liked Songs) des Benutzers von Spotify.
    
    Args:
        token: Spotify access token
        
    Returns:
        List[Dict[str, Any]]: List of saved track dictionaries
    """
    logger = logging.getLogger('app')
    tracks = []
    
    try:
        url = "https://api.spotify.com/v1/me/tracks?limit=50"
        headers = {"Authorization": f"Bearer {token}"}
        
        while url:
            r = _spotify_request('GET', url, headers=headers, timeout=10)
            
            if r.status_code == 200:
                data = r.json()
                for item in data.get("items", []):
                    track = item.get("track", {})
                    if track:
                        # Get the best available image from album
                        album = track.get("album", {})
                        images = album.get("images", [])
                        image_url = None
                        if images:
                            # Prefer medium size (around 300px)
                            for img in images:
                                width = img.get("width")
                                if width and width <= 300 and width >= 200:
                                    image_url = img.get("url")
                                    break
                            if not image_url and images:
                                image_url = images[0].get("url")
                        
                        # Format artist names
                        artists = track.get("artists", [])
                        artist_names = [artist.get("name", "") for artist in artists]
                        artist_string = ", ".join(artist_names) if artist_names else "Unknown Artist"
                        
                        tracks.append({
                            "name": track.get('name', 'Unknown Song'),
                            "artist": artist_string,  # Separate Artist field for UI
                            "uri": track.get("uri", ""),
                            "image_url": image_url,
                            "track_count": 1,  # Single song
                            "type": "track",
                            "album": album.get("name", ""),
                            "duration_ms": track.get("duration_ms", 0)
                        })
                
                url = data.get("next")
            else:
                logger.error(f"❌ Error fetching saved songs: {r.status_code} - {r.text}")
                break
                
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Network error fetching saved songs: {e}")
    except Exception as e:
        logger.error(f"❌ Unexpected error fetching saved songs: {e}")

    # Sort tracks alphabetically
    tracks.sort(key=lambda t: t["name"].lower())
    logger.info(f"💚 Total {len(tracks)} saved songs found")
    return tracks

def get_artist_top_tracks(token: str, artist_id: str, market: str = "US") -> List[Dict[str, Any]]:
    """Get an artist's top tracks.
    
    Args:
        token: Spotify access token
        artist_id: Spotify artist ID (extracted from URI)
        market: Country market (default: US)
        
    Returns:
        List[Dict[str, Any]]: List of top tracks
    """
    logger = logging.getLogger('app')
    url = f"https://api.spotify.com/v1/artists/{artist_id}/top-tracks"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"market": market}
    
    try:
        r = _spotify_request('GET', url, headers=headers, params=params, timeout=SPOTIFY_API_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            tracks = []
            
            for track in data.get("tracks", []):
                # Get image from album
                image_url = None
                images = track.get("album", {}).get("images", [])
                if images:
                    # Prefer medium-sized images (around 300px)
                    for img in images:
                        width = img.get("width")
                        if width and width <= 300 and width >= 200:
                            image_url = img.get("url")
                            break
                    if not image_url:
                        image_url = images[0].get("url")
                
                # Format artists
                artists = track.get("artists", [])
                artist_names = [artist.get("name", "") for artist in artists]
                artist_string = ", ".join(artist_names) if artist_names else "Unknown Artist"
                
                tracks.append({
                    "name": track.get("name", "Unknown Track"),
                    "artist": artist_string,
                    "uri": track.get("uri", ""),
                    "image_url": image_url,
                    "track_count": 1,  # Each track is 1 track
                    "type": "track",
                    "duration_ms": track.get("duration_ms", 0),
                    "popularity": track.get("popularity", 0)
                })
            
            logger.info(f"✅ Artist top tracks loaded: {len(tracks)} tracks")
            return tracks
        else:
            logger.error(f"❌ Error fetching artist top tracks: {r.text}")
            return []
    except Exception as e:
        logger.error(f"❌ Exception fetching artist top tracks: {e}")
        return []


def get_followed_artists(token: str) -> List[Dict[str, Any]]:
    """Fetches the user's followed artists from Spotify.
    
    Args:
        token: Spotify access token
        
    Returns:
        List[Dict[str, Any]]: List of followed artist dictionaries
    """
    logger = logging.getLogger('app')
    artists = []
    url = "https://api.spotify.com/v1/me/following?type=artist&limit=50"
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        while url:
            r = _spotify_request('GET', url, headers=headers, timeout=10)
            if r.status_code == 200:
                data = r.json()
                artists_data = data.get("artists", {})
                
                for item in artists_data.get("items", []):
                    # Get the best available image (prefer medium size)
                    images = item.get("images", [])
                    image_url = None
                    if images:
                        # Sort by size and pick medium sized image (around 300px)
                        for img in images:
                            width = img.get("width")
                            if width and width <= 300 and width >= 200:
                                image_url = img.get("url")
                                break
                        if not image_url and images:
                            image_url = images[0].get("url")
                    
                    # Skip artists without images
                    if not image_url:
                        logger.debug(f"⏭️  Skipping artist '{item['name']}' - no image available")
                        continue
                    
                    artists.append({
                        "name": item.get("name", "Unknown Artist"),
                        "artist": "Artist",
                        "uri": item.get("uri", ""),
                        "artist_id": item.get("id", ""),  # Artist ID for top tracks API
                        "image_url": image_url,
                        "track_count": None,  # Artists have no direct track count
                        "type": "artist"  # Marking as Artist
                    })
                
                # Check for next page
                url = artists_data.get("next")
            else:
                logger.error(f"❌ Error fetching followed artists: {r.status_code} - {r.text}")
                break
                
    except requests.exceptions.RequestException as e:
        logger.error(f"❌ Network error fetching followed artists: {e}")
    except Exception as e:
        logger.error(f"❌ Unexpected error fetching followed artists: {e}")
    
    # Sort artists alphabetically
    artists.sort(key=lambda a: a["name"].lower())
    logger.info(f"🎤 Total {len(artists)} followed artists found")
    return artists


def get_recently_played_tracks(token: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Fetch recently played tracks for the current user."""
    logger = logging.getLogger("app")
    headers = {"Authorization": f"Bearer {token}"}
    capped_limit = max(1, min(int(limit), 50))
    url = f"https://api.spotify.com/v1/me/player/recently-played?limit={capped_limit}"

    try:
        response = _spotify_request("GET", url, headers=headers, timeout=SPOTIFY_API_TIMEOUT)
        _raise_scope_error_if_needed(
            response,
            "user-read-recently-played",
            logger=logger,
            context="spotify.recently_played",
        )
        if response.status_code != 200:
            logger.error("❌ Error fetching recently played tracks: %s - %s", response.status_code, response.text)
            return []

        items = response.json().get("items", [])
        tracks: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            track = item.get("track", {}) if isinstance(item, dict) else {}
            uri = track.get("uri", "")
            if not uri or uri in seen:
                continue
            seen.add(uri)
            track_payload = _format_track_item(track)
            track_payload["type"] = "track"
            tracks.append(track_payload)
        return tracks
    except SpotifyScopeError:
        raise
    except Exception as exc:
        logger.error("❌ Exception fetching recently played tracks: %s", exc)
        return []


def get_user_top_items(
    token: str,
    *,
    item_type: str = "tracks",
    time_range: str = "medium_term",
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Fetch user's top tracks or artists."""
    logger = logging.getLogger("app")
    resolved_type = "artists" if item_type == "artists" else "tracks"
    resolved_range = time_range if time_range in {"short_term", "medium_term", "long_term"} else "medium_term"
    capped_limit = max(1, min(int(limit), 50))
    headers = {"Authorization": f"Bearer {token}"}
    url = (
        f"https://api.spotify.com/v1/me/top/{resolved_type}"
        f"?time_range={resolved_range}&limit={capped_limit}"
    )

    try:
        response = _spotify_request("GET", url, headers=headers, timeout=SPOTIFY_API_TIMEOUT)
        _raise_scope_error_if_needed(
            response,
            "user-top-read",
            logger=logger,
            context="spotify.top_items",
        )
        if response.status_code != 200:
            logger.error("❌ Error fetching top items: %s - %s", response.status_code, response.text)
            return []

        items = response.json().get("items", [])
        if resolved_type == "tracks":
            tracks = [_format_track_item(item) for item in items if isinstance(item, dict)]
            tracks.sort(key=lambda item: item.get("name", "").lower())
            return tracks

        artists: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            artists.append({
                "name": item.get("name", "Unknown Artist"),
                "artist": "Artist",
                "uri": item.get("uri", ""),
                "artist_id": item.get("id", ""),
                "image_url": _pick_image_url(item.get("images", [])),
                "track_count": None,
                "type": "artist",
            })
        artists.sort(key=lambda item: item.get("name", "").lower())
        return artists
    except SpotifyScopeError:
        raise
    except Exception as exc:
        logger.error("❌ Exception fetching top items: %s", exc)
        return []


def get_artist_albums(token: str, artist_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Fetch albums for a given artist."""
    logger = logging.getLogger("app")
    headers = {"Authorization": f"Bearer {token}"}
    capped_limit = max(1, min(int(limit), 50))
    url = (
        f"https://api.spotify.com/v1/artists/{artist_id}/albums"
        f"?include_groups=album,single,compilation&limit={capped_limit}"
    )

    albums: List[Dict[str, Any]] = []
    seen: set[str] = set()
    next_url: Optional[str] = url
    try:
        while next_url:
            response = _spotify_request("GET", next_url, headers=headers, timeout=SPOTIFY_API_TIMEOUT)
            if response.status_code != 200:
                logger.error("❌ Error fetching artist albums: %s - %s", response.status_code, response.text)
                break
            payload = response.json()
            for item in payload.get("items", []):
                if not isinstance(item, dict):
                    continue
                uri = item.get("uri", "")
                if not uri or uri in seen:
                    continue
                seen.add(uri)
                artist_names = [artist.get("name", "") for artist in item.get("artists", []) if isinstance(artist, dict)]
                albums.append({
                    "name": item.get("name", "Unknown Album"),
                    "artist": ", ".join(name for name in artist_names if name) or "Unknown Artist",
                    "uri": uri,
                    "image_url": _pick_image_url(item.get("images", [])),
                    "track_count": item.get("total_tracks", 0),
                    "type": "album",
                })
            next_url = payload.get("next")
    except Exception as exc:
        logger.error("❌ Exception fetching artist albums: %s", exc)
    albums.sort(key=lambda item: item.get("name", "").lower())
    return albums


def search_items(
    token: str,
    query: str,
    *,
    item_types: Union[List[str], Tuple[str, ...], str] = ("track", "album", "artist", "playlist"),
    limit: int = 5,
) -> Dict[str, List[Dict[str, Any]]]:
    """Search Spotify catalog for tracks, albums, artists and playlists."""
    logger = logging.getLogger("app")
    headers = {"Authorization": f"Bearer {token}"}
    allowed = {"track", "album", "artist", "playlist"}
    if isinstance(item_types, str):
        raw_types = [item.strip().lower() for item in item_types.split(",") if item.strip()]
    else:
        raw_types = [str(item).strip().lower() for item in item_types if str(item).strip()]
    selected_types = [item for item in raw_types if item in allowed] or ["track", "album", "artist", "playlist"]
    capped_limit = max(1, min(int(limit), 10))

    params = {
        "q": query,
        "type": ",".join(selected_types),
        "limit": capped_limit,
    }

    output: Dict[str, List[Dict[str, Any]]] = {
        "tracks": [],
        "albums": [],
        "artists": [],
        "playlists": [],
    }

    try:
        response = _spotify_request(
            "GET",
            "https://api.spotify.com/v1/search",
            headers=headers,
            params=params,
            timeout=SPOTIFY_API_TIMEOUT,
        )
        _raise_scope_error_if_needed(
            response,
            "user-read-private",
            logger=logger,
            context="spotify.search",
        )
        if response.status_code != 200:
            logger.error("❌ Error searching Spotify catalog: %s - %s", response.status_code, response.text)
            return output

        payload = response.json()

        for item in payload.get("tracks", {}).get("items", []):
            if isinstance(item, dict):
                track_payload = _format_track_item(item)
                track_payload["type"] = "track"
                output["tracks"].append(track_payload)

        for item in payload.get("albums", {}).get("items", []):
            if not isinstance(item, dict):
                continue
            artist_names = [artist.get("name", "") for artist in item.get("artists", []) if isinstance(artist, dict)]
            output["albums"].append({
                "name": item.get("name", "Unknown Album"),
                "artist": ", ".join(name for name in artist_names if name) or "Unknown Artist",
                "uri": item.get("uri", ""),
                "image_url": _pick_image_url(item.get("images", [])),
                "track_count": item.get("total_tracks", 0),
                "type": "album",
            })

        for item in payload.get("artists", {}).get("items", []):
            if not isinstance(item, dict):
                continue
            output["artists"].append({
                "name": item.get("name", "Unknown Artist"),
                "artist": "Artist",
                "uri": item.get("uri", ""),
                "artist_id": item.get("id", ""),
                "image_url": _pick_image_url(item.get("images", [])),
                "track_count": None,
                "type": "artist",
            })

        for item in payload.get("playlists", {}).get("items", []):
            if not isinstance(item, dict):
                continue
            owner = item.get("owner", {}) if isinstance(item.get("owner"), dict) else {}
            output["playlists"].append({
                "name": item.get("name", "Unknown Playlist"),
                "artist": owner.get("display_name", "Spotify"),
                "uri": item.get("uri", ""),
                "image_url": _pick_image_url(item.get("images", [])),
                "track_count": item.get("tracks", {}).get("total", 0) if isinstance(item.get("tracks"), dict) else 0,
                "type": "playlist",
            })

        return output
    except SpotifyScopeError:
        raise
    except Exception as exc:
        logger.error("❌ Exception searching Spotify catalog: %s", exc)
        return output


def get_playback_queue(token: str) -> Dict[str, Any]:
    """Fetch the current playback queue."""
    logger = logging.getLogger("spotify")
    headers = {"Authorization": f"Bearer {token}"}
    fallback = {"currently_playing": None, "queue": [], "total": 0}
    try:
        response = _spotify_request(
            "GET",
            "https://api.spotify.com/v1/me/player/queue",
            headers=headers,
            timeout=SPOTIFY_API_TIMEOUT,
        )
        _raise_scope_error_if_needed(
            response,
            "user-read-playback-state",
            logger=logger,
            context="spotify.queue",
        )
        if response.status_code != 200:
            logger.warning("spotify.queue.failed status=%s body=%s", response.status_code, response.text)
            return fallback

        data = response.json()
        currently_playing = data.get("currently_playing")
        queue_items = data.get("queue", [])
        return {
            "currently_playing": _format_queue_item(currently_playing) if isinstance(currently_playing, dict) else None,
            "queue": [_format_queue_item(item) for item in queue_items if isinstance(item, dict)],
            "total": len(queue_items) if isinstance(queue_items, list) else 0,
        }
    except SpotifyScopeError:
        raise
    except Exception as exc:
        logger.warning("spotify.queue.exception error=%s", exc)
        return fallback


def get_devices(token: str) -> List[Dict[str, Any]]:
    """Get available Spotify devices with unified caching.
    
    Args:
        token: Spotify access token
        
    Returns:
        List[Dict[str, Any]]: List of available devices
    """
    def load_devices_from_api(token: str) -> List[Dict[str, Any]]:
        """Load devices directly from Spotify API."""
        try:
            with perf_monitor.time_block("spotify.devices.api_call"):
                r = _spotify_request(
                    'GET',
                    "https://api.spotify.com/v1/me/player/devices",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=8
                )
            if r.status_code == 200:
                devices = r.json().get("devices", [])
                # Sort devices alphabetically by name (case-insensitive)
                devices.sort(key=lambda d: (d.get("name") or "").lower())
                # Keep the device-id cache warm so the alarm has a fallback id to
                # wake a device that has momentarily dropped off Spotify Connect.
                _remember_devices_seen(devices)
                return devices
            else:
                logging.getLogger('spotify').error(f"❌ Error fetching devices: {r.text}")
                return []
        except Exception as e:
            logging.getLogger('spotify').exception(f"❌ Exception while fetching devices: {e}")
            return []
    
    # Use unified cache system for devices
    return cache_migration.get_devices_cached(token, load_devices_from_api)


_DEVICE_WHITESPACE_RE = re.compile(r"\s+")
_MAX_CACHED_DEVICES = 8

# Device cache invalidation debouncing (prevent API storms)
_DEVICE_INVALIDATION_COOLDOWN = 30.0  # seconds
_last_device_invalidation: float = 0.0
_device_invalidation_lock = threading.Lock()


def _remember_device_mapping(requested_name: str, actual_name: str, device_id: Optional[str]) -> None:
    if not device_id:
        return
    normalized_key = _normalize_device_name(actual_name or requested_name)
    if not normalized_key:
        return
    try:
        with config_transaction() as transaction:
            cfg = transaction.load()
            cache = cfg.get("last_known_devices")
            if not isinstance(cache, dict):
                cache = {}
            cache[normalized_key] = {
                "id": device_id,
                "name": actual_name or requested_name,
                "requested_name": requested_name,
                "updated_at": time.time(),
            }
            if len(cache) > _MAX_CACHED_DEVICES:
                sorted_items = sorted(
                    cache.items(),
                    key=lambda item: item[1].get("updated_at", 0.0),
                    reverse=True,
                )[:_MAX_CACHED_DEVICES]
                cache = {key: value for key, value in sorted_items}
            cfg["last_known_devices"] = cache
            transaction.save(cfg)
    except Exception as exc:  # pragma: no cover - fallback on best-effort cache
        logging.getLogger('spotify').debug("device.cache.store_failed %s", exc)


def _remember_devices_seen(devices: List[Dict[str, Any]]) -> None:
    """Keep the device-id cache warm from every live device listing.

    ``get_device_id`` only caches ids during the alarm flow (prewarm at T-2min,
    readiness, execute). If the speaker has dropped off Spotify Connect by then,
    nothing is cached and the alarm has no id to wake it (observed: a bedroom
    alarm that never rang because the speaker was absent from the device list and
    the cache was empty). Calling this whenever the app actually fetches the
    device list from Spotify (UI device refresh, dashboard, sleep timer) keeps a
    fresh fallback id for any device the user has recently seen.

    Writes only when a mapping is new or its id changed, so the common steady
    state is a single cached config read with no disk write (Pi SD wear).
    """
    if not devices:
        return
    incoming: Dict[str, Tuple[str, str]] = {}
    for device in devices:
        device_id = device.get("id")
        name = device.get("name")
        key = _normalize_device_name(name)
        if device_id and key:
            incoming[key] = (device_id, name or "")
    if not incoming:
        return
    try:
        current = load_config_safe().get("last_known_devices")
    except Exception:
        current = None
    if not isinstance(current, dict):
        current = {}
    needs_write = any(
        not isinstance(current.get(key), dict) or current[key].get("id") != device_id
        for key, (device_id, _name) in incoming.items()
    )
    if not needs_write:
        return
    try:
        with config_transaction() as transaction:
            cfg = transaction.load()
            cache = cfg.get("last_known_devices")
            if not isinstance(cache, dict):
                cache = {}
            for key, (device_id, name) in incoming.items():
                entry = cache.get(key)
                if isinstance(entry, dict) and entry.get("id") == device_id:
                    continue
                cache[key] = {
                    "id": device_id,
                    "name": name,
                    "requested_name": name,
                    "updated_at": time.time(),
                }
            if len(cache) > _MAX_CACHED_DEVICES:
                sorted_items = sorted(
                    cache.items(),
                    key=lambda item: item[1].get("updated_at", 0.0),
                    reverse=True,
                )[:_MAX_CACHED_DEVICES]
                cache = {key: value for key, value in sorted_items}
            cfg["last_known_devices"] = cache
            transaction.save(cfg)
    except Exception as exc:  # pragma: no cover - best-effort cache
        logging.getLogger('spotify').debug("device.cache.bulk_store_failed %s", exc)


def _load_cached_device_mapping(device_name: str) -> Optional[Dict[str, Any]]:
    normalized_key = _normalize_device_name(device_name)
    if not normalized_key:
        return None
    try:
        cfg = load_config_safe()
    except Exception:
        try:
            cfg = load_config()
        except Exception:
            return None
    cache = cfg.get("last_known_devices")
    if not isinstance(cache, dict):
        return None
    entry = cache.get(normalized_key)
    if isinstance(entry, dict) and entry.get("id"):
        return entry
    return None


def _normalize_device_name(value: Any) -> str:
    """Normalize device names for robust comparison."""
    if not isinstance(value, str):
        return ""
    collapsed = _DEVICE_WHITESPACE_RE.sub(" ", value).strip()
    return collapsed.casefold()


def _pick_device_by_name(
    devices: List[Dict[str, Any]],
    normalized_target: str,
    *,
    allow_partial: bool = False,
) -> Optional[Dict[str, Any]]:
    """Find a device by normalized name, optionally allowing partial matches."""
    for device in devices:
        if _normalize_device_name(device.get("name")) == normalized_target:
            return device

    if not allow_partial or not normalized_target:
        return None

    candidates: List[Tuple[Tuple[int, int], Dict[str, Any]]] = []
    for device in devices:
        normalized_name = _normalize_device_name(device.get("name"))
        if not normalized_name:
            continue
        if normalized_target in normalized_name or normalized_name in normalized_target:
            weight = (
                0 if device.get("is_active") else 1,
                abs(len(normalized_name) - len(normalized_target)),
            )
            candidates.append((weight, device))

    if not candidates:
        return None

    candidates.sort(key=lambda entry: entry[0])
    return candidates[0][1]


def get_device_id(token: str, device_name: str) -> Optional[str]:
    """Get device ID by device name.
    
    Args:
        token: Spotify access token
        device_name: Name of the device to find
        
    Returns:
        Optional[str]: Device ID if found, None otherwise
    """
    global _last_device_invalidation
    
    logger = logging.getLogger('spotify')
    normalized_requested = _normalize_device_name(device_name)
    if not normalized_requested:
        return None

    devices = get_devices(token)
    device = _pick_device_by_name(devices, normalized_requested)

    if device is None:
        # Performance: Debounce cache invalidation to prevent API storms
        # Check if enough time passed since last invalidation
        now = time.time()
        should_invalidate = False
        
        with _device_invalidation_lock:
            if now - _last_device_invalidation >= _DEVICE_INVALIDATION_COOLDOWN:
                should_invalidate = True
                _last_device_invalidation = now
        
        if should_invalidate:
            try:
                cache_migration.invalidate_devices()
                logger.debug(
                    "spotify.device.cache_invalidated",
                    extra={"device": device_name, "cooldown_sec": _DEVICE_INVALIDATION_COOLDOWN}
                )
            except Exception as exc:
                logger.debug(
                    "spotify.device.cache_invalidate_failed",
                    extra={"error": str(exc)},
                )
            devices = get_devices(token)
            device = _pick_device_by_name(devices, normalized_requested)
        else:
            logger.debug(
                "spotify.device.invalidation_cooldown",
                extra={
                    "device": device_name,
                    "seconds_remaining": int(_DEVICE_INVALIDATION_COOLDOWN - (now - _last_device_invalidation))
                }
            )

    if device is None:
        device = _pick_device_by_name(devices, normalized_requested, allow_partial=True)

    if device:
        matched_name = device.get("name", "")
        device_id = device.get("id")
        if not device_id:
            logger.warning(
                "spotify.device.missing_id",
                extra={"requested": device_name, "matched": matched_name},
            )
            return None
        if _normalize_device_name(matched_name) != normalized_requested:
            logger.warning(
                "spotify.device.partial_match",
                extra={"requested": device_name, "matched": matched_name, "device_id": device_id},
            )
        _remember_device_mapping(device_name, matched_name or device_name, device_id)
        return device_id

    cached_entry = _load_cached_device_mapping(device_name)
    if cached_entry:
        cached_id = cached_entry.get("id")
        cached_name = cached_entry.get("name", device_name)
        if cached_id:
            logger.info(
                "spotify.device.cached_id_used",
                extra={"requested": device_name, "cached_name": cached_name, "device_id": cached_id},
            )
            return cached_id

    available_names = [
        d.get("name") for d in devices if isinstance(d.get("name"), str)
    ]
    logger.warning(
        "spotify.device.not_found",
        extra={
            "requested": device_name,
            "available": available_names[:8],
            "total_devices": len(available_names),
        },
    )
    return None

# ▶️ Playback

# Optional observer for the playback start sequence: called as trace(event, data).
# The alarm passes one that forwards to its probe log (the only logger that
# reaches journald on the Pi); everyone else passes None.
PlaybackTrace = Callable[[str, Dict[str, Any]], None]


def _emit_trace(trace: Optional[PlaybackTrace], event: str, data: Dict[str, Any]) -> None:
    """Call the trace observer; diagnostics must never break playback."""
    if trace is None:
        return
    try:
        trace(event, data)
    except Exception as exc:
        logging.getLogger('spotify').debug("playback trace failed: %s", exc)


def _start_playback_on_device(
    token: str,
    device_id: str,
    playlist_uri: str,
    volume_percent: Optional[int],
    shuffle: bool,
    *,
    enforce_volume: bool = False,
    trace: Optional[PlaybackTrace] = None,
) -> bool:
    """Issue the sequence of Spotify API calls to start playback on a device.

    With ``enforce_volume`` the volume is set again right after ``/play``: some
    Connect speakers ignore the preset on an idle device and start the stream
    at their own last volume (a fade-in alarm then blasted for the whole
    verification window before the post-verify reset caught it).
    ``volume_percent=None`` leaves the device volume untouched.
    """
    logger = logging.getLogger('spotify')
    headers = {"Authorization": f"Bearer {token}"}

    try:
        transfer_resp = _spotify_request(
            'PUT',
            "https://api.spotify.com/v1/me/player",
            json={"device_ids": [device_id], "play": False},
            headers=headers,
            timeout=10,
        )
        if transfer_resp.status_code not in (200, 202, 204):
            logger.warning("⚠️ Transfer playback failed: %s", transfer_resp.text)
    except requests.exceptions.RequestException:
        raise

    if volume_percent is not None:
        try:
            if not set_volume(token, volume_percent, device_id):
                logger.warning("⚠️ Could not preset volume to %s%%", volume_percent)
        except Exception as exc:
            logger.warning("⚠️ Error setting volume: %s", exc)

    payload: Dict[str, Any] = {}
    playlist_uri = playlist_uri.strip()
    if playlist_uri:
        if playlist_uri.startswith("spotify:track:"):
            payload["uris"] = [playlist_uri]
            logger.info("▶️ Starting track playback: %s", playlist_uri)
        else:
            payload["context_uri"] = playlist_uri
            if shuffle:
                try:
                    total = 0
                    if playlist_uri.startswith("spotify:playlist:"):
                        total = _get_track_total_cached(token, playlist_uri, 'playlist')
                    elif playlist_uri.startswith("spotify:album:"):
                        total = _get_track_total_cached(token, playlist_uri, 'album')
                    if total > 1:
                        pos = random.randint(0, total - 1)
                        payload["offset"] = {"position": pos}
                        logger.info("🎲 Randomised offset %s/%s", pos, total)
                except Exception as exc:
                    logger.debug("Shuffle offset calculation failed: %s", exc)
            logger.info("▶️ Starting context playback: %s", playlist_uri)
    else:
        logger.info("▶️ Resuming playback without context URI")

    try:
        play_resp = _spotify_request(
            'PUT',
            f"https://api.spotify.com/v1/me/player/play?device_id={device_id}",
            json=payload if payload else None,
            headers=headers,
            timeout=LONG_HTTP_TIMEOUT,
        )
    except requests.exceptions.RequestException:
        raise

    if play_resp.status_code not in (200, 202, 204):
        logger.warning("❌ Error starting playback: %s", play_resp.text)
        _invalidate_playback_cache()
        _emit_trace(trace, "play_failed", {"status": play_resp.status_code})
        return False

    _invalidate_playback_cache()
    _emit_trace(trace, "play_sent", {"status": play_resp.status_code, "volume": volume_percent})
    if enforce_volume and volume_percent is not None:
        try:
            ok = set_volume(token, volume_percent, device_id)
        except Exception as exc:
            logger.debug("Post-play volume enforce failed: %s", exc)
            ok = False
        _emit_trace(trace, "volume_enforced", {"phase": "after_play", "volume": volume_percent, "ok": ok})
    return True


def _set_shuffle_state(token: str, state: bool, *, retries: int = SHUFFLE_RETRY_ATTEMPTS, delay: float = SHUFFLE_RETRY_DELAY) -> bool:
    """Attempt to toggle Spotify shuffle state with small retries."""
    logger = logging.getLogger('spotify')
    headers = {"Authorization": f"Bearer {token}"}
    target = "true" if state else "false"

    for attempt in range(1, retries + 1):
        try:
            resp = _spotify_request(
                'PUT',
                "https://api.spotify.com/v1/me/player/shuffle",
                headers=headers,
                params={"state": target},
                timeout=10,
            )
            if resp.status_code == 204:
                if attempt > 1:
                    logger.debug(
                        "shuffle.state.ok_after_retry",
                        extra={"attempt": attempt},
                    )
                return True
            logger.debug(
                "shuffle.state.failed",
                extra={"status": resp.status_code, "attempt": attempt, "body": resp.text[:120]},
            )
        except requests.exceptions.RequestException as exc:
            logger.debug(
                "shuffle.state.error",
                extra={"error": exc.__class__.__name__, "attempt": attempt},
            )
        if attempt < retries:
            time.sleep(delay)
    return False


def _verify_playback_state(
    token: str,
    expected_device_id: str,
    playlist_uri: str,
    attempts: int = PLAYBACK_VERIFY_ATTEMPTS,
    wait_seconds: float = PLAYBACK_VERIFY_WAIT,
    *,
    expected_volume: Optional[int] = None,
    trace: Optional[PlaybackTrace] = None,
) -> bool:
    """Poll the player API to ensure playback is active on the expected device.

    With ``expected_volume`` every read that shows our device at a different
    volume re-asserts it, so a speaker that restored its own volume on stream
    start is corrected within one poll instead of after verification.
    """
    logger = logging.getLogger('spotify')
    context_uri_expected = playlist_uri if playlist_uri and not playlist_uri.startswith("spotify:track:") else None

    for attempt in range(max(1, attempts)):
        try:
            playback = get_current_playback(token)
        except Exception as exc:
            logger.debug("verify_playback fetch failed: %s", exc)
            playback = None

        if not playback:
            _emit_trace(trace, "verify_read", {"attempt": attempt, "empty": True})

        if playback:
            device_info = playback.get("device") or {}
            active_device_id = device_info.get("id")
            item = playback.get("item") or {}
            item_uri = item.get("uri")
            is_playing = bool(playback.get("is_playing"))
            progress_ms = playback.get("progress_ms") or 0
            device_match = not expected_device_id or active_device_id == expected_device_id
            reported_volume = device_info.get("volume_percent")
            _emit_trace(trace, "verify_read", {
                "attempt": attempt,
                "is_playing": is_playing,
                "volume": reported_volume,
                "device_match": device_match,
                "context_uri": (playback.get("context") or {}).get("uri"),
                "progress_ms": progress_ms,
            })
            if (
                expected_volume is not None
                and device_match
                and reported_volume is not None
                and reported_volume != expected_volume
            ):
                try:
                    ok = set_volume(token, expected_volume, expected_device_id)
                except Exception as exc:
                    logger.debug("verify_playback volume enforce failed: %s", exc)
                    ok = False
                _emit_trace(trace, "volume_enforced", {
                    "phase": "verify",
                    "attempt": attempt,
                    "seen": reported_volume,
                    "volume": expected_volume,
                    "ok": ok,
                })
            if not device_match:
                logger.debug(
                    "Playback active on unexpected device %s (expected %s)",
                    active_device_id,
                    expected_device_id,
                )
            else:
                matched = False
                if context_uri_expected:
                    context_uri = (playback.get("context") or {}).get("uri")
                    if context_uri and context_uri == context_uri_expected:
                        matched = True
                    elif context_uri:
                        logger.debug("Playback context mismatch (got %s)", context_uri)
                elif playlist_uri.startswith("spotify:track:"):
                    matched = item_uri == playlist_uri
                else:
                    matched = bool(item_uri)

                if matched and (is_playing or progress_ms > 0):
                    return True

        if attempt + 1 < attempts:
            time.sleep(wait_seconds)

    return False


def play_with_retry(
    token: str,
    device_id: str,
    playlist_uri: str = "",
    volume_percent: Optional[int] = None,
    shuffle: bool = False,
    *,
    fallback_device: Optional[str] = None,
    enforce_volume: bool = False,
    trace: Optional[PlaybackTrace] = None,
) -> bool:
    """Start playback with retries, verification, and optional fallback device.

    ``volume_percent=None`` (the default) plays at the device's current volume;
    only callers that own a volume (alarm, snooze resume, sleep timer) pass one.
    ``enforce_volume`` re-asserts ``volume_percent`` right after ``/play`` and on
    every verification read that disagrees (see ``_start_playback_on_device``);
    ``trace`` observes the sequence for diagnostics.
    """
    logger = logging.getLogger('spotify')

    if not token or not device_id:
        logger.error("❌ Missing token or device_id for playback")
        return False

    fallback_name = fallback_device or FALLBACK_DEVICE_NAME
    targets: List[Tuple[str, bool]] = [(device_id, False)]
    fallback_attempted = False
    total_attempts = 0
    idx = 0

    while idx < len(targets):
        target_device_id, is_fallback = targets[idx]
        idx += 1

        for attempt in range(1, PLAYER_MAX_ATTEMPTS + 1):
            total_attempts += 1

            refreshed = ensure_token_valid()
            if refreshed:
                token = refreshed

            logger.info(
                "alarm.start.attempt",
                extra={
                    "device": target_device_id,
                    "attempt": attempt,
                    "fallback": is_fallback,
                },
            )

            start_time = time.perf_counter()

            try:
                started = _start_playback_on_device(
                    token,
                    target_device_id,
                    playlist_uri,
                    volume_percent,
                    shuffle,
                    enforce_volume=enforce_volume,
                    trace=trace,
                )
                if not started:
                    raise RuntimeError("playback_start_failed")

                if _verify_playback_state(
                    token,
                    target_device_id,
                    playlist_uri,
                    expected_volume=volume_percent if enforce_volume else None,
                    trace=trace,
                ):
                    elapsed = time.perf_counter() - start_time
                    _emit_trace(trace, "verified", {
                        "device_id": target_device_id,
                        "attempt": attempt,
                        "fallback": is_fallback,
                        "elapsed_s": round(elapsed, 3),
                    })
                    try:
                        if volume_percent is not None and not set_volume(token, volume_percent, target_device_id):
                            logger.debug(
                                "alarm.start.volume_postcheck_failed",
                                extra={"device": target_device_id, "volume": volume_percent},
                            )
                    except Exception as exc:
                        logger.debug(
                            "alarm.start.volume_postcheck_error",
                            extra={"device": target_device_id, "error": str(exc)},
                        )
                    if shuffle:
                        shuffle_ok = _set_shuffle_state(token, True)
                        if not shuffle_ok:
                            logger.debug(
                                "shuffle.state.final_failed",
                                extra={"device": target_device_id},
                            )
                    logger.info(
                        "alarm.start.ok",
                        extra={
                            "attempts": total_attempts,
                            "device": target_device_id,
                            "fallback": is_fallback,
                            "elapsed": round(elapsed, 3),
                        },
                    )
                    return True

                logger.warning(
                    "alarm.start.unverified",
                    extra={
                        "device": target_device_id,
                        "attempt": attempt,
                        "waited_seconds": round(PLAYBACK_VERIFY_ATTEMPTS * PLAYBACK_VERIFY_WAIT, 2),
                    },
                )
                _emit_trace(trace, "unverified", {"device_id": target_device_id, "attempt": attempt})
            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status not in (429, 500, 502, 503, 504):
                    raise
                logger.warning(
                    "alarm.start.retry",
                    extra={
                        "device": target_device_id,
                        "attempt": attempt,
                        "status": status,
                    },
                )
            except requests.exceptions.RequestException as exc:
                logger.warning(
                    "alarm.start.retry",
                    extra={
                        "device": target_device_id,
                        "attempt": attempt,
                        "error": exc.__class__.__name__,
                    },
                )
            except Exception as exc:
                logger.warning(
                    "alarm.start.retry",
                    extra={
                        "device": target_device_id,
                        "attempt": attempt,
                        "error": str(exc),
                    },
                )

            delay = _compute_backoff(PLAYER_BACKOFF_BASE, attempt, PLAYER_BACKOFF_JITTER, cap=10.0)
            time.sleep(delay)

        if not fallback_attempted and fallback_name:
            fallback_attempted = True
            try:
                fallback_id = get_device_id(token, fallback_name)
            except Exception as exc:
                logger.warning(
                    "alarm.start.fallback_lookup_failed",
                    extra={"device": fallback_name, "error": str(exc)},
                )
                fallback_id = None

            existing_ids = {d for d, _ in targets}
            if fallback_id and fallback_id not in existing_ids:
                logger.warning(
                    "alarm.start.fallback_activate",
                    extra={"device": fallback_name, "device_id": fallback_id},
                )
                targets.append((fallback_id, True))

    logger.error(
        "alarm.start.fail",
        extra={
            "attempts": total_attempts,
            "device": device_id,
            "fallback": fallback_name,
        },
    )
    return False


def start_playback(
    token: str,
    device_id: str,
    playlist_uri: str = "",
    volume_percent: Optional[int] = None,
    shuffle: bool = False,
    *,
    enforce_volume: bool = False,
    trace: Optional[PlaybackTrace] = None,
) -> bool:
    """Backward-compatible wrapper that delegates to play_with_retry."""
    return play_with_retry(
        token,
        device_id,
        playlist_uri=playlist_uri,
        volume_percent=volume_percent,
        shuffle=shuffle,
        enforce_volume=enforce_volume,
        trace=trace,
    )

# --- Track count caching for random offset (playlist/album) ---
# Keyed on (uri, kind) only — NOT the access token. The token rotates ~hourly, so
# keying on it made every entry unreachable after a refresh (re-fetching unchanged
# playlist/album metadata each hour) and retained up to 256 stale tokens in memory.
# A TTL keeps the old code's natural ~hourly refresh cadence so a resized playlist/
# album can't keep a stale total indefinitely (a too-large total would feed an
# out-of-range shuffle offset). Bounded LRU; only successful lookups are cached.
_TRACK_TOTAL_CACHE_MAX = 256
_TRACK_TOTAL_TTL = 3600.0  # seconds
_track_total_cache: "OrderedDict[Tuple[str, str], Tuple[int, float]]" = OrderedDict()
_track_total_cache_lock = threading.Lock()


def _get_track_total_cached(token: str, uri: str, kind: str) -> int:
    key = (uri, kind)
    now = time.time()
    with _track_total_cache_lock:
        cached = _track_total_cache.get(key)
        if cached is not None:
            total, fetched_at = cached
            if now - fetched_at < _TRACK_TOTAL_TTL:
                _track_total_cache.move_to_end(key)
                return total
            del _track_total_cache[key]  # expired — re-fetch below
    try:
        if kind == 'playlist':
            playlist_id = uri.split(":")[-1]
            meta_resp = _spotify_request(
                'GET',
                f"https://api.spotify.com/v1/playlists/{playlist_id}?fields=tracks.total",
                headers={"Authorization": f"Bearer {token}"},
                timeout=8
            )
        else:
            album_id = uri.split(":")[-1]
            meta_resp = _spotify_request(
                'GET',
                f"https://api.spotify.com/v1/albums/{album_id}?fields=tracks.total",
                headers={"Authorization": f"Bearer {token}"},
                timeout=8
            )
        if meta_resp.status_code == 200:
            total = meta_resp.json().get('tracks', {}).get('total', 0)
            with _track_total_cache_lock:
                _track_total_cache[key] = (total, time.time())
                _track_total_cache.move_to_end(key)
                while len(_track_total_cache) > _TRACK_TOTAL_CACHE_MAX:
                    _track_total_cache.popitem(last=False)
            return total
    except Exception:
        pass
    return 0

def stop_playback(token: str, device_id: Optional[str] = None) -> bool:
    """Stop Spotify playback.
    
    Args:
        token: Spotify access token
        
    Returns:
        bool: True if successful, False otherwise
    """
    logger = logging.getLogger('spotify')
    try:
        params: Dict[str, Any] = {}
        if device_id:
            params["device_id"] = device_id

        response = _spotify_request(
            'PUT',
            "https://api.spotify.com/v1/me/player/pause",
            headers={"Authorization": f"Bearer {token}"},
            params=params if params else None,
            timeout=DEFAULT_HTTP_TIMEOUT
        )
    except requests.exceptions.RequestException as exc:
        logger.error(
            "spotify.playback.stop.request_error",
            extra={"error": str(exc)},
        )
        if getattr(exc, "response", None) is not None and exc.response.status_code == 401:  # type: ignore[attr-defined]
            invalidate_token_cache()
        return False
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.exception("spotify.playback.stop.unexpected", extra={"error": str(exc)})
        return False
    finally:
        _invalidate_playback_cache()

    if 200 <= response.status_code < 300:
        return True

    if response.status_code == 401:
        invalidate_token_cache()

    logger.warning(
        "spotify.playback.stop.failed status=%s body=%s",
        response.status_code,
        response.text[:200],
    )
    return False

def resume_playback(token: str, device_id: Optional[str] = None) -> bool:
    """Resume/start playback.
    
    Args:
        token: Spotify access token
        
    Returns:
        bool: True if successful, False otherwise
    """
    logger = logging.getLogger('spotify')
    try:
        params: Dict[str, Any] = {}
        if device_id:
            params["device_id"] = device_id

        response = _spotify_request(
            'PUT',
            "https://api.spotify.com/v1/me/player/play",
            headers={"Authorization": f"Bearer {token}"},
            params=params if params else None,
            timeout=DEFAULT_HTTP_TIMEOUT
        )
    except requests.exceptions.RequestException as exc:
        logger.error(
            "spotify.playback.resume.request_error",
            extra={"error": str(exc)},
        )
        if getattr(exc, "response", None) is not None and exc.response.status_code == 401:  # type: ignore[attr-defined]
            invalidate_token_cache()
        return False
    except Exception as exc:  # pragma: no cover
        logger.exception("spotify.playback.resume.unexpected", extra={"error": str(exc)})
        return False
    finally:
        _invalidate_playback_cache()

    if 200 <= response.status_code < 300:
        return True

    if response.status_code == 401:
        invalidate_token_cache()

    logger.warning(
        "spotify.playback.resume.failed status=%s body=%s",
        response.status_code,
        response.text[:200],
    )
    return False

def toggle_playback(token: str) -> Dict[str, Union[bool, str]]:
    """Toggle between play and pause.
    
    Args:
        token: Spotify access token
        
    Returns:
        Dict[str, Union[bool, str]]: Result dictionary with action and success status
    """
    logger = logging.getLogger('spotify')
    try:
        playback = get_current_playback(token)
    except Exception as exc:
        logger.error("spotify.playback.toggle.status_failed", extra={"error": str(exc)})
        return {"success": False, "error": str(exc)}

    device_id = None
    if playback and isinstance(playback, dict):
        device = playback.get("device")
        if isinstance(device, dict):
            device_id = device.get("id")

    try:
        if playback and playback.get("is_playing", False):
            success = stop_playback(token, device_id=device_id)
            return {"action": "paused", "success": success}
        success = resume_playback(token, device_id=device_id)
        return {"action": "playing", "success": success}
    except Exception as exc:  # pragma: no cover
        logger.exception("spotify.playback.toggle.unexpected", extra={"error": str(exc)})
        return {"success": False, "error": str(exc)}


def toggle_playback_fast(token: str) -> Dict[str, Union[bool, str]]:
    """Fast toggle - tries pause first, then play if that fails.
    Optimized for immediate response without status check.
    
    Args:
        token: Spotify access token
        
    Returns:
        Dict[str, Union[bool, str]]: Result dictionary with action and success status
    """
    return toggle_playback(token)


def skip_to_next(token: str) -> Dict[str, Union[bool, str]]:
    """Skip to next track in Spotify playback.
    
    Args:
        token: Spotify access token
        
    Returns:
        Dict[str, Union[bool, str]]: Result dictionary with success status
    """
    logger = logging.getLogger('spotify')
    try:
        response = _spotify_request(
            'POST',
            "https://api.spotify.com/v1/me/player/next",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        
        # 204 No Content = success
        if response.status_code in (200, 204):
            logger.info("spotify.playback.skip_next.success")
            return {"success": True, "action": "next"}
        
        if response.status_code == 401:
            invalidate_token_cache()
            return {"success": False, "error": "auth_required"}
        
        if response.status_code == 404:
            return {"success": False, "error": "no_active_device"}
        
        logger.warning("spotify.playback.skip_next.failed", extra={"status": response.status_code})
        return {"success": False, "error": f"skip_failed_{response.status_code}"}
        
    except requests.exceptions.RequestException as exc:
        logger.warning("spotify.playback.skip_next.request_error", extra={"error": str(exc)})
        return {"success": False, "error": str(exc)}
    except Exception as exc:
        logger.exception("spotify.playback.skip_next.unexpected", extra={"error": str(exc)})
        return {"success": False, "error": str(exc)}


def skip_to_previous(token: str) -> Dict[str, Union[bool, str]]:
    """Skip to previous track in Spotify playback.
    
    Args:
        token: Spotify access token
        
    Returns:
        Dict[str, Union[bool, str]]: Result dictionary with success status
    """
    logger = logging.getLogger('spotify')
    try:
        response = _spotify_request(
            'POST',
            "https://api.spotify.com/v1/me/player/previous",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        
        # 204 No Content = success
        if response.status_code in (200, 204):
            logger.info("spotify.playback.skip_previous.success")
            return {"success": True, "action": "previous"}
        
        if response.status_code == 401:
            invalidate_token_cache()
            return {"success": False, "error": "auth_required"}
        
        if response.status_code == 404:
            return {"success": False, "error": "no_active_device"}
        
        logger.warning("spotify.playback.skip_previous.failed", extra={"status": response.status_code})
        return {"success": False, "error": f"skip_failed_{response.status_code}"}
        
    except requests.exceptions.RequestException as exc:
        logger.warning("spotify.playback.skip_previous.request_error", extra={"error": str(exc)})
        return {"success": False, "error": str(exc)}
    except Exception as exc:
        logger.exception("spotify.playback.skip_previous.unexpected", extra={"error": str(exc)})
        return {"success": False, "error": str(exc)}


#  Exportable functions - Updated for new config system
__all__ = [
    "refresh_access_token", "ensure_token_valid", "get_access_token", "force_refresh_token",
    "get_playlists", "get_devices", "get_device_id",
    "start_playback", "play_with_retry", "stop_playback", "resume_playback", "toggle_playback",
    "get_current_playback", "get_current_track", "get_current_spotify_volume",
    "get_saved_albums", "get_user_saved_tracks", "get_followed_artists",
    "get_recently_played_tracks", "get_user_top_items", "get_artist_albums", "search_items",
    "get_playback_queue", "SpotifyScopeError",
    "set_volume", "get_playback_status", "get_user_library", "get_combined_playback",
    "load_music_library_parallel", "spotify_network_health", "get_user_profile"
]

def get_current_spotify_volume(token: str) -> int:
    """Get current Spotify volume.
    
    Args:
        token: Spotify access token
        
    Returns:
        int: Current volume percentage (0-100), defaults to 50
    """
    logger = logging.getLogger('spotify')
    try:
        response = _spotify_request(
            'GET',
            "https://api.spotify.com/v1/me/player",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
    except requests.exceptions.RequestException as exc:
        logger.warning("spotify.volume.request_error", extra={"error": str(exc)})
        if getattr(exc, "response", None) is not None and exc.response.status_code == 401:  # type: ignore[attr-defined]
            invalidate_token_cache()
        return 50
    except Exception as exc:  # pragma: no cover
        logger.exception("spotify.volume.unexpected", extra={"error": str(exc)})
        return 50

    if response.status_code == 200:
        try:
            data = response.json()
            return int(data.get("device", {}).get("volume_percent", 50))
        except (ValueError, TypeError):
            logger.debug("spotify.volume.invalid_payload")
            return 50

    if response.status_code == 401:
        invalidate_token_cache()

    logger.debug(
        "spotify.volume.fallback",
        extra={"status": response.status_code, "body_preview": response.text[:200]},
    )
    return 50

def get_current_playback(token: str, device_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retrieves the current playback status.
    
    Args:
        token: Spotify access token
        device_id: Optional device ID to query
        
    Returns:
        Optional[Dict[str, Any]]: Playback status JSON or None if no active player
        
    Raises:
        RuntimeError: If API returns an error status
    """
    params = {}
    if device_id:
        params["device_id"] = device_id
        
    try:
        r = _spotify_request(
            'GET',
            "https://api.spotify.com/v1/me/player",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        if r.status_code == 200:
            return r.json()
        elif r.status_code == 204:
            # 204 No Content - no active player, this is normal
            return None
        else:
            raise RuntimeError(f"Error retrieving playback status: {r.status_code} - {r.text}")
    except requests.exceptions.RequestException as e:
        # Let callers turn this into a 503 so UI can degrade gracefully
        raise RuntimeError(f"Network error retrieving playback status: {e}")

def get_current_track(token: str) -> Optional[Dict[str, Any]]:
    """Get current playing track information for Now Playing display.
    
    Args:
        token: Spotify access token
        
    Returns:
        Optional[Dict[str, Any]]: Track info with name, artist, album, image or None
    """
    try:
        playback = get_current_playback(token)
        if not playback or not playback.get("item"):
            return None
            
        track = playback["item"]
        artists = track.get("artists", [])
        album = track.get("album", {})
        images = album.get("images", [])
        
        return {
            "name": track.get("name"),
            "artist": ", ".join([artist.get("name", "") for artist in artists]),
            "album": album.get("name"),
            "album_image": images[0]["url"] if images else None,
            "is_playing": playback.get("is_playing", False),
            "uri": track.get("uri")
        }
    except Exception as e:
        logging.error(f"Error getting current track: {e}")
        return None

def _playback_cache_ttl() -> float:
    """Get playback cache TTL with adaptive defaults.
    
    Longer TTL on Pi Zero W to reduce API calls - playback state doesn't 
    change frequently when track is playing (only on skip/pause/volume change).
    """
    default_ttl = 5.0 if _LOW_POWER_MODE else 1.5
    ttl_env = os.getenv('SPOTIPI_PLAYBACK_CACHE_TTL', str(default_ttl))
    try:
        ttl = float(ttl_env)
    except (TypeError, ValueError):
        ttl = default_ttl
    return max(0.0, ttl)


def get_combined_playback(token: str, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
    """Single-call helper returning playback + simplified track + volume.

    Avoids making three separate requests (status + track + volume) since
    Spotify's /me/player already contains everything needed.
    """
    global _PLAYBACK_CACHE, _PLAYBACK_CACHE_EXPIRY

    ttl = _playback_cache_ttl()
    now = time.time()

    if not force_refresh and ttl > 0 and _PLAYBACK_CACHE and now < _PLAYBACK_CACHE_EXPIRY:
        return copy.deepcopy(_PLAYBACK_CACHE)

    try:
        playback = get_current_playback(token)
        if not playback:
            return None
        item = playback.get("item")
        track = None
        if item:
            album = item.get("album", {})
            images = album.get("images", [])
            artists = item.get("artists", [])
            track = {
                "name": item.get("name"),
                "artist": ", ".join([a.get("name", "") for a in artists]),
                "album": album.get("name"),
                "album_image": images[0]["url"] if images else None,
                "is_playing": playback.get("is_playing", False),
                "uri": item.get("uri")
            }
        volume = int(playback.get("device", {}).get("volume_percent", 50))
        combined = {
            "is_playing": playback.get("is_playing", False),
            "device": playback.get("device"),
            "progress_ms": playback.get("progress_ms"),
            "shuffle_state": playback.get("shuffle_state"),
            "repeat_state": playback.get("repeat_state"),
            "current_track": track,
            "volume": volume
        }
        if ttl > 0:
            _PLAYBACK_CACHE = copy.deepcopy(combined)
            _PLAYBACK_CACHE_EXPIRY = now + ttl
        return combined
    except Exception as e:
        logging.getLogger('spotify').debug(f"Combined playback fetch failed: {e}")
        return None

def load_music_library_parallel(token: str) -> Dict[str, Any]:
    """Load playlists, albums, tracks, and artists in parallel for better performance.
    
    Args:
        token: Spotify access token
        
    Returns:
        Dict[str, Any]: Dictionary containing all music library data
    """
    logger = logging.getLogger('app')  # Use same logger as app.py
    start_time = time.time()
    logger.info("🚀 Starting parallel music library loading...")
    
    def load_with_error_handling(func, name: str) -> List[Dict[str, Any]]:
        """Helper function to load data with error handling.
        
        Args:
            func: Function to call for loading data
            name: Name for logging purposes
            
        Returns:
            List[Dict[str, Any]]: Loaded data or empty list on error
        """
        label = name.lower().replace(' ', '_')
        try:
            with perf_monitor.time_block(f"spotify.library.{label}"):
                result = func(token)
            logger.info(f"✅ {name} loaded: {len(result)} items")
            return result
        except Exception as e:
            logger.error(f"❌ Error loading {name}: {e}")
            return []
    
    section_funcs = {
        'playlists': (get_playlists, "Playlists"),
        'albums': (get_saved_albums, "Albums"),
        'tracks': (get_user_saved_tracks, "Saved Tracks"),
        'artists': (get_followed_artists, "Followed Artists"),
        'recent': (get_recently_played_tracks, "Recently Played"),
        'top': (lambda token: get_user_top_items(token, item_type="tracks", time_range="medium_term"), "Top Tracks"),
    }
    max_workers = max(1, min(len(section_funcs), _get_library_worker_limit()))

    if max_workers == 1:
        results = {
            name: load_with_error_handling(func, label)
            for name, (func, label) in section_funcs.items()
        }
    else:
        # Use timeout to prevent indefinite blocking on Pi Zero W with poor network
        timeout_seconds = float(os.getenv('SPOTIPI_LIBRARY_LOAD_TIMEOUT', '20.0'))
        # Use shared executor instead of creating a new one per call
        executor = _get_library_executor()
        futures = {
            name: executor.submit(load_with_error_handling, func, label)
            for name, (func, label) in section_funcs.items()
        }
        results = {}
        for key, future in futures.items():
            try:
                results[key] = future.result(timeout=timeout_seconds)
            except TimeoutError:
                logger.error(f"❌ {key} timed out after {timeout_seconds}s, using empty fallback")
                results[key] = []
            except Exception as e:
                logger.error(f"❌ {key} failed with error: {e}")
                results[key] = []
    
    end_time = time.time()
    total_items = sum(len(data) for data in results.values())
    logger.info(f"🎉 Parallel loading completed in {end_time - start_time:.2f} seconds - {total_items} total items")
    
    payload = {
        "playlists": results['playlists'],
        "albums": results['albums'], 
        "tracks": results['tracks'],
        "artists": results['artists'],
        "recent": results['recent'],
        "top": results['top'],
        "total": total_items
    }
    payload["hash"] = compute_library_hash(payload)
    return payload

# Additional functions for the new modular app.py

def set_volume(token: str, volume_percent: int, device_id: Optional[str] = None) -> bool:
    """Set Spotify volume.
    
    Args:
        token: Spotify access token
        volume_percent: Volume level (0-100)
        device_id: Optional specific device ID to target
        
    Returns:
        bool: True if successful, False otherwise
    """
    logger = logging.getLogger('spotify')
    try:
        params: Dict[str, Any] = {"volume_percent": int(volume_percent)}
        if device_id:
            params["device_id"] = device_id

        response = _spotify_request(
            'PUT',
            "https://api.spotify.com/v1/me/player/volume",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=10
        )
        
        if response.status_code == 204:
            return True
        logger.warning(
            "spotify.volume.failed",
            extra={
                "status": response.status_code,
                "device_id": device_id,
                "requested_volume": params["volume_percent"],
            },
        )
        if response.status_code == 404 and device_id:
            try:
                cache_migration.invalidate_devices()
            except Exception as exc:
                logger.debug(
                    "spotify.device.cache_invalidate_failed",
                    extra={"error": str(exc)},
                )
        return False
    except Exception as exc:
        logger.warning(
            "spotify.volume.exception",
            extra={"error": str(exc), "device_id": device_id},
        )
        return False

def get_playback_status(token: str) -> Optional[Dict[str, Any]]:
    """Get current playback status.
    
    Args:
        token: Spotify access token
        
    Returns:
        Optional[Dict[str, Any]]: Current playback status or None
    """
    return get_current_playback(token)

def get_user_library(token: str) -> Dict[str, Any]:
    """Get comprehensive user library.
    
    Args:
        token: Spotify access token
        
    Returns:
        Dict[str, Any]: Complete user library data
    """
    return load_music_library_parallel(token)

def spotify_network_health() -> Dict[str, Any]:
    """Basic connectivity diagnostics to Spotify API.

    Returns:
        Dict with ok flag, DNS/IPs, TLS reachability, and error info.
    """
    info: Dict[str, Any] = {
        "ok": False,
        "dns": {},
        "tls": {},
    }

    host = "api.spotify.com"

    # DNS resolution
    try:
        addrs = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        ips = sorted({ai[4][0] for ai in addrs})
        info["dns"] = {"resolved": True, "ips": ips}
    except Exception as e:
        info["dns"] = {"resolved": False, "error": str(e)}

    # TLS reachability (HEAD to a harmless endpoint)
    try:
        with _REQUEST_SEMAPHORE:
            r = SESSION.head(
                "https://api.spotify.com/v1",
                timeout=_coerce_timeout(5)
            )
        info["tls"] = {
            "reachable": True,
            "status": r.status_code,
        }
        info["ok"] = True
    except requests.exceptions.SSLError as e:
        info["tls"] = {"reachable": False, "type": "SSLError", "error": str(e)}
    except requests.exceptions.RequestException as e:
        info["tls"] = {"reachable": False, "type": "RequestException", "error": str(e)}

    return info
