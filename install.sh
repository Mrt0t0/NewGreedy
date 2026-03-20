#!/bin/bash
# NewGreedy v1.1 — Linux installer
# Usage:
#   sudo ./install.sh             # Standard mode
#   sudo ./install.sh --mitmproxy # mitmproxy mode (HTTPS trackers)
set -e

VERSION="1.1"
INSTALL_DIR="/opt/newgreedy"
SERVICE_FILE="/etc/systemd/system/newgreedy.service"
MITMPROXY_MODE=false

for arg in "$@"; do
  [[ "$arg" == "--mitmproxy" ]] && MITMPROXY_MODE=true
done

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  NewGreedy v${VERSION} Installer"
echo "  Mode: $([ "$MITMPROXY_MODE" == "true" ] && echo mitmproxy || echo standard)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

[[ $EUID -ne 0 ]] && { echo "Run as root: sudo ./install.sh"; exit 1; }

# ── Dependencies ──────────────────────────────────────────────────────────────
echo "[1/6] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip git openssl

echo "[2/6] Installing Python dependencies..."
pip3 install requests
if [ "$MITMPROXY_MODE" == "true" ]; then
  pip3 install mitmproxy
fi

# ── Copy files ────────────────────────────────────────────────────────────────
echo "[3/6] Installing NewGreedy to ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
cp newgreedy.py newgreedy_addon.py "${INSTALL_DIR}/"
if [ ! -f "${INSTALL_DIR}/config.ini" ]; then
  cp config.ini "${INSTALL_DIR}/"
  echo "  config.ini copied (first install)"
else
  echo "  config.ini preserved (existing install)"
fi
chmod +x "${INSTALL_DIR}/newgreedy.py"
git config --global --add safe.directory "${INSTALL_DIR}" 2>/dev/null || true

# ── systemd service ───────────────────────────────────────────────────────────
echo "[4/6] Creating systemd service..."

if [ "$MITMPROXY_MODE" == "true" ]; then
  EXEC_START="/usr/local/bin/mitmdump -p 3456 --ssl-insecure -s ${INSTALL_DIR}/newgreedy_addon.py"
else
  EXEC_START="/usr/bin/python3 ${INSTALL_DIR}/newgreedy.py"
fi

cat > "${SERVICE_FILE}" << EOF
[Unit]
Description=NewGreedy v${VERSION} BitTorrent Proxy
After=network.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=${EXEC_START}
Restart=on-failure
RestartSec=5
StandardOutput=append:${INSTALL_DIR}/newgreedy.log
StandardError=append:${INSTALL_DIR}/newgreedy.log

[Install]
WantedBy=multi-user.target
EOF

# ── CA setup (mitmproxy) ──────────────────────────────────────────────────────
if [ "$MITMPROXY_MODE" == "true" ]; then
  echo "[5/6] Generating mitmproxy CA (first run)..."
  cd "${INSTALL_DIR}"
  timeout 3 mitmdump -p 3456 --ssl-insecure -s newgreedy_addon.py 2>/dev/null || true
  CA_SRC="${HOME}/.mitmproxy/mitmproxy-ca-cert.pem"
  if [ -f "${CA_SRC}" ]; then
    cp "${CA_SRC}" /usr/local/share/ca-certificates/mitmproxy.crt
    update-ca-certificates
    echo "  CA installed system-wide"
  else
    echo "  WARNING: CA not found — run mitmdump once and install CA manually"
  fi
else
  echo "[5/6] Skipping CA setup (standard mode)"
fi

# ── Enable service ────────────────────────────────────────────────────────────
echo "[6/6] Enabling and starting service..."
systemctl daemon-reload
systemctl enable newgreedy.service
systemctl restart newgreedy.service

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  NewGreedy v${VERSION} installed successfully!"
echo "  Status : systemctl status newgreedy.service"
echo "  Logs   : tail -f ${INSTALL_DIR}/newgreedy.log"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
