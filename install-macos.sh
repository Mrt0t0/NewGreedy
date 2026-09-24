#!/usr/bin/env bash
set -e
VERSION="v1.7.7"
UPDATE=false
UNINSTALL=false
MERGE_OPTS=""
for arg in "$@"; do
  [ "$arg" = "--update" ] && UPDATE=true
  [ "$arg" = "--uninstall" ] && UNINSTALL=true
  [ "$arg" = "--keep-scrape" ] && MERGE_OPTS="--keep-scrape"
done

DEST="$HOME/Library/Application Support/NewGreedy"
PLIST="$HOME/Library/LaunchAgents/com.mrt0t0.newgreedy.plist"
LABEL="com.mrt0t0.newgreedy"

PROGRAM_FILES="newgreedy.py newgreedy_addon.py newgreedy_web.py _version.py
               client_profiles.py updater.py config_merge.py requirements.txt
               README.md LICENSE install.sh install-macos.sh install.ps1
               uninstall.sh Dockerfile docker-compose.yml"
DATA_FILES="config.ini stats.json torrent_registry.json control.json purge_pending.json"

if $UNINSTALL; then
  echo "[NewGreedy] Uninstalling..."
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "[NewGreedy] Service removed. Data kept in:"
  echo "  $DEST"
  echo "  Delete it manually if you want a full cleanup."
  exit 0
fi

echo "[NewGreedy $VERSION] Starting install (update=$UPDATE)..."

if ! command -v python3 >/dev/null 2>&1; then
  echo "[NewGreedy] ERROR: python3 not found."
  echo "  Install it with:  brew install python@3.11"
  exit 1
fi

PYVER=$(python3 -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "[NewGreedy] Using Python $PYVER"

python3 -m pip install -q --break-system-packages -r requirements.txt 2>/dev/null \
  || python3 -m pip install -q -r requirements.txt
echo "[NewGreedy] Dependencies installed."

mkdir -p "$DEST/static" "$DEST/torrents"

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
cp -p static/* "$DEST/static/"
cp -p config.ini "$DEST/config.ini"

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
    fi
  fi
  echo "[NewGreedy] Stats, registry, control file and torrents/ preserved."
fi

chmod +x "$DEST/newgreedy.py"
echo "[NewGreedy] Files copied to $DEST"

if ! $UPDATE; then
  echo "[NewGreedy] Generating mitmproxy CA..."
  python3 -c "from mitmproxy.certs import CertStore; CertStore.from_store('$HOME/.mitmproxy', 'mitmproxy')" 2>/dev/null || true
  CA="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
  if [ -f "$CA" ]; then
    echo "[NewGreedy] Adding CA to the login keychain (password may be requested)..."
    security add-trusted-cert -d -r trustRoot \
      -k "$HOME/Library/Keychains/login.keychain-db" "$CA" 2>/dev/null \
      && echo "[NewGreedy] CA certificate trusted." \
      || echo "[NewGreedy] WARNING: could not add CA automatically. Import manually: $CA"
  fi
fi

PYBIN=$(command -v python3)

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYBIN</string>
    <string>$DEST/newgreedy.py</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$DEST</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>StandardOutPath</key>
  <string>$DEST/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>$DEST/launchd.err.log</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "[NewGreedy] launchd agent installed and started."

echo "[NewGreedy $VERSION] Installation complete."
echo "  Proxy  : 127.0.0.1:3456"
echo "  Web UI : http://localhost:8080"
echo "  Watch  : $DEST/torrents  (drop .torrent files here)"
echo ""
echo "  Manage the service:"
echo "    launchctl unload $PLIST   # stop"
echo "    launchctl load   $PLIST   # start"
echo "    tail -f \"$DEST/newgreedy.log\""
echo ""
echo "  In Firefox, import the CA manually:"
echo "    $HOME/.mitmproxy/mitmproxy-ca-cert.pem"
