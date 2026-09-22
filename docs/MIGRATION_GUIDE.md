# 🔄 SpotiPi Migration Guide: `spotify_wakeup` → `spotipi`

## 📋 Migration Overview

This guide walks you through the safe renaming of the working directory from `spotify_wakeup` to `spotipi`, both locally and on the Raspberry Pi.

### ✨ **Path-Independence Improvements**
All hardcoded paths have been replaced by environment variable-controlled, path-agnostic solutions:
- **Environment Variable**: `SPOTIPI_APP_NAME` (default: "spotipi")
- **Config Directory**: `~/.${SPOTIPI_APP_NAME}/` (default: `~/.spotipi/`)
- **Pi App Directory**: `/home/pi/${SPOTIPI_APP_NAME}/` (default: `/home/pi/spotipi/`)

### 🎯 **Migration Goals**
1. **Consistent Naming**: Project, directories and services all named "spotipi"
2. **Path-Independence**: No more hardcoded paths
3. **Backward Compatibility**: Data migration without loss
4. **Clean Structure**: Old directories will be cleaned up

## 🚀 **Step-by-Step Migration**

### **Phase 1: Local Migration**

#### 1.1 Create Backup
```bash
cd /Users/michi/spotipi-dev/
cp -r spotify_wakeup spotify_wakeup_backup_$(date +%Y%m%d)
```

#### 1.2 Rename Repository Locally  
```bash
cd /Users/michi/spotipi-dev/
mv spotify_wakeup spotipi
cd spotipi
```

#### 1.3 Update Git Remote (if required)
```bash
# If repository URL needs to be changed
git remote set-url origin git@github.com:phobits/SpotiPi.git
```

#### 1.4 Activate New Path-Agnostic Scripts
```bash
# Keep old scripts as backup, use new ones
mv scripts/deploy_to_pi.sh scripts/deploy_to_pi_old.sh
mv scripts/deploy_to_pi_new.sh scripts/deploy_to_pi.sh
chmod +x scripts/deploy_to_pi.sh

mv scripts/toggle_logging.sh scripts/toggle_logging_old.sh  
mv scripts/toggle_logging_new.sh scripts/toggle_logging.sh
chmod +x scripts/toggle_logging.sh
```

### **Phase 2: Raspberry Pi Preparation**

#### 2.1 Stop Service
```bash
ssh pi@spotipi.local "sudo systemctl stop spotify-web.service"
```

#### 2.2 Config/Cache Migration on Pi
```bash
ssh pi@spotipi.local "
# Create backup
sudo cp -r ~/.spotify_wakeup ~/.spotify_wakeup_backup_$(date +%Y%m%d) 2>/dev/null || true

# Create new directory and migrate data
mkdir -p ~/.spotipi
if [ -d ~/.spotify_wakeup ]; then
  cp -r ~/.spotify_wakeup/* ~/.spotipi/ 2>/dev/null || true
fi
"
```

#### 2.3 Main Directory Migration on Pi
```bash
ssh pi@spotipi.local "
# Backup main directory  
sudo cp -r /home/pi/spotify_wakeup /home/pi/spotify_wakeup_backup_$(date +%Y%m%d) 2>/dev/null || true

# Create new directory
sudo mkdir -p /home/pi/spotipi
sudo chown pi:pi /home/pi/spotipi

# Migrate code (excluding venv - will be recreated)
if [ -d /home/pi/spotify_wakeup ]; then
  rsync -av --exclude='venv/' --exclude='*.log' --exclude='logs/' /home/pi/spotify_wakeup/ /home/pi/spotipi/
fi
"
```

### **Phase 3: Systemd Service Update**

