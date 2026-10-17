# SpotiPi as a container (e.g. Raspberry Pi 5, arm64; also builds on amd64).
# Start, volumes and hardening: compose.yaml. Guide: Readme.MD, section "Docker Setup (Raspberry Pi 5)".
# The systemd installation (deploy/systemd/, deploy/install.sh) is untouched by this.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Runtime files only. static/dist/ is committed, so no Node inside the image (AGENTS.md).
COPY run.py .
COPY src/ src/
COPY static/ static/
COPY templates/ templates/
COPY scripts/run_alarm.sh scripts/
# Shipped defaults kept apart from the config volume; the entrypoint copies them into config/ on start
COPY config/default_config.json config.dist/default_config.json
COPY deploy/docker/entrypoint.sh /usr/local/bin/spotipi-entrypoint

# Pre-compile bytecode: the filesystem is read-only at runtime (compose.yaml: read_only)
RUN python -m compileall -q src run.py

# Dedicated user instead of root. UID 1000 = first user on Raspberry Pi OS, who owns data/ on the Pi.
# HOME and USER are fixed: ~/.spotipi/ is the canonical secrets path, and the key for
# spotify_token.json is derived from USER and the hostname among others (src/utils/token_encryption.py).
RUN groupadd --gid 1000 spotipi \
    && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/spotipi --shell /usr/sbin/nologin spotipi \
    && mkdir -p config cache /home/spotipi/.spotipi \
    && chown -R spotipi:spotipi config cache /home/spotipi \
    && chmod 700 /home/spotipi/.spotipi \
    && chmod +x /usr/local/bin/spotipi-entrypoint
USER spotipi

# Production mode explicitly: Pi detection checks for "arm*", but arm64 reports "aarch64".
ENV HOME=/home/spotipi \
    USER=spotipi \
    SPOTIPI_ENV=production \
    SPOTIPI_RASPBERRY_PI=1 \
    PORT=5000

# Port inside the container is fixed; change the host side in compose.yaml (ports)
EXPOSE 5000
# Start phase: probed every 5 s (Docker 25+), afterwards every 2 minutes
HEALTHCHECK --interval=2m --timeout=5s --start-period=30s --start-interval=5s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ.get('PORT', '5000')}/healthz\", timeout=3)"]

ENTRYPOINT ["spotipi-entrypoint"]
CMD ["python", "run.py"]
