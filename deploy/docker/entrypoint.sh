#!/bin/sh
# Entrypoint for the SpotiPi image (Dockerfile). Checks the volumes, then runs the command from CMD.
set -eu

uid="$(id -u)"
gid="$(id -g)"

# Volumes that Docker creates as root on first start are not writable for UID 1000.
# Fail with a clear message instead of a PermissionError somewhere during startup.
for dir in /app/config /app/cache "$HOME/.spotipi"; do
  if [ ! -w "$dir" ]; then
    echo "$dir is not writable for UID $uid: run 'sudo chown -R $uid:$gid data/' on the host" >&2
    exit 1
  fi
done

# default_config.json belongs to the image, not to the user data: production.json is a sparse override
# on top of it (src/config.py). The config volume hides the file, so copy it fresh on every start.
cp /app/config.dist/default_config.json /app/config/default_config.json

exec "$@"
