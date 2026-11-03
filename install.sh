#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
VERSION=$(sed -n 's/^VERSION *= *"\(.*\)"/\1/p' _version.py)
UPDATE=false
TRUST_CA=false
MERGE_OPTS=""
for arg in "$@"; do
  [ "$arg" = "--update" ] && UPDATE=true
  [ "$arg" = "--trust-ca" ] && TRUST_CA=true
  [ "$arg" = "--no-trust-ca" ] && TRUST_CA=false   # the default since this version
  { [ "$arg" = "--keep-defaults" ] || [ "$arg" = "--keep-scrape" ]; } && MERGE_OPTS="--keep-defaults"
done

DEST=/opt/newgreedy
SERVICE=/etc/systemd/system/newgreedy.service
CERT_DIR=/usr/local/share/ca-certificates
CERT_FILE=$CERT_DIR/newgreedy-mitmproxy-ca.crt
LEGACY_CERT=$CERT_DIR/mitmproxy-ca.crt
SVC_USER=newgreedy
STATE_DIR=/var/lib/newgreedy
CONFDIR=$STATE_DIR/mitmproxy
CA_PEM=$CONFDIR/mitmproxy-ca-cert.pem
CLIENT_BUNDLE=$STATE_DIR/client-ca-bundle.pem
VENV=$DEST/venv

PROGRAM_FILES=$(grep -v '^[[:space:]]*#' MANIFEST.txt | grep -v '^[[:space:]]*$')

if [ "$(id -u)" != "0" ]; then
  echo "[NewGreedy] Run this installer with sudo."
  exit 1
fi

echo "[NewGreedy $VERSION] Starting install (update=$UPDATE)..."

# ── service account ──────────────────────────────────────────────────────────
if ! id "$SVC_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$STATE_DIR" --create-home --shell /usr/sbin/nologin "$SVC_USER"
  echo "[NewGreedy] System user '$SVC_USER' created."
fi
mkdir -p "$DEST/static" "$DEST/torrents" "$CONFDIR"

# ── Python environment: a venv, the system Python as a fallback ──────────────
PYBIN=/usr/bin/python3
if [ -x "$VENV/bin/python" ] || python3 -m venv "$VENV" >/dev/null 2>&1; then
  "$VENV/bin/python" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
  "$VENV/bin/python" -m pip install -q -r requirements.txt
  PYBIN=$VENV/bin/python
  echo "[NewGreedy] Dependencies installed in $VENV."
else
  echo "[NewGreedy] WARNING: python3-venv is missing (apt install python3-venv) -"
  echo "            installing into the system Python instead."
  python3 -m pip install -q --break-system-packages -r requirements.txt 2>/dev/null \
    || python3 -m pip install -q -r requirements.txt
  echo "[NewGreedy] Dependencies installed."
fi

# ── program files; an installed config.ini is always merged, never replaced ──
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
    echo "[NewGreedy] The new keys will fall back to their defaults until you add them."
  fi
  echo "[NewGreedy] Stats, registry, control file, trash and torrents/ preserved."
fi
# versions before v1.7.8 ran as root: keep their CA so the torrent client still trusts it
if [ -z "$(ls -A "$CONFDIR" 2>/dev/null)" ] && [ -d /root/.mitmproxy ]; then
  cp -a /root/.mitmproxy/. "$CONFDIR/"
  echo "[NewGreedy] mitmproxy CA copied from /root/.mitmproxy to $CONFDIR."
fi
chmod +x $DEST/newgreedy.py
rm -rf "$DEST/.git"
chown -R "$SVC_USER:$SVC_USER" "$DEST" "$STATE_DIR"
chmod 700 "$CONFDIR"
echo "[NewGreedy] Files copied to $DEST"

# ── mitmproxy CA ─────────────────────────────────────────────────────────────
if [ ! -f "$CA_PEM" ]; then
  echo "[NewGreedy] Generating mitmproxy CA..."
  runuser -u "$SVC_USER" -- "$PYBIN" -c "from mitmproxy.certs import CertStore; CertStore.from_store('$CONFDIR', 'mitmproxy', 2048)" 2>/dev/null || true
fi
chmod 600 "$CONFDIR"/mitmproxy-ca.pem "$CONFDIR"/mitmproxy-ca.p12 2>/dev/null || true

# earlier installers trusted the CA under a generic name: take it over when it is ours
if [ -f "$CA_PEM" ] && [ -f "$LEGACY_CERT" ] && cmp -s "$CA_PEM" "$LEGACY_CERT"; then
  mv "$LEGACY_CERT" "$CERT_FILE"
fi
if [ -f "$CA_PEM" ] && { $TRUST_CA || [ -f "$CERT_FILE" ]; }; then
  cp "$CA_PEM" "$CERT_FILE"
  update-ca-certificates >/dev/null 2>&1 || true
  echo "[NewGreedy] CA certificate trusted system-wide (HTTPS trackers need it)."
  echo "            Anyone who obtains its private key in $CONFDIR can intercept TLS on this"
  echo "            machine: it stays readable by '$SVC_USER' only."
elif [ -f "$CA_PEM" ]; then
  # a bundle only the torrent client uses: the rest of the system keeps its own trust store
  SYS_BUNDLE=/etc/ssl/certs/ca-certificates.crt
  { [ -f "$SYS_BUNDLE" ] && cat "$SYS_BUNDLE"; cat "$CA_PEM"; } > "$CLIENT_BUNDLE"
  chmod 644 "$CLIENT_BUNDLE"
  echo "[NewGreedy] CA NOT trusted system-wide (pass --trust-ca to do so)."
  echo "            HTTPS trackers need the torrent client to trust it - start the client with:"
  echo "              SSL_CERT_FILE=$CLIENT_BUNDLE CURL_CA_BUNDLE=$CLIENT_BUNDLE <client>"
  echo "            (re-run the installer after a ca-certificates update to refresh the bundle)"
fi

# ── systemd unit, regenerated on every install and update ────────────────────
cat > $SERVICE <<EOF
[Unit]
Description=NewGreedy $VERSION - BitTorrent ratio spoofer
After=network.target

[Service]
Type=simple
User=$SVC_USER
Group=$SVC_USER
WorkingDirectory=$DEST
Environment=NEWGREEDY_MITM_CONFDIR=$CONFDIR
Environment=PYTHONUNBUFFERED=1
ExecStart=$PYBIN $DEST/newgreedy.py
Restart=on-failure
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=20
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable newgreedy >/dev/null 2>&1
if $UPDATE; then
  systemctl restart newgreedy 2>/dev/null || true
  echo "[NewGreedy] Service restarted."
else
  systemctl restart newgreedy
  echo "[NewGreedy] systemd service installed and started."
fi

echo "[NewGreedy $VERSION] Installation complete."
echo "  Proxy  : 127.0.0.1:3456"
echo "  Web UI : http://localhost:8080"
echo "  Watch  : $DEST/torrents  (drop .torrent files here)"
echo "  Check  : sudo -u $SVC_USER NEWGREEDY_MITM_CONFDIR=$CONFDIR $PYBIN $DEST/newgreedy.py --check-config"
