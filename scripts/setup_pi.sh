#!/usr/bin/env bash
# =============================================================================
# SpotiPi — Full Pi Setup Script (Pi-hole compatible)
#
# Run on the Pi:
#   curl -sSL <raw-url> | bash
#   — or —
#   bash scripts/setup_pi.sh
#
# What it does:
#   1. Checks prerequisites (Python, RAM, disk, network)
#   2. Detects Pi-hole and adjusts accordingly
#   3. Clones repo (or updates existing)
#   4. Creates venv and installs Python deps
#   5. Sets up secrets directory and .env
#   6. Patches systemd units for Pi-hole coexistence
#   7. Ensures swap is configured (Pi Zero 2 W)
#   8. Installs and starts systemd services
#   9. Verifies everything works
# =============================================================================
set -euo pipefail

# -- Config -------------------------------------------------------------------
REPO_URL="https://github.com/phobits/SpotiPi.git"
INSTALL_DIR="/home/pi/spotipi"
SECRETS_DIR="/home/pi/.spotipi"
VENV_DIR="$INSTALL_DIR/venv"
MIN_PYTHON="3.10"
MIN_DISK_MB=500
SWAP_SIZE_MB=256

# -- Colors -------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

# =============================================================================
# Phase 1: Prerequisites
# =============================================================================
echo ""
echo "==========================================="
echo "  SpotiPi Setup for Raspberry Pi"
echo "==========================================="
echo ""

# Must not be root (systemd user services)
if [ "$(id -u)" -eq 0 ]; then
  fail "Don't run as root. Run as 'pi' user (sudo is used where needed)."
fi

# Python version check
info "Checking Python version..."
if ! command -v python3 &>/dev/null; then
  fail "python3 not found. Install with: sudo apt install python3"
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
  fail "Python >= $MIN_PYTHON required, found $PY_VERSION"
fi
ok "Python $PY_VERSION"

# pip / venv available?
info "Checking python3-venv..."
if ! python3 -m venv --help &>/dev/null; then
  warn "python3-venv not installed. Installing..."
  sudo apt-get update -qq && sudo apt-get install -y -qq python3-venv
fi
ok "python3-venv available"

# git available?
if ! command -v git &>/dev/null; then
  warn "git not found. Installing..."
  sudo apt-get update -qq && sudo apt-get install -y -qq git
fi

# Disk space
info "Checking disk space..."
AVAIL_MB=$(df -BM --output=avail / | tail -1 | tr -d ' M')
if [ "$AVAIL_MB" -lt "$MIN_DISK_MB" ]; then
  fail "Need at least ${MIN_DISK_MB}MB free disk space, only ${AVAIL_MB}MB available"
fi
ok "${AVAIL_MB}MB free"

# RAM check
info "Checking memory..."
TOTAL_RAM_MB=$(free -m | awk '/^Mem:/ {print $2}')
FREE_RAM_MB=$(free -m | awk '/^Mem:/ {print $7}')
info "Total: ${TOTAL_RAM_MB}MB, Available: ${FREE_RAM_MB}MB"
if [ "$FREE_RAM_MB" -lt 100 ]; then
  warn "Low available RAM (${FREE_RAM_MB}MB). SpotiPi needs ~80-150MB."
  warn "Consider stopping unnecessary services or adding swap."
fi

# =============================================================================
# Phase 2: Pi-hole Detection
# =============================================================================
PIHOLE_DETECTED=false
info "Checking for Pi-hole..."

if systemctl is-active --quiet pihole-FTL 2>/dev/null; then
  PIHOLE_DETECTED=true
  ok "Pi-hole detected and running"
elif command -v pihole &>/dev/null; then
  PIHOLE_DETECTED=true
  warn "Pi-hole installed but FTL not running"
else
  info "Pi-hole not detected — skipping Pi-hole integration"
fi

# =============================================================================
# Phase 3: Swap Setup (Pi Zero 2 W — 512MB RAM)
# =============================================================================
info "Checking swap..."
SWAP_TOTAL=$(free -m | awk '/^Swap:/ {print $2}')

