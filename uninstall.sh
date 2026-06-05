#!/usr/bin/env bash
set -e
echo "[NewGreedy] Uninstalling..."
systemctl stop newgreedy 2>/dev/null || true
systemctl disable newgreedy 2>/dev/null || true
rm -f /etc/systemd/system/newgreedy.service
systemctl daemon-reload 2>/dev/null || true
rm -rf /opt/newgreedy
echo "[NewGreedy] Uninstalled."
