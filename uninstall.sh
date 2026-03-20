#!/bin/bash
# NewGreedy v1.1 — Linux uninstaller
set -e

INSTALL_DIR="/opt/newgreedy"
SERVICE_FILE="/etc/systemd/system/newgreedy.service"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  NewGreedy Uninstaller"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

[[ $EUID -ne 0 ]] && { echo "Run as root: sudo ./uninstall.sh"; exit 1; }

echo "[1/4] Stopping and disabling service..."
systemctl stop    newgreedy.service 2>/dev/null || true
systemctl disable newgreedy.service 2>/dev/null || true

echo "[2/4] Removing service file..."
rm -f "${SERVICE_FILE}"
systemctl daemon-reload

echo "[3/4] Removing installation directory..."
rm -rf "${INSTALL_DIR}"

echo "[4/4] Removing mitmproxy CA (if installed)..."
if [ -f /usr/local/share/ca-certificates/mitmproxy.crt ]; then
  rm -f /usr/local/share/ca-certificates/mitmproxy.crt
  update-ca-certificates
  echo "  mitmproxy CA removed"
else
  echo "  No mitmproxy CA found — skipping"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  NewGreedy uninstalled successfully."
echo "  pip3 uninstall mitmproxy requests  (optional)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