if [ "$SWAP_TOTAL" -lt 100 ]; then
  warn "No/low swap configured (${SWAP_TOTAL}MB). Setting up ${SWAP_SIZE_MB}MB swap..."
  if [ -f /etc/dphys-swapfile ]; then
    sudo sed -i "s/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=${SWAP_SIZE_MB}/" /etc/dphys-swapfile
    sudo dphys-swapfile setup
    sudo dphys-swapfile swapon
    ok "Swap configured: ${SWAP_SIZE_MB}MB"
  else
    # Fallback: manual swapfile
    if [ ! -f /swapfile ]; then
      sudo fallocate -l ${SWAP_SIZE_MB}M /swapfile
      sudo chmod 600 /swapfile
      sudo mkswap /swapfile
      sudo swapon /swapfile
      echo "/swapfile none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
      ok "Swap file created: ${SWAP_SIZE_MB}MB"
    fi
  fi
else
  ok "Swap available: ${SWAP_TOTAL}MB"
fi

# =============================================================================
# Phase 4: Clone / Update Repository
# =============================================================================
info "Setting up SpotiPi repository..."

if [ -d "$INSTALL_DIR/.git" ]; then
  info "Existing installation found. Pulling latest..."
  cd "$INSTALL_DIR"
  git fetch origin
  git reset --hard origin/main
  ok "Updated to latest"
else
  info "Cloning repository..."
  git clone "$REPO_URL" "$INSTALL_DIR"
  ok "Cloned to $INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# =============================================================================
# Phase 5: Python Virtual Environment
# =============================================================================
info "Setting up Python virtual environment..."

if [ ! -d "$VENV_DIR" ]; then
  python3 -m venv "$VENV_DIR"
  ok "Created venv at $VENV_DIR"
else
  ok "Venv already exists"
fi

info "Installing Python dependencies..."
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet -r requirements.txt
ok "Dependencies installed"

# =============================================================================
# Phase 6: Secrets & Configuration
# =============================================================================
info "Setting up secrets directory..."
mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"

if [ ! -f "$SECRETS_DIR/.env" ]; then
  # Generate a secure Flask secret key
  FLASK_KEY=$("$VENV_DIR/bin/python" -c "import secrets; print(secrets.token_hex(32))")

  cat > "$SECRETS_DIR/.env" <<ENVEOF
# SpotiPi Environment Configuration
# Generated by setup_pi.sh on $(date -Iseconds)

# Spotify API Credentials
# Get these from: https://developer.spotify.com/dashboard
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
SPOTIFY_REFRESH_TOKEN=
SPOTIFY_USERNAME=

# Flask Secret Key (auto-generated)
FLASK_SECRET_KEY=${FLASK_KEY}

# Production settings
SPOTIPI_ENV=production
PORT=5000
ENVEOF

  chmod 600 "$SECRETS_DIR/.env"
  warn "Created $SECRETS_DIR/.env — YOU MUST edit it with your Spotify credentials!"
  warn "  nano $SECRETS_DIR/.env"
  echo ""
  echo -e "  ${YELLOW}Required fields:${NC}"
  echo "    SPOTIFY_CLIENT_ID     — from https://developer.spotify.com/dashboard"
  echo "    SPOTIFY_CLIENT_SECRET — from the same app"
  echo "    SPOTIFY_USERNAME      — your Spotify username"
  echo ""
  echo -e "  ${YELLOW}After editing .env, generate the refresh token:${NC}"
  echo "    cd $INSTALL_DIR && source venv/bin/activate && python generate_token.py"
  echo ""
  NEEDS_CREDENTIALS=true
else
  ok "Secrets file exists at $SECRETS_DIR/.env"
  # Check if credentials are filled in
  source "$SECRETS_DIR/.env" 2>/dev/null || true
  if [ -z "${SPOTIFY_CLIENT_ID:-}" ] || [ "$SPOTIFY_CLIENT_ID" = "your_client_id_here" ]; then
    warn "Spotify credentials not configured yet!"
    NEEDS_CREDENTIALS=true
  else
    ok "Spotify credentials present"
    NEEDS_CREDENTIALS=false
  fi
fi

# =============================================================================
# Phase 7: systemd Service Installation (Pi-hole aware)
# =============================================================================
info "Installing systemd services..."

SYSTEMD_DIR="/etc/systemd/system"