#### 3.1 Create New Service
```bash
ssh pi@spotipi.local "
# Backup old service
sudo cp /etc/systemd/system/spotify-web.service /etc/systemd/system/spotify-web.service.backup

# Create new path-agnostic service
sudo tee /etc/systemd/system/spotify-web.service > /dev/null << 'EOF'
[Unit]
Description=SpotiPi Web Interface
After=network.target

[Service]
User=pi
WorkingDirectory=/home/pi/spotipi
EnvironmentFile=/home/pi/.spotipi/.env
Environment=\"SPOTIPI_APP_NAME=spotipi\"
ExecStart=/home/pi/spotipi/venv/bin/python run.py
Restart=always
Environment=\"PYTHONUNBUFFERED=1\"
Environment=\"PORT=5000\"
StandardOutput=append:/home/pi/spotipi/web.log
StandardError=append:/home/pi/spotipi/web.log

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
"
```

### **Phase 4: Python Environment Setup**

#### 4.1 Recreate Virtual Environment on Pi
```bash
ssh pi@spotipi.local "
cd /home/pi/spotipi
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
"
```

#### 4.2 Migrate .env File
```bash
ssh pi@spotipi.local "
# Copy .env from old directory if available
if [ -f /home/pi/spotify_wakeup/.env ]; then
  mkdir -p /home/pi/.spotipi
  cp /home/pi/spotify_wakeup/.env /home/pi/.spotipi/.env
fi

# Copy .env from config directory if available  
if [ -f ~/.spotipi/.env ]; then
  cp ~/.spotipi/.env /home/pi/.spotipi/.env
fi

chmod 600 /home/pi/.spotipi/.env
"
```

### **Phase 5: Deployment Test**

#### 5.1 First Deployment from New Local Directory
```bash
cd /Users/michi/spotipi-dev/spotipi

# Set environment variables for migration
export SPOTIPI_APP_NAME="spotipi"
export SPOTIPI_PI_PATH="/home/pi/spotipi"

# Execute deployment  
./scripts/deploy_to_pi.sh
```

#### 5.2 Check Service Status
```bash
ssh pi@spotipi.local "
sudo systemctl status spotify-web.service
sudo journalctl -u spotify-web.service -n 20
"
```

#### 5.3 Test Web Interface
```bash
# Open browser or curl test
curl -s -o /dev/null -w "%{http_code}" http://spotipi.local:5000
```

### **Phase 6: Cleanup (after successful migration)**

#### 6.1 Clean up Old Directories on Pi
```bash
ssh pi@spotipi.local "
# After successful test - delete old directories
sudo rm -rf /home/pi/spotify_wakeup
rm -rf ~/.spotify_wakeup

# Backup directories can be deleted after a few days
# sudo rm -rf /home/pi/spotify_wakeup_backup_*
# rm -rf ~/.spotify_wakeup_backup_*
"
```

#### 6.2 Local Cleanup
```bash
cd /Users/michi/spotipi-dev/
# After successful test
rm -rf spotify_wakeup_backup_*
```

## 🔧 **Environment Variables for Customization**

### **Deployment Configuration**
```bash
export SPOTIPI_APP_NAME="spotipi"           # App name (default: spotipi)
export SPOTIPI_PI_HOST="pi@spotipi.local"   # Pi SSH host
export SPOTIPI_PI_PATH="/home/pi/spotipi"   # Pi app directory
export SPOTIPI_LOCAL_PATH="/path/to/local"  # Local project path (auto-detected)
export SPOTIPI_SERVICE_NAME="spotify-web.service"  # Systemd service name
export SPOTIPI_SHOW_STATUS="1"             # Show service status after deployment
```

### **Application Runtime Configuration**  
```bash
export SPOTIPI_APP_NAME="spotipi"           # Affects ~/.spotipi/ config directory
export SPOTIPI_LOG_DIR="/custom/log/path"   # Custom log directory
export SPOTIPI_ENV="production"            # Environment mode
```

## 🛡️ **Rollback Plan**

If problems occur:

### **Service Rollback**
```bash
ssh pi@spotipi.local "
sudo systemctl stop spotify-web.service
sudo cp /etc/systemd/system/spotify-web.service.backup /etc/systemd/system/spotify-web.service
sudo systemctl daemon-reload
sudo systemctl start spotify-web.service
"
```

