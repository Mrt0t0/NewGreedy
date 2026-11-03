#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
VERSION=$(sed -n 's/^VERSION *= *"\(.*\)"/\1/p' _version.py)
UPDATE=false
UNINSTALL=false
TRUST_CA=false
MERGE_OPTS=""
for arg in "$@"; do
  [ "$arg" = "--update" ] && UPDATE=true
  [ "$arg" = "--uninstall" ] && UNINSTALL=true
  [ "$arg" = "--trust-ca" ] && TRUST_CA=true
  { [ "$arg" = "--keep-defaults" ] || [ "$arg" = "--keep-scrape" ]; } && MERGE_OPTS="--keep-defaults"
done

DEST="$HOME/Library/Application Support/NewGreedy"
PLIST="$HOME/Library/LaunchAgents/com.mrt0t0.newgreedy.plist"
LABEL="com.mrt0t0.newgreedy"
CA="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"

PROGRAM_FILES=$(grep -v '^[[:space:]]*#' MANIFEST.txt | grep -v '^[[:space:]]*$')

if $UNINSTALL; then
  echo "[NewGreedy] Uninstalling..."
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  # the CA must not stay trusted once its program is gone - this CA only, by its fingerprint:
  # deleting by name would also remove the certificate of any other mitmproxy setup
  if [ -f "$CA" ]; then
    SHA1=$(openssl x509 -noout -fingerprint -sha1 -in "$CA" 2>/dev/null | cut -d= -f2 | tr -d :)
    [ -n "$SHA1" ] && security delete-certificate -Z "$SHA1" "$KEYCHAIN" >/dev/null 2>&1 \
      && echo "[NewGreedy] mitmproxy CA removed from the login keychain." || true
  fi
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

mkdir -p "$DEST/static" "$DEST/torrents"
VENV="$DEST/venv"
if [ -x "$VENV/bin/python" ] || python3 -m venv "$VENV" >/dev/null 2>&1; then
  "$VENV/bin/python" -m pip install -q -r requirements.txt
  PYBIN="$VENV/bin/python"
  echo "[NewGreedy] Dependencies installed in $VENV."
else
  python3 -m pip install -q --break-system-packages -r requirements.txt 2>/dev/null \
    || python3 -m pip install -q -r requirements.txt
  PYBIN=$(command -v python3)
  echo "[NewGreedy] Dependencies installed."
fi

# an installed config.ini is always merged, never replaced
OLD_CFG=""
if [ -f "$DEST/config.ini" ]; then
  OLD_CFG=$(mktemp)
  trap 'rm -f "$OLD_CFG"' EXIT
  cp -p "$DEST/config.ini" "$OLD_CFG"
fi

for f in $PROGRAM_FILES; do
  [ -f "$f" ] && cp -p "$f" "$DEST/$f"
done
cp -p static/* "$DEST/static/"
cp -p config.ini "$DEST/config.ini"

if [ -n "$OLD_CFG" ]; then
  echo "[NewGreedy] Merging config.ini — your settings into the $VERSION template..."
  if "$PYBIN" "$DEST/config_merge.py" --shipped "$DEST/config.ini" \
       --current "$OLD_CFG" --out "$DEST/config.ini" $MERGE_OPTS; then
    echo "[NewGreedy] config.ini merged."
  else
    cp -p "$OLD_CFG" "$DEST/config.ini"
    echo "[NewGreedy] WARNING: merge failed — your previous config.ini was kept untouched."
  fi
  echo "[NewGreedy] Stats, registry, control file and torrents/ preserved."
fi

chmod +x "$DEST/newgreedy.py"
echo "[NewGreedy] Files copied to $DEST"

if [ ! -f "$CA" ]; then
  echo "[NewGreedy] Generating mitmproxy CA..."
  "$PYBIN" -c "from mitmproxy.certs import CertStore; CertStore.from_store('$HOME/.mitmproxy', 'mitmproxy', 2048)" 2>/dev/null || true
fi
if [ -f "$CA" ] && $TRUST_CA; then
  echo "[NewGreedy] Adding CA to the login keychain (password may be requested)..."
  security add-trusted-cert -d -r trustRoot -k "$KEYCHAIN" "$CA" 2>/dev/null \
    && echo "[NewGreedy] CA certificate trusted for every program of this account." \
    || echo "[NewGreedy] WARNING: could not add CA automatically. Import manually: $CA"
elif [ -f "$CA" ] && ! $UPDATE; then
  echo "[NewGreedy] CA NOT added to the login keychain (pass --trust-ca to do so)."
  echo "            HTTPS trackers need the torrent client to trust $CA"
fi

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
echo "  Check  : \"$PYBIN\" \"$DEST/newgreedy.py\" --check-config"
echo ""
echo "  Manage the service:"
echo "    launchctl unload $PLIST   # stop"
echo "    launchctl load   $PLIST   # start"
echo "    tail -f \"$DEST/newgreedy.log\""
echo ""
echo "  In Firefox, import the CA manually:"
echo "    $HOME/.mitmproxy/mitmproxy-ca-cert.pem"
