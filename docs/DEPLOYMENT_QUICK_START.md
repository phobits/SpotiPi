# 🚀 SpotiPi Deployment Quick Start (Pi Zero W)

**Datum:** 31. Mai 2026  
**Version:** 1.9.1  
**Python:** 3.10+  
**Verifizierte Test-Baseline:** `pytest -q` → `186 passed, 2 skipped`

---

## 📋 **Pre-Deployment Checklist**

### ✅ **1. Lokale Änderungen committed?**
```bash
git status  # Sollte "working tree clean" zeigen
```

### ✅ **2. Environment-Check auf dem Pi**

SSH zum Pi:
```bash
ssh pi@spotipi.local
```

Prüfe `.env` Datei:
```bash
cat /home/pi/.spotipi/.env
```

**Minimale `.env` für Production:**
```bash
# Spotify Credentials (PFLICHT)
SPOTIFY_CLIENT_ID=deine_client_id
SPOTIFY_CLIENT_SECRET=dein_client_secret
SPOTIFY_REFRESH_TOKEN=dein_refresh_token
SPOTIFY_USERNAME=dein_username

# Flask Security (PFLICHT)
FLASK_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# Environment
SPOTIPI_ENV=production

# Performance (wird automatisch von systemd service gesetzt, optional hier)
# SPOTIPI_LOW_POWER=1
# SPOTIPI_MAX_CONCURRENCY=2
# SPOTIPI_JSON_LOGS=1
```

**Wichtig:** Die Runtime-Secrets liegen kanonisch unter `/home/pi/.spotipi/.env`. Ein Repo-Root-`.env` ist nur für lokale Dev-Overrides gedacht.

---

## 🚀 **Deployment durchführen**

### **Option A: Deployment Script (Empfohlen)**

Von deinem **Mac** aus:

```bash
cd /Users/michi/Development/repos/spotipi

# Lokales Deploy-Script aus der anonymisierten Vorlage erzeugen
cp scripts/deploy_to_pi.sh.example scripts/deploy_to_pi.sh
chmod +x scripts/deploy_to_pi.sh

# Standard-Deployment (rsync + systemd restart)
./scripts/deploy_to_pi.sh
```

**Das Script macht automatisch:**
- ✅ Sync von `src/`, `static/`, `templates/`, `config/`, `deploy/` zum Pi
- ✅ Ausschluss von Tests, Docs, Logs, Cache-Files
- ✅ Systemd Units Update (inkl. neuer Performance-Flags)
- ✅ Service Restart (`spotipi.service`)
- ✅ Alarm Timer Enable (`spotipi-alarm.timer`)

**Custom Options:**
```bash
# Deployment ohne systemd restart (nur code sync)
SPOTIPI_SERVICE_NAME="" ./scripts/deploy_to_pi.sh

# Force systemd update (auch wenn keine Änderung erkannt)
SPOTIPI_FORCE_SYSTEMD=1 ./scripts/deploy_to_pi.sh

# Alte Files auf dem Pi löschen (einmalig nach großen Refactorings)
SPOTIPI_PURGE_UNUSED=1 ./scripts/deploy_to_pi.sh

# Custom Pi Host/Path
SPOTIPI_PI_HOST=pi@192.168.1.100 SPOTIPI_PI_PATH=/opt/spotipi ./scripts/deploy_to_pi.sh
```

---

### **Option B: Git Push Deployment**

Falls du Git Hooks konfiguriert hast (siehe `docs/DEPLOYMENT.md`):

```bash
git push prod main
```

**⚠️ Achtung:** Git Hooks updaten systemd units NICHT automatisch!  
Nach dem Push einmalig per SSH:
```bash
ssh pi@spotipi.local
cd /home/pi/spotipi
sudo cp deploy/systemd/spotipi.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart spotipi.service
```

---

## 🔍 **Post-Deployment Verification**

### **1. Service Status prüfen**
```bash
ssh pi@spotipi.local 'sudo systemctl status spotipi.service'
```

**Erwarteter Output:**
```
● spotipi.service - SpotiPi Web Application
   Loaded: loaded (/etc/systemd/system/spotipi.service; enabled)
   Active: active (running) since Mon 2025-11-04 12:34:56 CET
   ...
   Main PID: 1234 (python)
```

### **2. LOW_POWER_MODE aktiv?**
```bash
ssh pi@spotipi.local 'journalctl -u spotipi.service -n 50 --no-pager | grep -i "low.power\|pool\|worker"'
```

**Erwarteter Output:**
```json
{"timestamp": "...", "level": "info", "message": "LOW_POWER_MODE enabled", "workers": 2}
{"timestamp": "...", "level": "info", "message": "HTTP pool configured", "pool_maxsize": 5}
```

### **3. Web Interface erreichbar?**
```bash
curl -I http://spotipi.local:5000/
```

**Erwarteter Output:**
```
HTTP/1.1 200 OK
Server: waitress
...
```

### **4. Alarm Timer aktiv?**
```bash
ssh pi@spotipi.local 'sudo systemctl status spotipi-alarm.timer'
```

**Erwarteter Output:**
```
● spotipi-alarm.timer - SpotiPi Alarm Reliability Timer
   Loaded: loaded (/etc/systemd/system/spotipi-alarm.timer; enabled)
   Active: active (waiting) since ...
   Triggers: ● spotipi-alarm.service
```

---

## 📊 **Performance Monitoring (Optional)**

