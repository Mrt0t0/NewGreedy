#!/bin/bash
# Installation script for NewGreedy v1.0
# MrT0t0 - https://github.com/Mrt0t0/NewGreedy/
#
# Usage:
#   Standard mode  (HTTP trackers) : sudo ./install.sh
#   mitmproxy mode (HTTPS trackers): sudo ./install.sh --mitmproxy
#
# Clones or updates the repo directly into INSTALL_DIR
# so that future updates work with: cd /opt/newgreedy && git pull origin main

set -euo pipefail

# ── Variables ──────────────────────────────────────────────────────────────────
REPO_URL="https://github.com/Mrt0t0/NewGreedy.git"
INSTALL_DIR="/opt/newgreedy"
SERVICE_NAME="newgreedy.service"
PYTHON_PATH=$(which python3)
VERSION="1.0"
MODE="standard"

for arg in "$@"; do
    case $arg in
        --mitmproxy) MODE="mitmproxy" ;;
    esac
done

# ── Colors ─────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[OK]${NC}    $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $1"; }
fail() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
info() { echo -e "        $1"; }

# ── Root check ─────────────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && fail "Please run as root or with sudo."

echo ""
echo "=================================================="
echo "   NewGreedy v${VERSION} — Installation (${MODE} mode)"
echo "=================================================="
echo ""

# ── Dependencies ──────────────────────────────────────────────────────────────
command -v git     &>/dev/null || fail "git is required: apt install git"
command -v python3 &>/dev/null || fail "python3 is required: apt install python3"
command -v openssl &>/dev/null && ok "openssl detected" || warn "openssl not found (optional)"

if [[ "$MODE" == "mitmproxy" ]]; then
    if ! command -v mitmdump &>/dev/null; then
        warn "mitmdump not found — installing mitmproxy..."
        $PYTHON_PATH -m pip install --quiet mitmproxy
        ok "mitmproxy installed"
    else
        ok "mitmdump detected: $(which mitmdump)"
    fi
fi

# ── Detect existing installation ──────────────────────────────────────────────
IS_UPDATE=false

# Allow git to access the directory even if owned by root
git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true

if [[ -d "$INSTALL_DIR/.git" ]]; then
    IS_UPDATE=true
    warn "Existing git installation detected — updating..."
elif [[ -d "$INSTALL_DIR" ]]; then
    warn "Non-git installation detected — migrating to git..."
    [[ -f "$INSTALL_DIR/config.ini" ]] && {
        cp "$INSTALL_DIR/config.ini" /tmp/config.ini.bak_$(date +%Y%m%d_%H%M%S)
        ok "Config backed up to /tmp/"
    }
    rm -rf "$INSTALL_DIR"
fi

# ── Stop service ───────────────────────────────────────────────────────────────
systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null && {
    systemctl stop "$SERVICE_NAME"
    ok "Service stopped"
}

# ── Clone or update ────────────────────────────────────────────────────────────
if $IS_UPDATE; then
    [[ -f "$INSTALL_DIR/config.ini" ]] && {
        cp "$INSTALL_DIR/config.ini" /tmp/config.ini.bak_$(date +%Y%m%d_%H%M%S)
        ok "Config backed up to /tmp/"
    }
    cd "$INSTALL_DIR"
    git fetch origin main
    git reset --hard origin/main
    ok "Repository updated"
else
    git clone "$REPO_URL" "$INSTALL_DIR"
    ok "Repository cloned to $INSTALL_DIR"
fi

# ── Restore config ─────────────────────────────────────────────────────────────
LATEST_BACKUP=$(ls -t /tmp/config.ini.bak_* 2>/dev/null | head -1 || true)
if [[ -n "$LATEST_BACKUP" ]]; then
    cp "$LATEST_BACKUP" "$INSTALL_DIR/config.ini"
    cp "$INSTALL_DIR/config.ini" "$INSTALL_DIR/config.ini.new" 2>/dev/null || true
    ok "Config restored — review config.ini.new for new parameters"
fi

