"""Keep the Docker deployment files (Dockerfile, compose.yaml, entrypoint) consistent.

No container is built here: the test only checks the contracts the files share with
each other and with the app (port, health endpoint, writable paths, shipped defaults).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is not a runtime dependency
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE = ROOT / "compose.yaml"
ENTRYPOINT = ROOT / "deploy" / "docker" / "entrypoint.sh"
ENV_EXAMPLE = ROOT / "spotipi.env.example"
DOCKERIGNORE = ROOT / ".dockerignore"

# Paths the app writes to at runtime; each needs a volume in compose.yaml.
RUNTIME_WRITE_PATHS = ("/app/config", "/app/cache", "/home/spotipi/.spotipi")


def _compose() -> dict:
    if yaml is None:
        pytest.skip("PyYAML not installed")
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_docker_files_exist():
    for path in (DOCKERFILE, COMPOSE, ENTRYPOINT, ENV_EXAMPLE, DOCKERIGNORE):
        assert path.is_file(), f"missing {path.relative_to(ROOT)}"


def test_entrypoint_is_executable_and_seeds_defaults():
    assert os.access(ENTRYPOINT, os.X_OK), "entrypoint.sh must be executable (git mode 100755)"
    text = ENTRYPOINT.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh")
    # default_config.json is shipped with the image and hidden by the config volume
    assert "config.dist/default_config.json" in text
    assert "/app/config/default_config.json" in text
    for path in RUNTIME_WRITE_PATHS:
        expected = path.replace("/home/spotipi", "$HOME")
        assert expected in text, f"entrypoint must check write access to {path}"
    assert text.rstrip().endswith('exec "$@"')


def test_dockerfile_contract():
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "EXPOSE 5000" in text
    assert "PORT=5000" in text
    assert "/healthz" in text, "healthcheck must hit the /healthz route"
    assert "USER spotipi" in text, "app must not run as root"
    assert "SPOTIPI_ENV=production" in text
    assert "SPOTIPI_RASPBERRY_PI=1" in text, "arm64 reports aarch64, Pi detection must be forced"
    assert 'ENTRYPOINT ["spotipi-entrypoint"]' in text
    assert "COPY config/default_config.json config.dist/default_config.json" in text
    assert "COPY static/ static/" in text, "committed static/dist bundle is the runtime frontend"
    assert "npm" not in text, "no frontend build inside the image (AGENTS.md)"


def test_dockerignore_lets_runtime_files_through():
    text = DOCKERIGNORE.read_text(encoding="utf-8")
    for needed in (
        "!requirements.txt",
        "!run.py",
        "!src/",
        "!static/",
        "!templates/",
        "!config/default_config.json",
        "!deploy/docker/entrypoint.sh",
    ):
        assert needed in text, f".dockerignore must allow {needed[1:]}"


def test_compose_service_contract():
    compose = _compose()
    service = compose["services"]["spotipi"]

    assert service["build"] == "."
    assert service["hostname"] == "spotipi", "token key derivation needs a stable hostname"
    assert service["env_file"] == "spotipi.env"
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert service["tmpfs"] == "/tmp", "logs and sleep/snooze status live under /tmp in production"
    assert service["restart"] == "unless-stopped"

    container_ports = {mapping.split(":")[-1] for mapping in service["ports"]}
    assert container_ports == {"5000"}, "container port must match EXPOSE/PORT in the Dockerfile"

    targets = {volume.split(":")[1] for volume in service["volumes"]}
    for path in RUNTIME_WRITE_PATHS:
        assert path in targets, f"{path} is written at runtime and needs a volume"


def test_env_example_covers_required_keys():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    active_keys = set(re.findall(r"^([A-Z_]+)=", text, flags=re.MULTILINE))
    assert "FLASK_SECRET_KEY" in active_keys, "without it sessions reset on every restart"
    assert "SPOTIPI_TIMEZONE" in active_keys
    for secret in ("SPOTIFY_CLIENT_SECRET", "SPOTIFY_REFRESH_TOKEN", "SPOTIPI_ADMIN_PASSWORD"):
        assert secret not in active_keys, f"{secret} must stay commented out in the template"
