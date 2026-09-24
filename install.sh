#!/usr/bin/env bash
set -e
VERSION="v1.7.7"
UPDATE=false
MERGE_OPTS=""
for arg in "$@"; do
  [ "$arg" = "--update" ] && UPDATE=true
  [ "$arg" = "--keep-scrape" ] && MERGE_OPTS="--keep-scrape"
done

DEST=/opt/newgreedy
SERVICE=/etc/systemd/system/newgreedy.service
CERT_DIR=/usr/local/share/ca-certificates

PROGRAM_FILES="newgreedy.py newgreedy_addon.py newgreedy_web.py _version.py
               client_profiles.py updater.py config_merge.py requirements.txt
               README.md LICENSE install.sh install-macos.sh install.ps1
               uninstall.sh Dockerfile docker-compose.yml"
DATA_FILES="config.ini stats.json torrent_registry.json control.json purge_pending.json"

echo "[NewGreedy $VERSION] Starting install (update=$UPDATE)..."

python3 -m pip install -q --break-system-packages -r requirements.txt
echo "[NewGreedy] Dependencies installed."

mkdir -p $DEST/static $DEST/torrents

if $UPDATE; then
  SAVE=$(mktemp -d)
  trap 'rm -rf "$SAVE"' EXIT
  for f in $DATA_FILES; do
    [ -f "$DEST/$f" ] && cp -p "$DEST/$f" "$SAVE/$f"
  done
fi

for f in $PROGRAM_FILES; do
  [ -f "$f" ] && cp -p "$f" "$DEST/$f"
done
cp -p static/* $DEST/static/
cp -p config.ini $DEST/config.ini

if $UPDATE; then
  for f in stats.json torrent_registry.json control.json purge_pending.json; do
    [ -f "$SAVE/$f" ] && cp -p "$SAVE/$f" "$DEST/$f"
  done
  if [ -f "$SAVE/config.ini" ]; then
    echo "[NewGreedy] Merging config.ini — your settings into the $VERSION template..."
    if python3 "$DEST/config_merge.py" --shipped "$DEST/config.ini" \
         --current "$SAVE/config.ini" --out "$DEST/config.ini" $MERGE_OPTS; then
      echo "[NewGreedy] config.ini merged."
    else
      cp -p "$SAVE/config.ini" "$DEST/config.ini"
      echo "[NewGreedy] WARNING: merge failed — your previous config.ini was kept untouched."
      echo "[NewGreedy] The new keys will fall back to their defaults until you add them."
    fi
  fi
  echo "[NewGreedy] Stats, registry, control file and torrents/ preserved."
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