# Copy service files
for unit_file in deploy/systemd/*.service deploy/systemd/*.timer; do
  [ -f "$unit_file" ] || continue
  UNIT_NAME="$(basename "$unit_file")"
  info "  Installing $UNIT_NAME"
  sudo cp "$unit_file" "$SYSTEMD_DIR/$UNIT_NAME"
done

# Patch spotipi.service for Pi-hole coexistence
if [ "$PIHOLE_DETECTED" = true ]; then
  info "Patching spotipi.service for Pi-hole coexistence..."
  # Add After=pihole-FTL.service so DNS is ready before SpotiPi starts
  if ! grep -q "pihole-FTL" "$SYSTEMD_DIR/spotipi.service"; then
    sudo sed -i '/^After=network-online.target/a After=pihole-FTL.service' "$SYSTEMD_DIR/spotipi.service"
    ok "Added After=pihole-FTL.service dependency"
  else
    ok "Pi-hole dependency already configured"
  fi
fi

sudo systemctl daemon-reload

# Enable and start main service
sudo systemctl enable spotipi.service
ok "spotipi.service enabled"

# Enable alarm timer
if [ "${SPOTIPI_ENABLE_ALARM_TIMER:-1}" = "1" ]; then
  sudo systemctl enable spotipi-alarm.timer
  ok "spotipi-alarm.timer enabled"
fi

# Start service only if credentials are configured
if [ "${NEEDS_CREDENTIALS:-true}" = true ]; then
  warn "NOT starting spotipi.service — credentials not configured yet"
  warn "After configuring credentials and generating token, start with:"
  warn "  sudo systemctl start spotipi.service"
  warn "  sudo systemctl start spotipi-alarm.timer"
else
  info "Starting SpotiPi..."
  sudo systemctl restart spotipi.service
  sudo systemctl start spotipi-alarm.timer
  sleep 3

  if systemctl is-active --quiet spotipi.service; then
    ok "spotipi.service is running"
  else
    warn "spotipi.service failed to start. Check: sudo journalctl -u spotipi -n 30"
  fi
fi

# =============================================================================
# Phase 8: Verification
# =============================================================================
echo ""
echo "==========================================="
echo "  Setup Summary"
echo "==========================================="
echo ""

ok "Python:    $PY_VERSION"
ok "Venv:      $VENV_DIR"
ok "App:       $INSTALL_DIR"
ok "Secrets:   $SECRETS_DIR/.env"
ok "Port:      5000"

if [ "$PIHOLE_DETECTED" = true ]; then
  ok "Pi-hole:   detected, systemd dependency added"
fi

SWAP_NOW=$(free -m | awk '/^Swap:/ {print $2}')
ok "Swap:      ${SWAP_NOW}MB"

RAM_NOW=$(free -m | awk '/^Mem:/ {print $7}')
ok "Free RAM:  ${RAM_NOW}MB"

echo ""
if [ "${NEEDS_CREDENTIALS:-true}" = true ]; then
  echo -e "${YELLOW}=== NEXT STEPS ===${NC}"
  echo ""
  echo "  1. Edit credentials:"
  echo "     nano $SECRETS_DIR/.env"
  echo ""
  echo "  2. Generate Spotify refresh token (needs browser):"
  echo "     cd $INSTALL_DIR && source venv/bin/activate"
  echo "     python generate_token.py"
  echo ""
  echo "     Tip: If headless, generate token on your Mac first,"
  echo "     then copy the SPOTIFY_REFRESH_TOKEN value to .env"
  echo ""
  echo "  3. Start SpotiPi:"
  echo "     sudo systemctl start spotipi.service"
  echo "     sudo systemctl start spotipi-alarm.timer"
  echo ""
  echo "  4. Open dashboard:"
  echo "     http://$(hostname -I | awk '{print $1}'):5000"
  echo ""
else
  PI_IP=$(hostname -I | awk '{print $1}')
  echo -e "${GREEN}SpotiPi is running!${NC}"
  echo ""
  echo "  Dashboard:    http://${PI_IP}:5000"
  if [ "$PIHOLE_DETECTED" = true ]; then
    echo "  Pi-hole:      http://${PI_IP}/admin"
  fi
  echo ""
  echo "  Useful commands:"
  echo "    sudo systemctl status spotipi"
  echo "    sudo journalctl -u spotipi -f"
  echo "    sudo systemctl restart spotipi"
  echo ""
fi

echo "==========================================="
echo "  Setup complete!"
echo "==========================================="
