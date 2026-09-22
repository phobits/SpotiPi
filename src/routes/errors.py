"""
🚨 Error Handlers
Centralized HTTP error handling for API and browser routes.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict

from flask import Flask, render_template, request

from ..config import load_config
from ..utils.translations import get_translations, get_user_language, t_api
from ..version import VERSION, get_app_info
from .helpers import api_error


def _build_template_context(config: Dict[str, Any], *, error_message: str) -> Dict[str, Any]:
    user_language = get_user_language(request)
    translations = get_translations(user_language)

    # Error rendering must not call services or snapshots: those may be the
    # source of the original failure. Reuse only the shell's pure payload builders.
    from .main import (
        LOW_POWER_MODE,
        _build_dashboard_payload,
        _build_settings_payload,
        _build_sleep_defaults,
    )

    return {
        "lang": user_language,
        "app_info": get_app_info(),
        "version": VERSION,
        "bootstrap": {
            "language": user_language,
            "translations": translations,
            "low_power": LOW_POWER_MODE,
            "app": {
                "version": VERSION,
                "info": get_app_info(),
                "initial_surface": "home",
                "now_iso": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            },
            "dashboard": _build_dashboard_payload(
                config, {}, {}, None, {}, None, {}, None, {}
            ),
            "settings": _build_settings_payload(config),
            "sleep_defaults": _build_sleep_defaults(config),
            "notifications": [{"type": "error", "message": error_message}],
        },
    }


def register_error_handlers(app: Flask) -> None:
    """Register shared error handlers on the Flask app."""

    @app.errorhandler(404)
    def not_found_error(_error):  # type: ignore[unused-argument]
        if request.path.startswith("/api/") or request.is_json:
            return api_error(
                t_api("page_not_found", request),
                status=404,
                error_code="not_found",
            )
        config = {}
        try:
            config = load_config()
        except Exception:
            config = {}
        context = _build_template_context(config, error_message=t_api("page_not_found", request))
        return render_template("index.html", **context), 404

    @app.errorhandler(500)
    def internal_error(_error):  # type: ignore[unused-argument]
        if request.path.startswith("/api/") or request.is_json:
            return api_error(
                t_api("internal_server_error_page", request),
                status=500,
                error_code="internal_error",
            )
        config = {}
        try:
            config = load_config()
        except Exception:
            config = {}
        context = _build_template_context(config, error_message=t_api("internal_server_error_page", request))
        return render_template("index.html", **context), 500
