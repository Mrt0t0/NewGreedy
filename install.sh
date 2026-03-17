#!/bin/bash
# Installation script for NewGreedy v1.0
# MrT0t0 - https://github.com/Mrt0t0/NewGreedy/
#
# Usage:
#   Fresh install : sudo ./install.sh
#   Update        : sudo ./install.sh
#
# This script clones or updates the repo directly into INSTALL_DIR,
# so that 'git pull' works from /opt/newgreedy on every future update.

set -euo pipefail

# ── Variables ──────────────────────────────────────────────────────────────────
REPO_URL="https://github.com/Mrt0t0/NewGreedy.git"
INSTALL_DIR="/opt/newgreedy"
SERVICE_NAME="newgreedy.service"
SERVICE_USER="newgreedy"
PYTHON_PATH=$(which python3)
VERSION="1.0"

# ── Colors ─────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC}    $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $1"; }
fail() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
info() { echo -e "        $1"; }

# ── Root check ─────────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    fail "Please run this script as root or with sudo."
fi

echo ""
echo "=================================================="
echo "   NewGreedy v${VERSION} — Installation"
echo "=================================================="
echo ""

# ── Dependencies check ─────────────────────────────────────────────────────────
command -v git      &>/dev/null || fail "git is required. Install with: apt install git"
command -v python3  &>/dev/null || fail "python3 is required. Install with: apt install python3"

if command -v openssl &>/dev/null; then
    ok "openssl detected — automatic self-signed certificate generation available"
else
    warn "openssl not found. Automatic certificate generation will not be available."
    info "Install with: apt install openssl"
fi

# ── Detect update vs fresh install ────────────────────────────────────────────
IS_UPDATE=false
if [[ -d "$INSTALL_DIR/.git" ]]; then
    IS_UPDATE=true
    warn "Existing git installation detected in $INSTALL_DIR."
    info "Running git pull to update..."
elif [[ -d "$INSTALL_DIR" ]]; then
    # Directory exists but is NOT a git repo (legacy manual install)
    warn "Existing non-git installation detected. Migrating to git-based install..."
    if [[ -f "$INSTALL_DIR/config.ini" ]]; then
        cp "$INSTALL_DIR/config.ini" /tmp/config.ini.bak_$(date +%Y%m%d_%H%M%S)
        ok "Config backed up to /tmp/"
    fi
    rm -rf "$INSTALL_DIR"
    info "Old installation removed."
fi

# ── Stop service if running ───────────────────────────────────────────────────
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    systemctl stop "$SERVICE_NAME"
    ok "Service stopped"
fi

# ── Clone or update repo directly into INSTALL_DIR ───────────────────────────
if $IS_UPDATE; then
    # Preserve config.ini across git pull
    if [[ -f "$INSTALL_DIR/config.ini" ]]; then
        cp "$INSTALL_DIR/config.ini" /tmp/config.ini.bak_$(date +%Y%m%d_%H%M%S)
        ok "Config backed up to /tmp/"
    fi
    cd "$INSTALL_DIR"
    git fetch origin main
    git reset --hard origin/main
    ok "Repository updated to latest version"
else
    git clone "$REPO_URL" "$INSTALL_DIR"
    ok "Repository cloned to $INSTALL_DIR"
fi

# ── Restore config if it was backed up ────────────────────────────────────────
LATEST_BACKUP=$(ls -t /tmp/config.ini.bak_* 2>/dev/null | head -1 || true)
if [[ -n "$LATEST_BACKUP" ]]; then
    cp "$LATEST_BACKUP" "$INSTALL_DIR/config.ini"
    ok "Config restored from $LATEST_BACKUP"
    info "Review $INSTALL_DIR/config.ini.new for any new parameters"
    cp "$INSTALL_DIR/config.ini" "$INSTALL_DIR/config.ini.new" 2>/dev/null || true
fi

# ── Create dedicated system user ──────────────────────────────────────────────
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    ok "System user '$SERVICE_USER' created"
else
    ok "User '$SERVICE_USER' already exists"
fi

# ── Permissions ───────────────────────────────────────────────────────────────
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"
chmod 750 "$INSTALL_DIR"
chmod 640 "$INSTALL_DIR/config.ini" 2>/dev/null || true
ok "Permissions applied on $INSTALL_DIR"

# ── Python dependencies ───────────────────────────────────────────────────────
$PYTHON_PATH -m pip install --upgrade --quiet requests
ok "Python dependencies installed"

# ── Log file setup ────────────────────────────────────────────────────────────
LOG_FILE=$(grep -i 'log_file' "$INSTALL_DIR/config.ini" 2>/dev/null \
    | head -1 | cut -d'=' -f2 | tr -d ' ' || echo "newgreedy.log")
if [[ "$LOG_FILE" != /* ]]; then
    LOG_FILE="$INSTALL_DIR/$LOG_FILE"
fi
touch "$LOG_FILE"
chown "$SERVICE_USER":"$SERVICE_USER" "$LOG_FILE"
ok "Log file: $LOG_FILE"

# ── systemd service ───────────────────────────────────────────────────────────
cat > /etc/systemd/system/$SERVICE_NAME << EOF
[Unit]
Description=NewGreedy BitTorrent Proxy v${VERSION}
After=network.target
Documentation=https://github.com/Mrt0t0/NewGreedy

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${PYTHON_PATH} ${INSTALL_DIR}/newgreedy.py
Restart=on-failure
RestartSec=10s
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=${INSTALL_DIR}
StandardOutput=append:${LOG_FILE}
StandardError=append:${LOG_FILE}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

# ── Startup check ─────────────────────────────────────────────────────────────
sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "Service started successfully"
else
    fail "Service failed to start. Check: journalctl -u $SERVICE_NAME -n 30"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=================================================="
echo -e "${GREEN}   NewGreedy v${VERSION} successfully installed!${NC}"
echo "=================================================="
echo ""
echo "  Directory  : $INSTALL_DIR"
echo "  Git remote : $REPO_URL"
echo "  Service    : $SERVICE_NAME (auto-start enabled)"
echo "  Log        : $LOG_FILE"
echo "  User       : $SERVICE_USER"
echo ""
echo "  Useful commands:"
echo "    sudo systemctl status $SERVICE_NAME"
echo "    sudo journalctl -u $SERVICE_NAME -f"
echo "    tail -f $LOG_FILE"
echo ""
echo "  Future updates (one command):"
echo "    cd $INSTALL_DIR && git pull origin main && systemctl restart $SERVICE_NAME"
echo ""
if $IS_UPDATE; then
    echo -e "  ${YELLOW}Update complete. Review config.ini vs config.ini.new for new parameters.${NC}"
    echo ""
fi
echo "  To enable HTTPS, edit $INSTALL_DIR/config.ini:"
echo "    enable_https = true"
echo ""
