#!/usr/bin/env bash
set -e
VERSION="v1.7.6"
UPDATE=false
for arg in "$@"; do [ "$arg" = "--update" ] && UPDATE=true; done

DEST=/opt/newgreedy
SERVICE=/etc/systemd/system/newgreedy.service
CERT_DIR=/usr/local/share/ca-certificates

echo "[NewGreedy $VERSION] Starting install (update=$UPDATE)..."

python3 -m pip install -q --break-system-packages -r requirements.txt
echo "[NewGreedy] Dependencies installed."

mkdir -p $DEST/static $DEST/torrents
if $UPDATE; then
  for f in config.ini stats.json torrent_registry.json; do
    [ -f "$DEST/$f" ] && cp "$DEST/$f" "/tmp/ng_$f.bak" 2>/dev/null || true
  done
fi
cp -r . $DEST/
if $UPDATE; then
  for f in config.ini stats.json torrent_registry.json; do
    [ -f "/tmp/ng_$f.bak" ] && cp "/tmp/ng_$f.bak" "$DEST/$f" && rm -f "/tmp/ng_$f.bak"
  done
  echo "[NewGreedy] Existing config.ini and stats preserved."
fi
chmod +x $DEST/newgreedy.py
echo "[NewGreedy] Files copied to $DEST"

if ! $UPDATE; then
  echo "[NewGreedy] Generating mitmproxy CA..."
  python3 -c "from mitmproxy.certs import CertStore; CertStore.from_store('$HOME/.mitmproxy', 'mitmproxy')" 2>/dev/null || true
  if [ -f "$HOME/.mitmproxy/mitmproxy-ca-cert.pem" ]; then
    cp "$HOME/.mitmproxy/mitmproxy-ca-cert.pem" "$CERT_DIR/mitmproxy-ca.crt" 2>/dev/null || true
    update-ca-certificates 2>/dev/null || true
    echo "[NewGreedy] CA certificate trusted."
  fi
fi

if ! $UPDATE; then
  cat > $SERVICE <<EOF
[Unit]
Description=NewGreedy $VERSION - BitTorrent ratio spoofer
After=network.target

[Service]
Type=simple
WorkingDirectory=$DEST
ExecStart=/usr/bin/python3 $DEST/newgreedy.py
Restart=on-failure
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=20
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable newgreedy
  systemctl start newgreedy
  echo "[NewGreedy] systemd service installed and started."
else
  systemctl restart newgreedy 2>/dev/null || true
  echo "[NewGreedy] Service restarted."
fi

echo "[NewGreedy $VERSION] Installation complete."
echo "  Proxy  : 127.0.0.1:3456"
echo "  Web UI : http://localhost:8080"
echo "  Watch  : $DEST/torrents  (drop .torrent files here)"