### **Strukturierte Logs abfragen (JSON)**
```bash
# Alle Logs der letzten Stunde
ssh pi@spotipi.local 'journalctl -u spotipi.service --since "1 hour ago" -o json-pretty'

# Nur Errors
ssh pi@spotipi.local 'journalctl -u spotipi.service -p err -o json-pretty'

# Alarm-spezifische Events
ssh pi@spotipi.local 'journalctl -u spotipi.service -o json | jq "select(.ALARM_ID != null)"'
```

### **Resource Usage**
```bash
# CPU/Memory während Library-Load
ssh pi@spotipi.local 'top -b -n 1 | grep python'

# Disk I/O (benötigt iotop)
ssh pi@spotipi.local 'sudo iotop -b -n 1 -P | grep python'
```

### **Cache Statistics**
```bash
# Config Cache Hits
ssh pi@spotipi.local 'journalctl -u spotipi.service --since today | grep "config.cache"'

# Device Persist Events (sollte max 144x/Tag sein bei 600s interval)
ssh pi@spotipi.local 'journalctl -u spotipi.service --since today | grep "device.cache.persist" | wc -l'
```

---

## 🐛 **Troubleshooting**

### **Problem: Service startet nicht**
```bash
# Full Logs anzeigen
ssh pi@spotipi.local 'sudo journalctl -u spotipi.service -xe'

# Python Errors
ssh pi@spotipi.local 'sudo journalctl -u spotipi.service -p err --since "10 min ago"'

# Environment-Variablen prüfen
ssh pi@spotipi.local 'sudo systemctl show spotipi.service -p Environment'
```

### **Problem: LOW_POWER_MODE nicht aktiv**
```bash
# Check systemd Environment
ssh pi@spotipi.local 'sudo cat /etc/systemd/system/spotipi.service | grep SPOTIPI_LOW_POWER'

# Manual restart mit debug
ssh pi@spotipi.local 'cd /home/pi/spotipi && SPOTIPI_LOW_POWER=1 venv/bin/python run.py'
```

### **Problem: Web Interface langsam**
```bash
# Library Load Timeout Events
ssh pi@spotipi.local 'journalctl -u spotipi.service | grep -i "timeout\|slow"'

# Netzwerk-Latenz zu Spotify
ssh pi@spotipi.local 'ping -c 5 api.spotify.com'

# Worker Count Verification
ssh pi@spotipi.local 'ps aux | grep python | wc -l'  # Sollte ~3-4 sein (main + 2 workers)
```

### **Problem: Alarms werden verpasst**
```bash
# Check Timer Next Trigger
ssh pi@spotipi.local 'sudo systemctl list-timers spotipi-alarm.timer'

# Check Alarm Service Logs
ssh pi@spotipi.local 'sudo journalctl -u spotipi-alarm.service -n 50'

# Manual Alarm Test
ssh pi@spotipi.local '/home/pi/spotipi/scripts/run_alarm.sh'
```

---

## 🔄 **Rollback (falls nötig)**

### **Code Rollback via Git**
```bash
# Auf dem Pi
ssh pi@spotipi.local
cd /home/pi/spotipi
git log --oneline -5  # Letzte Commits anzeigen
git checkout <commit-hash>
sudo systemctl restart spotipi.service
```

### **Systemd Service Rollback**
```bash
# Alte systemd unit wiederherstellen
ssh pi@spotipi.local
sudo cp /etc/systemd/system/spotipi.service.backup /etc/systemd/system/spotipi.service
sudo systemctl daemon-reload
sudo systemctl restart spotipi.service
```

---

## 📈 **Historische Performance-Referenz (v1.3.8)**

### **Before (v1.3.7):**
- Library Load: **~8.2s**
- Config I/O: **43,200 reads/Tag**
- Device Writes: **480 writes/Tag**
- API Polling: **86,400 calls/Tag**
- UI Freezes: **1-2x/Tag** (bei schlechter Netzwerkverbindung)

### **After (v1.3.8 mit Quick Wins):**
- Library Load: **~6.1s** (-25%)
- Config I/O: **2,880 reads/Tag** (-93%)
- Device Writes: **144 writes/Tag** (-70%)
- API Polling: **28,800 calls/Tag** (-60%)
- UI Freezes: **0x/Tag** (ThreadPoolExecutor timeout)

### **Combined Impact:**
- **-93% SD-Card I/O** (Config Cache)
- **-60% API Rate Limit Risk** (Playback Cache)
- **-25% Library Load Time** (Worker Limit)
- **-20% CPU Usage** (2 statt 4 Workers)
- **100% Freeze-Free** (Timeout Handling)

---

## 📚 **Weiterführende Dokumentation**

- **Vollständige Deployment-Anleitung:** `docs/DEPLOYMENT.md`
- **Environment-Variablen:** `docs/ENVIRONMENT_VARIABLES.md`
- **Structured Logging:** `docs/JSON_LOGGING.md`
- **HTTP Retry Logic:** `docs/SPOTIFY_API_RETRY.md`
- **Config Validation:** `docs/CONFIG_SCHEMA_VALIDATION.md`

---

**Status:** ✅ Ready for Production Deployment  
**Tested:** Pi Zero W (ARMv6, 512MB RAM, Raspbian Lite) und aktuelle Test-Baseline vom 17. März 2026  
**Risk Level:** 🟢 Low (aktueller Patch-Release, Verträge auf Doku und Tests abgestimmt)
