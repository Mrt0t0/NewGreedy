#!/bin/bash
# Installation script for NewGreedy v1.0
# MrT0t0 - https://github.com/Mrt0t0/NewGreedy/

set -euo pipefail

# ── Variables ──────────────────────────────────────────────────────────────────
INSTALL_DIR="/opt/newgreedy"
SCRIPT_NAME="newgreedy.py"
CONFIG_NAME="config.ini"
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

# ── Source files check ─────────────────────────────────────────────────────────
[[ -f "$SCRIPT_NAME" ]] || fail "Source file '$SCRIPT_NAME' not found in current directory."
[[ -f "$CONFIG_NAME" ]] || fail "Config file '$CONFIG_NAME' not found in current directory."

# ── Detect update vs fresh install ────────────────────────────────────────────
IS_UPDATE=false
if [[ -d "$INSTALL_DIR" ]]; then
    IS_UPDATE=true
    warn "Existing installation detected in $INSTALL_DIR."
    info "Your config.ini will be preserved."
fi

# ── Backup config on update ───────────────────────────────────────────────────
if $IS_UPDATE && [[ -f "$INSTALL_DIR/$CONFIG_NAME" ]]; then
    BACKUP="$INSTALL_DIR/config.ini.bak_$(date +%Y%m%d_%H%M%S)"
    cp "$INSTALL_DIR/$CONFIG_NAME" "$BACKUP"
    ok "Config backed up → $BACKUP"
fi

# ── Stop service if running ───────────────────────────────────────────────────
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    systemctl stop "$SERVICE_NAME"
    ok "Service stopped"
fi

# ── Create dedicated system user ──────────────────────────────────────────────
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    ok "System user '$SERVICE_USER' created"
else
    ok "User '$SERVICE_USER' already exists"
fi

# ── Install files ─────────────────────────────────────────────────────────────
mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_NAME" "$INSTALL_DIR/"

if ! $IS_UPDATE || [[ ! -f "$INSTALL_DIR/$CONFIG_NAME" ]]; then
    cp "$CONFIG_NAME" "$INSTALL_DIR/"
    ok "config.ini installed"
else
    warn "Existing config.ini preserved (new template saved as config.ini.new)"
    cp "$CONFIG_NAME" "$INSTALL_DIR/config.ini.new"
fi

chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR"
chmod 750 "$INSTALL_DIR"
chmod 640 "$INSTALL_DIR/$CONFIG_NAME" 2>/dev/null || true
ok "Permissions applied on $INSTALL_DIR"

# ── Python dependencies ───────────────────────────────────────────────────────
echo ""
info "Installing Python dependencies..."
$PYTHON_PATH -m pip install --upgrade --quiet requests
ok "requests installed/updated"

# ── Check openssl availability ────────────────────────────────────────────────
if command -v openssl &>/dev/null; then
    ok "openssl detected — automatic self-signed certificate generation available"
else
    warn "openssl not found. Automatic certificate generation will not be available."
    info "Install it with: apt install openssl  /  yum install openssl"
fi

# ── Log file setup ────────────────────────────────────────────────────────────
LOG_FILE=$(grep -i 'log_file' "$INSTALL_DIR/$CONFIG_NAME" 2>/dev/null \
    | head -1 | cut -d'=' -f2 | tr -d ' ' || echo "newgreedy.log")
if [[ "$LOG_FILE" != /* ]]; then
    LOG_FILE="$INSTALL_DIR/$LOG_FILE"
fi
touch "$LOG_FILE"
chown "$SERVICE_USER":"$SERVICE_USER" "$LOG_FILE"
ok "Log file: $LOG_FILE"

# ── systemd service ───────────────────────────────────────────────────────────
echo ""
info "Creating systemd service..."

cat > /etc/systemd/system/$SERVICE_NAME << EOF
[Unit]
Description=NewGreedy BitTorrent Proxy v${VERSION}
After=network.target
Documentation=https://github.com/Mrt0t0/NewGreedy

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${PYTHON_PATH} ${INSTALL_DIR}/${SCRIPT_NAME}
Restart=on-failure
RestartSec=10s

; Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=${INSTALL_DIR}

; Logging
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
    fail "Service failed to start. Check logs: journalctl -u $SERVICE_NAME -n 30"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=================================================="
echo -e "${GREEN}   MrT0t0 - NewGreedy v${VERSION} successfully installed!${NC}"
echo "=================================================="
echo ""
echo "  Directory     : $INSTALL_DIR"
echo "  Service       : $SERVICE_NAME (auto-start enabled)"
echo "  Log           : $LOG_FILE"
echo "  User          : $SERVICE_USER"
echo ""
echo "  Useful commands:"
echo "    sudo systemctl status $SERVICE_NAME"
echo "    sudo journalctl -u $SERVICE_NAME -f"
echo "    sudo systemctl restart $SERVICE_NAME"
echo ""
if $IS_UPDATE; then
    echo -e "  ${YELLOW}Update complete.${NC}"
    echo "  Review config.ini.new for new HTTPS parameters."
    echo ""
fi
echo "  To enable HTTPS, edit $INSTALL_DIR/config.ini:"
echo "    enable_https = true"
echo ""