# ── Permissions (run as root) ─────────────────────────────────────────────────
chown -R root:root "$INSTALL_DIR"
chmod 755 "$INSTALL_DIR"
chmod 644 "$INSTALL_DIR/config.ini" 2>/dev/null || true
ok "Permissions applied (root)"

# ── Python deps ────────────────────────────────────────────────────────────────
$PYTHON_PATH -m pip install --upgrade --quiet requests
ok "requests installed"
if [[ "$MODE" == "mitmproxy" ]]; then
    $PYTHON_PATH -m pip install --upgrade --quiet mitmproxy
    ok "mitmproxy installed/updated"
fi

# ── Log file ───────────────────────────────────────────────────────────────────
LOG_FILE=$(grep -i 'log_file' "$INSTALL_DIR/config.ini" 2>/dev/null \
    | head -1 | cut -d'=' -f2 | tr -d ' ' || echo "newgreedy.log")
[[ "$LOG_FILE" != /* ]] && LOG_FILE="$INSTALL_DIR/$LOG_FILE"
touch "$LOG_FILE"
chmod 644 "$LOG_FILE"
ok "Log file: $LOG_FILE"

# ── Resolve mitmdump path ──────────────────────────────────────────────────────
if [[ "$MODE" == "mitmproxy" ]]; then
    MITMDUMP_PATH=$(which mitmdump 2>/dev/null || echo "/usr/local/bin/mitmdump")
    EXEC_START="$MITMDUMP_PATH -p 3456 --ssl-insecure -s ${INSTALL_DIR}/newgreedy_addon.py"
    DESCRIPTION="NewGreedy v${VERSION} — mitmproxy mode (HTTP+HTTPS)"
else
    EXEC_START="${PYTHON_PATH} ${INSTALL_DIR}/newgreedy.py"
    DESCRIPTION="NewGreedy v${VERSION} — standard mode (HTTP)"
fi

# ── systemd service (runs as root) ────────────────────────────────────────────
cat > /etc/systemd/system/$SERVICE_NAME << EOF
[Unit]
Description=${DESCRIPTION}
After=network.target
Documentation=https://github.com/Mrt0t0/NewGreedy

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
ExecStart=${EXEC_START}
Restart=on-failure
RestartSec=10s
StandardOutput=append:${LOG_FILE}
StandardError=append:${LOG_FILE}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

# ── Startup check ─────────────────────────────────────────────────────────────
sleep 3
if systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "Service started successfully"
else
    echo ""
    warn "Service did not start — full error below:"
    journalctl -u "$SERVICE_NAME" -n 20 --no-pager
    fail "Fix the error above then run: systemctl restart $SERVICE_NAME"
fi

# ── Summary ────────────────────────────────────────────────────────────────────
echo ""
echo "=================================================="
echo -e "${GREEN}   NewGreedy v${VERSION} installed! [${MODE} mode]${NC}"
echo "=================================================="
echo ""
echo "  Directory  : $INSTALL_DIR"
echo "  Mode       : $MODE"
echo "  User       : root"
echo "  Service    : $SERVICE_NAME"
echo "  Log        : $LOG_FILE"
echo ""
echo "  Commands:"
echo "    systemctl status $SERVICE_NAME"
echo "    journalctl -u $SERVICE_NAME -f"
echo "    tail -f $LOG_FILE"
echo ""
echo "  Future updates:"
echo "    cd $INSTALL_DIR && git pull origin main && systemctl restart $SERVICE_NAME"
echo ""
if [[ "$MODE" == "mitmproxy" ]]; then
    echo -e "  ${YELLOW}HTTPS setup — install mitmproxy CA in qBittorrent:${NC}"
    echo "    1. CA location: ~/.mitmproxy/mitmproxy-ca-cert.pem"
    echo "    2. Install CA : cp ~/.mitmproxy/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/mitmproxy.crt && update-ca-certificates"
    echo "    3. In qBittorrent: Tools → Options → Connection → Proxy → HTTP / 127.0.0.1 / 3456"
    echo ""
fi
