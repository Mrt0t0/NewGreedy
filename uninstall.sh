#!/usr/bin/env bash
set -e
CERT_DIR=/usr/local/share/ca-certificates
CA_PEM=/var/lib/newgreedy/mitmproxy/mitmproxy-ca-cert.pem
if [ "$(id -u)" != "0" ]; then
  echo "[NewGreedy] Run this uninstaller with sudo."
  exit 1
fi
echo "[NewGreedy] Uninstalling..."
systemctl stop newgreedy 2>/dev/null || true
systemctl disable newgreedy 2>/dev/null || true
rm -f /etc/systemd/system/newgreedy.service
systemctl daemon-reload 2>/dev/null || true
# the CA must not stay trusted once its program is gone - only NewGreedy's own certificate:
# a mitmproxy-ca.crt with other content belongs to another mitmproxy setup
removed=false
if [ -f "$CERT_DIR/newgreedy-mitmproxy-ca.crt" ]; then
  rm -f "$CERT_DIR/newgreedy-mitmproxy-ca.crt"
  removed=true
fi
if [ -f "$CERT_DIR/mitmproxy-ca.crt" ] && [ -f "$CA_PEM" ] && cmp -s "$CA_PEM" "$CERT_DIR/mitmproxy-ca.crt"; then
  rm -f "$CERT_DIR/mitmproxy-ca.crt"
  removed=true
fi
if $removed; then
  update-ca-certificates --fresh >/dev/null 2>&1 || true
  echo "[NewGreedy] mitmproxy CA removed from the system trust store."
fi
rm -rf /var/lib/newgreedy
id newgreedy >/dev/null 2>&1 && userdel newgreedy 2>/dev/null || true
rm -rf /opt/newgreedy
if [ -d /root/.mitmproxy ]; then
  echo "[NewGreedy] /root/.mitmproxy left in place: versions before v1.7.8 kept their CA there,"
  echo "            but other mitmproxy tools use it too. Delete it by hand if it is not needed."
fi
echo "[NewGreedy] Uninstalled - program, data, CA and service account removed."
