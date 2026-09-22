# 🚀 SpotiPi Deployment Guide (Generic)

Comprehensive guide for setting up and managing SpotiPi deployment on Raspberry Pi or Linux servers.

## 📋 Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Complete Setup from Scratch](#complete-setup-from-scratch)
4. [Deployment from the development machine](#deployment-from-the-development-machine)
5. [Service Management](#service-management)
6. [Troubleshooting](#troubleshooting)
7. [Maintenance](#maintenance)

---

## 🏗️ Overview

### Architecture
```
Development Machine         Raspberry Pi / Linux Server
├── repos/spotipi           ├── /home/pi/spotipi          (app, rsync target)
│   ├── scripts/            ├── /home/pi/.spotipi/.env    (runtime secrets)
│   │   └── deploy_to_pi.sh └── systemd: spotipi.service  (Waitress via run.py)
│   └── deploy/systemd/         + spotipi-alarm.timer     (readiness backup)
```

### Deployment Flow
1. **Develop** on the local machine.
2. **Deploy** via `./scripts/deploy_to_pi.sh` (rsync + service restart).
3. **First-time install** on the Pi via `./deploy/install_fresh_pi.sh`.
4. **Ready** at `http://your-pi-ip:5000`.

> The earlier bare-repo + `post-receive` hook model is **no longer used**.

---

## 📋 Prerequisites

### Raspberry Pi / Linux Server
- **Operating System**: Raspberry Pi OS or Ubuntu/Debian
- **SSH Access**: Enabled and accessible
- **User**: With sudo privileges (default `pi` on Raspberry Pi)
- **Network**: Stable internet connection

### Development Machine
- **Git**: Installed and configured
- **SSH**: Access to the server
- **Text Editor**: For configuration files

### Spotify Credentials
- **Spotify Developer App**: Created at https://developer.spotify.com
- **Client ID**: From the Spotify App
- **Client Secret**: From the Spotify App
- **Refresh Token**: Obtained via OAuth2 flow

---

## 🛠️ Complete Setup from Scratch

### Recommended: guided install

On a fresh Pi/server the guided script handles packages, venv, `.env`, an optional token,
and the systemd units in one pass:

```bash
git clone https://github.com/phobits/SpotiPi.git /home/pi/spotipi
cd /home/pi/spotipi
./deploy/install_fresh_pi.sh
```

`deploy/install_fresh_pi.sh` performs:
1. `apt update/upgrade` + base packages (`git python3 python3-venv python3-pip`).
2. venv at `/home/pi/spotipi/venv` + `pip install -r requirements.txt` (includes **waitress** — without it the server falls back to the Flask dev server).
3. Creates `~/.spotipi/.env` (`chmod 600`), prompts for Spotify credentials + a generated `FLASK_SECRET_KEY`, sets `SPOTIPI_ENV=production`.
4. Optionally generates a token (`generate_token.py`).
5. Installs the systemd units via `deploy/install.sh` and writes an override for the real app path.

### Manual: install only the systemd units

If the venv and `~/.spotipi/.env` already exist, `deploy/install.sh` installs the shipped
units from `deploy/systemd/` — no hand-written unit file needed:

```bash
cd /home/pi/spotipi
./deploy/install.sh    # copies units, enable+restart spotipi.service,
                       # enables spotipi-alarm.timer by default
```

The shipped `deploy/systemd/spotipi.service`:

```ini
[Service]
Type=simple
WorkingDirectory=/home/pi/spotipi
EnvironmentFile=-/home/pi/.spotipi/.env
Environment=PYTHONUNBUFFERED=1
Environment=SPOTIPI_LOW_POWER=1
Environment=SPOTIPI_MAX_CONCURRENCY=2
Environment=SPOTIPI_JSON_LOGS=1
ExecStart=/home/pi/spotipi/venv/bin/python run.py
Restart=on-failure
RestartSec=5s
```

Logging goes to journald (`journalctl -u spotipi.service`), not a `spotipi.log` file.
The `spotipi-alarm.timer` (daily 05:30, `Persistent=true`) is enabled by default as a
readiness backup to the in-process scheduler thread; disable with
`SPOTIPI_ENABLE_ALARM_TIMER=0 ./deploy/install.sh`.

### Environment configuration (`~/.spotipi/.env`)

The canonical runtime secrets path is `~/.spotipi/.env` (created by `install_fresh_pi.sh`;
also editable via **Settings → Spotify Account** in the UI). Minimum content:

```env
SPOTIFY_CLIENT_ID=your_client_id_here
SPOTIFY_CLIENT_SECRET=your_client_secret_here
SPOTIFY_USERNAME=your_spotify_username_here
# optional, otherwise produced by generate_token.py:
SPOTIFY_REFRESH_TOKEN=your_refresh_token_here
```

Full variable reference: [`ENVIRONMENT_VARIABLES.md`](ENVIRONMENT_VARIABLES.md).

---

## 🔄 Deployment from the development machine

Deployment runs via `rsync` through `scripts/deploy_to_pi.sh` — no Git hook, no bare repo.

```bash
# One-time setup
cp scripts/deploy_to_pi.sh.example scripts/deploy_to_pi.sh
chmod +x scripts/deploy_to_pi.sh

# Deploy (rsync + service restart)
./scripts/deploy_to_pi.sh

# Useful flags
SPOTIPI_FORCE_SYSTEMD=1 ./scripts/deploy_to_pi.sh   # force systemd unit refresh
SPOTIPI_PURGE_UNUSED=1  ./scripts/deploy_to_pi.sh   # one-off cleanup of stale files
SPOTIPI_SERVICE_NAME="" ./scripts/deploy_to_pi.sh   # code sync only, no service restart

# Check status
ssh pi@spotipi.local "sudo systemctl status spotipi.service"
ssh pi@spotipi.local "journalctl -u spotipi.service -n 50 --no-pager"
```

> Build the frontend (`npm run build`) before deploying — the Pi does not rebuild `static/dist/`.

---

## 🔧 Service Management

### Service Commands

```bash
# Start service
sudo systemctl start spotipi.service

# Stop service
sudo systemctl stop spotipi.service

# Restart service (main command)
sudo systemctl restart spotipi.service
# or use alias:
restart-spotipi

# Check status
sudo systemctl status spotipi.service
# or use alias:
status-spotipi

# View logs
sudo journalctl -u spotipi.service -f
# or use alias:
logs-spotipi

# Enable auto-start on boot
sudo systemctl enable spotipi.service
```

### Log Files

| Source | Purpose | Access |
|--------|---------|--------|
| journald | Application stdout/stderr + service management | `journalctl -u spotipi.service` |
| journald | Alarm readiness timer | `journalctl -u spotipi-alarm.service` |
| local (dev) | Deploy output | console output of `./scripts/deploy_to_pi.sh` |

---

## 🐛 Troubleshooting

### Common Issues

#### Service Won't Start
```bash
# Check service status
sudo systemctl status spotipi.service

# Check detailed logs
sudo journalctl -u spotipi.service -f

# Verify Python environment
ls -la /home/pi/spotipi/venv/bin/python
```

#### Deployment Fails
```bash
# Run the deploy from the dev machine and read its output (rsync/SSH errors)
./scripts/deploy_to_pi.sh

# Verify SSH reachability + service state
ssh pi@spotipi.local "echo ok && sudo systemctl status spotipi.service"

# Service logs after a failed restart
ssh pi@spotipi.local "journalctl -u spotipi.service -n 50 --no-pager"
```

#### Environment Issues
```bash
# Verify .env file
cat /home/pi/.spotipi/.env

# Check file permissions
ls -la /home/pi/.spotipi/.env

# Test Python imports
cd /home/pi/spotipi
venv/bin/python -c "import src.app"
```

#### SSH Connection Problems
```bash
# Test SSH connection
ssh pi@your-server-ip "echo 'Connection OK'"

# Use SSH keys (recommended)
ssh-copy-id pi@your-server-ip

# Test Git over SSH
git ls-remote production
```

### Debug Commands

```bash
# Service status overview
systemctl --user status spotipi.service

# Disk space check
df -h

# Memory usage
free -h

# Process check
ps aux | grep python

# Network connectivity
curl -I http://localhost:5000

# Port availability check
sudo netstat -tlnp | grep :5000
```

---

## 🔄 Maintenance

### Regular Tasks

#### Weekly
```bash
# Update system packages
sudo apt update && sudo apt upgrade -y

# Check log file sizes
du -sh /home/pi/*.log

# Verify service health
sudo systemctl status spotipi.service
```

#### Monthly
```bash
# Rotate large log files
sudo logrotate -f /etc/logrotate.conf

# Check available disk space
df -h

# Backup configuration
cp /home/pi/.spotipi/.env /home/pi/.env.backup
```

### Backup Strategy

#### Configuration Backup
```bash
#!/bin/bash
# backup.sh
BACKUP_DIR="/home/pi/backups/$(date +%Y%m%d)"
mkdir -p "$BACKUP_DIR"

# Backup critical files
cp /home/pi/.spotipi/.env "$BACKUP_DIR/"
cp /etc/systemd/system/spotipi.service "$BACKUP_DIR/"
cp -r /home/pi/spotipi/deploy/systemd "$BACKUP_DIR/units"

echo "Backup completed: $BACKUP_DIR"
```

#### Configuration Export
```bash
# Export current configuration
python3 -c "
import json
from src.config import load_config
config = load_config()
print(json.dumps(config, indent=2))
" > config_backup.json
```

### Updates and Upgrades

#### Update Application
```bash
# From the development machine — normal deployment (rsync + restart)
./scripts/deploy_to_pi.sh

# If dependencies changed, also on the server:
ssh pi@spotipi.local "cd /home/pi/spotipi && venv/bin/pip install -r requirements.txt && sudo systemctl restart spotipi.service"
```

#### Update Python Dependencies
```bash
cd /home/pi/spotipi
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt --upgrade
```

---

## 📚 Additional Resources

### Useful Commands Reference

```bash
# Quick deployment status
ssh pi@your-server-ip "systemctl is-active spotipi.service && journalctl -u spotipi.service -n 5 --no-pager"

# Remote restart
ssh pi@your-server-ip "sudo systemctl restart spotipi.service"

# Check app accessibility
curl -s -o /dev/null -w "%{http_code}" http://your-server-ip:5000

# Liveness incl. version
curl -s http://your-server-ip:5000/healthz

# App directory size
du -sh /home/pi/spotipi

# Find server IP address
hostname -I
```

### Network Configuration

#### Access Points
- **Local IP**: `http://192.168.x.x:5000`
- **Hostname**: `http://raspberrypi.local:5000` (with mDNS)
- **SSH Access**: `ssh pi@raspberrypi.local` or `ssh pi@192.168.x.x`

#### Firewall (if enabled)
```bash
sudo ufw allow 5000/tcp
sudo ufw allow 22/tcp
sudo ufw enable
```

#### Port Forwarding (Router)
For external access:
- **Internal Port**: 5000
- **External Port**: Your choice (e.g. 8080)
- **Protocol**: TCP

---

## 🎯 Quick Start Summary

For experienced users who need quick setup:

```bash
# On the server: fresh guided install (packages, venv, .env, systemd units)
git clone https://github.com/phobits/SpotiPi.git /home/pi/spotipi
cd /home/pi/spotipi
./deploy/install_fresh_pi.sh

# Then, from the development machine: deploy
cp scripts/deploy_to_pi.sh.example scripts/deploy_to_pi.sh && chmod +x scripts/deploy_to_pi.sh
./scripts/deploy_to_pi.sh

# Ready to go! 🚀
```

### Customizations for Your Setup

**Adjust paths:**
- `/home/pi/` → Your user path
- `pi` → Your username
- `192.168.x.x` → Your server IP

**Adjust service names:**
- `spotipi.service` → Your desired service name
- Adjust repository names accordingly

**Adjust network:**
- Set up SSH keys for secure connection
- Configure firewall rules as needed
- Use port 5000 or another port

---

**Last Updated**: May 31, 2026  
**Version**: 1.9.1  
**License**: MIT License
