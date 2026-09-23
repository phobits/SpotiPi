# 🚀 SpotiPi Deployment Anleitung

Umfassende Anleitung für die Einrichtung und Verwaltung des SpotiPi Deployments auf dem Raspberry Pi.

## 📋 Inhaltsverzeichnis

1. [Aktueller Setup Überblick](#aktueller-setup-überblick)
2. [Komplette Einrichtung von Grund auf](#komplette-einrichtung-von-grund-auf)
3. [Deployment vom Entwicklungsrechner](#deployment-vom-entwicklungsrechner)
4. [Service Verwaltung](#service-verwaltung)
5. [Fehlerbehebung](#fehlerbehebung)
6. [Wartung](#wartung)

---

## 🏗️ Aktueller Setup Überblick

### Architektur
```
Mac Entwicklung              Raspberry Pi Produktion
├── repos/spotipi            ├── /home/pi/spotipi          (app, rsync-Ziel)
│   ├── scripts/             ├── /home/pi/.spotipi/.env    (Runtime-Secrets)
│   │   └── deploy_to_pi.sh  ├── systemd: spotipi.service  (Waitress via run.py)
│   └── deploy/systemd/      └── systemd: spotipi-alarm.timer (Readiness-Backup)
```

### Deployment Ablauf
1. **Entwickeln** auf dem Mac im Repo (`~/Development/repos/spotipi`).
2. **Shippen** mit `/ship` (commit + push an `github`) — oder direkt `./scripts/deploy_to_pi.sh`.
3. **Sync** via `rsync` auf den Pi (`scripts/deploy_to_pi.sh`), inkl. optionalem systemd-Unit-Refresh.
4. **Auto-Neustart** der `spotipi.service`.
5. **Bereit** unter `http://spotipi.local:5000`.

> Hinweis: Das frühere Modell (bare Git-Repo + `post-receive`-Hook) wird **nicht mehr verwendet**.
> Deployment läuft über `scripts/deploy_to_pi.sh` (rsync), Erstinstallation über `deploy/install_fresh_pi.sh`.

### Aktuelle Git Remotes
```bash
github  git@github.com:phobits/SpotiPi.git   # einziges Remote (Code + Backup)
```

---

## 🛠️ Komplette Einrichtung von Grund auf

### Empfohlen: Geführte Installation

Auf einem frischen Pi erledigt das geführte Skript Pakete, venv, `.env`, optionalen
Token und die systemd-Units in einem Durchlauf:

```bash
git clone https://github.com/phobits/SpotiPi.git /home/pi/spotipi
cd /home/pi/spotipi
./deploy/install_fresh_pi.sh
```

`deploy/install_fresh_pi.sh` macht:
1. `apt update/upgrade` + Basis-Pakete (`git python3 python3-venv python3-pip`).
2. venv unter `/home/pi/spotipi/venv` + `pip install -r requirements.txt` (inkl. **waitress** – sonst fällt der Server auf den Flask-Dev-Server zurück).
3. `~/.spotipi/.env` anlegen (`chmod 600`), Spotify-Credentials + `FLASK_SECRET_KEY` interaktiv setzen, `SPOTIPI_ENV=production`.
4. optional Token generieren (`generate_token.py`).
5. systemd-Units via `deploy/install.sh` installieren und einen Override für den realen App-Pfad setzen.

### Manuell: nur die systemd-Units installieren

Wenn venv und `~/.spotipi/.env` bereits stehen, installiert `deploy/install.sh` die
mitgelieferten Units aus `deploy/systemd/` (kein handgeschriebenes Unit-File nötig):

```bash
cd /home/pi/spotipi
./deploy/install.sh         # kopiert die Units, enable+restart spotipi.service,
                            # aktiviert standardmäßig spotipi-alarm.timer
```

Die gelieferten Units (`deploy/systemd/spotipi.service`):

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

Logging läuft über journald (`journalctl -u spotipi.service`), nicht über eine `web.log`-Datei.

### Alarm-Readiness-Timer (Robustheit)

`deploy/install.sh` aktiviert standardmäßig `spotipi-alarm.timer` (täglich 05:30,
`Persistent=true` → Catch-up nach Reboot). Der Timer ruft `spotipi-alarm.service`
→ `scripts/run_alarm.sh` als Backup-Layer zum in-process Scheduler-Thread auf.

```bash
sudo systemctl status spotipi-alarm.timer       # Status
sudo systemctl disable --now spotipi-alarm.timer # deaktivieren
# Bei der Installation deaktivieren:
SPOTIPI_ENABLE_ALARM_TIMER=0 ./deploy/install.sh
```

### Umgebungskonfiguration (`~/.spotipi/.env`)

Kanonischer Runtime-Pfad ist `~/.spotipi/.env` (vom `install_fresh_pi.sh` angelegt; alternativ
über **Settings → Spotify Account** in der UI pflegbar). Mindest-Inhalt:

```env
SPOTIFY_CLIENT_ID=deine_client_id
SPOTIFY_CLIENT_SECRET=dein_client_secret
SPOTIFY_USERNAME=dein_username
# optional, sonst via generate_token.py erzeugt:
SPOTIFY_REFRESH_TOKEN=dein_refresh_token
```

Vollständige Variablen-Referenz: [`ENVIRONMENT_VARIABLES.md`](ENVIRONMENT_VARIABLES.md).

---

## 🔄 Deployment vom Entwicklungsrechner

Deployment läuft per `rsync` über `scripts/deploy_to_pi.sh` — kein Git-Hook, kein bare Repo.

### Einrichtung (einmalig)

```bash
cp scripts/deploy_to_pi.sh.example scripts/deploy_to_pi.sh   # lokale Kopie (gitignored)
chmod +x scripts/deploy_to_pi.sh
```

### Wie es funktioniert

1. Quelldateien werden per `rsync` nach `pi@spotipi.local:/home/pi/spotipi` synchronisiert.
2. systemd-Units werden bei Bedarf aktualisiert (steuerbar über `SPOTIPI_FORCE_SYSTEMD`).
3. `spotipi.service` wird neu gestartet.
4. Eine Deployment-Zusammenfassung (geänderte/erstellte/gelöschte Dateien) wird ausgegeben.

### Deployment-Befehle (vom Mac)

```bash
# Standard-Deploy (sync + Service-Neustart)
./scripts/deploy_to_pi.sh

# Nützliche Flags
SPOTIPI_FORCE_SYSTEMD=1 ./scripts/deploy_to_pi.sh   # systemd-Units erzwingen
SPOTIPI_PURGE_UNUSED=1  ./scripts/deploy_to_pi.sh   # alte/ungenutzte Dateien einmalig entfernen
SPOTIPI_SERVICE_NAME="" ./scripts/deploy_to_pi.sh   # nur Code-Sync, ohne Service-Neustart

# Status prüfen
ssh pi@spotipi.local "sudo systemctl status spotipi.service"
ssh pi@spotipi.local "journalctl -u spotipi.service -n 50 --no-pager"
```

> Wichtig: Frontend-Änderungen vorher mit `npm run build` bauen — der Pi baut `static/dist/` nicht selbst.

---

## 🔧 Service Verwaltung

### Service Befehle

```bash
# Service starten
sudo systemctl start spotipi.service

# Service stoppen
sudo systemctl stop spotipi.service

# Service neustarten (Hauptbefehl)
sudo systemctl restart spotipi.service
# oder Alias verwenden:
restart-app

# Status prüfen
sudo systemctl status spotipi.service
# oder Alias verwenden:
status-app

# Logs anzeigen
sudo journalctl -u spotipi.service -f
# oder Alias verwenden:
logs-app

# Auto-Start beim Systemstart aktivieren
sudo systemctl enable spotipi.service
```

### Log Dateien

| Quelle | Zweck | Zugriff |
|--------|-------|---------|
| journald | Anwendung stdout/stderr + Service-Verwaltung | `journalctl -u spotipi.service` |
| journald | Alarm-Readiness-Timer | `journalctl -u spotipi-alarm.service` |
| lokal (Dev) | Deploy-Ausgabe | Konsolen-Ausgabe von `./scripts/deploy_to_pi.sh` |

---

## 🐛 Fehlerbehebung

### Häufige Probleme

#### Service startet nicht
```bash
# Service Status prüfen
sudo systemctl status spotipi.service

# Detaillierte Logs prüfen
sudo journalctl -u spotipi.service -f

# Python Umgebung überprüfen
ls -la /home/pi/spotipi/venv/bin/python
```

#### Deployment schlägt fehl
```bash
# Deploy-Ausgabe direkt vom Mac prüfen (rsync-Fehler, SSH-Probleme)
./scripts/deploy_to_pi.sh

# SSH-Erreichbarkeit des Pi testen
ssh pi@spotipi.local "echo ok && sudo systemctl status spotipi.service"

# Service-Logs nach einem fehlgeschlagenen Restart
ssh pi@spotipi.local "journalctl -u spotipi.service -n 50 --no-pager"
```

#### Umgebungsprobleme
```bash
# .env Datei überprüfen
cat /home/pi/.spotipi/.env

# Dateiberechtigungen prüfen
ls -la /home/pi/.spotipi/.env

# Python Imports testen
cd /home/pi/spotipi
venv/bin/python -c "import src.app"
```

### Debug Befehle

```bash
# Service Status Übersicht
systemctl --user status spotipi.service

# Festplattenspeicher prüfen
df -h

# Speicherverbrauch
free -h

# Prozess prüfen
ps aux | grep python

# Netzwerkverbindung
curl -I http://localhost:5000
```

---

## 🔄 Wartung

### Regelmäßige Aufgaben

#### Wöchentlich
```bash
# Systempakete aktualisieren
sudo apt update && sudo apt upgrade -y

# Log-Dateigrößen prüfen
du -sh /home/pi/*.log

# Service-Gesundheit überprüfen
sudo systemctl status spotipi.service
```

#### Monatlich
```bash
# Große Log-Dateien rotieren
sudo logrotate -f /etc/logrotate.conf

# Verfügbaren Festplattenspeicher prüfen
df -h

# Konfiguration sichern
cp /home/pi/.spotipi/.env /home/pi/.env.backup
```

### Backup Strategie

#### Konfiguration Backup
```bash
#!/bin/bash
# backup.sh
BACKUP_DIR="/home/pi/backups/$(date +%Y%m%d)"
mkdir -p "$BACKUP_DIR"

# Kritische Dateien sichern
cp /home/pi/.spotipi/.env "$BACKUP_DIR/"
cp /etc/systemd/system/spotipi.service "$BACKUP_DIR/"
cp -r /home/pi/spotipi/deploy/systemd "$BACKUP_DIR/units"

echo "Backup abgeschlossen: $BACKUP_DIR"
```

#### Datenbank/Konfig Export
```bash
# Aktuelle Konfiguration exportieren
python3 -c "
import json
from src.config import load_config
config = load_config()
print(json.dumps(config, indent=2))
" > config_backup.json
```

### Updates und Upgrades

#### Anwendung aktualisieren
```bash
# Vom Mac - normales Deployment (rsync + Service-Neustart)
./scripts/deploy_to_pi.sh

# Bei geänderten Dependencies zusätzlich auf dem Pi:
ssh pi@spotipi.local "cd /home/pi/spotipi && venv/bin/pip install -r requirements.txt && sudo systemctl restart spotipi.service"
```

#### Python Abhängigkeiten aktualisieren
```bash
cd /home/pi/spotipi
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt --upgrade
```

---

## 📚 Zusätzliche Ressourcen

### Nützliche Befehle Referenz

```bash
# Schneller Deployment Status
ssh pi@spotipi.local "systemctl is-active spotipi.service && journalctl -u spotipi.service -n 5 --no-pager"

# Remote Neustart (ohne Passwort dank /etc/sudoers.d/spotipi-deploy, siehe scripts/setup_pi.sh)
ssh pi@spotipi.local "sudo -n systemctl restart spotipi.service"

# Regel auf einem vor 2026-09 eingerichteten Pi einmalig nachziehen (fragt nach dem Passwort)
ssh -t pi@spotipi.local 'echo "pi ALL=(root) NOPASSWD: /usr/bin/systemctl restart spotipi.service" | sudo EDITOR="tee" visudo -f /etc/sudoers.d/spotipi-deploy'

# App Erreichbarkeit prüfen
curl -s -o /dev/null -w "%{http_code}" http://spotipi.local:5000

# Liveness inkl. Version
curl -s http://spotipi.local:5000/healthz

# App-Verzeichnisgröße
du -sh /home/pi/spotipi
```

### Netzwerk Konfiguration

#### Zugriffspunkte
- **Entwicklung**: `http://spotipi.local:5000`
- **Lokale IP**: `http://192.168.x.x:5000`
- **SSH Zugriff**: `ssh pi@spotipi.local`

#### Firewall (falls aktiviert)
```bash
sudo ufw allow 5000/tcp
sudo ufw allow 22/tcp
sudo ufw enable
```

---

## 🎯 Schnellstart Zusammenfassung

Für erfahrene Benutzer, die das Setup erneut benötigen:

```bash
# Auf dem Pi: frische, geführte Installation (Pakete, venv, .env, systemd-Units)
git clone https://github.com/phobits/SpotiPi.git /home/pi/spotipi
cd /home/pi/spotipi
./deploy/install_fresh_pi.sh

# Danach vom Mac: deployen
cp scripts/deploy_to_pi.sh.example scripts/deploy_to_pi.sh && chmod +x scripts/deploy_to_pi.sh
./scripts/deploy_to_pi.sh

# Bereit zum Loslegen! 🚀
```

---

**Zuletzt aktualisiert**: 31. Mai 2026  
**Version**: 1.9.1  
**Betreuer**: SpotiPi Entwicklungsteam