### **Directory Rollback**
```bash
ssh pi@spotipi.local "
sudo rm -rf /home/pi/spotipi
sudo mv /home/pi/spotify_wakeup_backup_* /home/pi/spotify_wakeup
rm -rf ~/.spotipi  
mv ~/.spotify_wakeup_backup_* ~/.spotify_wakeup
"
```

## ✅ **Migration Checklist**

- [ ] **Phase 1**: Local backup created
- [ ] **Phase 1**: Repository renamed locally to `spotipi`
- [ ] **Phase 1**: New path-agnostic scripts activated  
- [ ] **Phase 2**: Service stopped on Pi
- [ ] **Phase 2**: Config directory migrated (`~/.spotify_wakeup` → `~/.spotipi`)
- [ ] **Phase 2**: Main directory migrated (`/home/pi/spotify_wakeup` → `/home/pi/spotipi`)
- [ ] **Phase 3**: Systemd service updated
- [ ] **Phase 4**: Python venv recreated
- [ ] **Phase 4**: .env file migrated
- [ ] **Phase 5**: Deployment tested
- [ ] **Phase 5**: Web interface working
- [ ] **Phase 6**: Old directories cleaned up

## 🔍 **Migration Verification**

After migration you should verify successful completion:

### **Automatic Verification**

#### **Quick local verification:**
```bash
./scripts/verify_local.sh
```

#### **Complete migration verification:**
```bash
./scripts/verify_migration.sh
```

### **Manual Verification Checklist**

#### **Local Environment:**
- [ ] Working directory named `spotipi`
- [ ] Old scripts backed up as `_old.sh`
- [ ] New scripts are executable
- [ ] Git remote points to correct URL (if changed)

#### **Raspberry Pi Environment:**
- [ ] SSH connection works: `ssh pi@spotipi.local`
- [ ] New main directory exists: `/home/pi/spotipi/`
- [ ] New config directory exists: `~/.spotipi/`
- [ ] Old directories cleaned up or marked as backup

#### **System Service:**
```bash
ssh pi@spotipi.local "sudo systemctl status spotify-web.service"
```
- [ ] Service is `active (running)`
- [ ] Service is `enabled` for autostart
- [ ] No errors in logs

#### **Web Interface:**
- [ ] Web interface accessible: `http://spotipi.local:5000`
- [ ] Main page loads (HTTP 200)
- [ ] API endpoints respond: `/api/alarm_status`, `/api/spotify/library_status`

#### **Python Environment:**
```bash
ssh pi@spotipi.local "cd /home/pi/spotipi && ./venv/bin/python --version"
```
- [ ] Virtual environment working
- [ ] All requirements installed
- [ ] App can be imported

#### **Configuration:**
```bash
ssh pi@spotipi.local "cd /home/pi/spotipi && ./venv/bin/python -c 'from src.config import load_config; print(load_config())'"
```
- [ ] Config loads successfully
- [ ] .env file is present (if needed)
- [ ] Spotify credentials working

### **Verification Output Codes**

The `verify_migration.sh` script returns these exit codes:
- **0**: Completely successful (green)
- **1**: Successful with warnings (yellow) 
- **2**: Failed (red)

### **Troubleshooting Migration Issues**

#### **Service won't start:**
```bash
ssh pi@spotipi.local "sudo journalctl -u spotify-web.service -n 50"
```

#### **Web interface not accessible:**
```bash
ssh pi@spotipi.local "netstat -tlnp | grep :5000"
```

#### **Python import errors:**
```bash
ssh pi@spotipi.local "cd /home/pi/spotipi && ./venv/bin/python -c 'import sys; print(sys.path)'"
```

#### **Config problems:**
```bash
ssh pi@spotipi.local "ls -la ~/.spotipi/ /home/pi/spotipi/config/"
```

## 🎯 **Post-Migration Benefits**

After successful migration you have:
- ✅ **Path-Independence**: No more hardcoded paths
- ✅ **Consistent Naming**: Everything named "spotipi"  
- ✅ **Flexible Configuration**: Customizable via environment variables
- ✅ **Clean Structure**: Old directories cleaned up
- ✅ **Future-Proof**: Easy migration for future changes
- ✅ **Automatic Verification**: Scripts for migration validation
