"""Browser errors must render a valid shell without consulting failed services."""
import json
import re
from pathlib import Path

import pytest
from flask import Flask, abort

from src.routes.errors import register_error_handlers


@pytest.fixture
def error_client(monkeypatch):
    from src.routes import errors, main

    def unavailable(*args, **kwargs):
        raise RuntimeError("Service unavailable")

    monkeypatch.setattr(errors, "load_config", unavailable)
    monkeypatch.setattr(main, "get_service", unavailable)
    root = Path(__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(root / "templates"))
    app.config['TESTING'] = True
    register_error_handlers(app)

    @app.route('/broken')
    @app.route('/api/broken')
    def broken():
        abort(500)

    return app.test_client()


@pytest.mark.parametrize('path,status', [('/favicon.ico', 404), ('/missing', 404), ('/broken', 500)])
def test_browser_error_has_valid_bootstrap(error_client, path, status):
    response = error_client.get(path)
    assert response.status_code == status
    match = re.search(r'<script id="spotipi-bootstrap" type="application/json">(.*?)</script>', response.text)
    assert match
    bootstrap = json.loads(match.group(1))
    assert bootstrap['app']['initial_surface'] == 'home'
    assert bootstrap['notifications'][0]['type'] == 'error'
    assert bootstrap['notifications'][0]['message']
    assert isinstance(bootstrap['dashboard']['devices'], list)
    assert 'feature_flags' in bootstrap['settings']
    assert 'duration' in bootstrap['sleep_defaults']


@pytest.mark.parametrize('path,status,code', [('/api/missing', 404, 'not_found'), ('/api/broken', 500, 'internal_error')])
def test_api_errors_keep_json_envelope(error_client, path, status, code):
    response = error_client.get(path)
    assert response.status_code == status
    assert response.json['success'] is False
    assert response.json['error_code'] == code
