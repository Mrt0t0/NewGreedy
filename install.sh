#!/usr/bin/env bash
# NewGreedy v1.4 installer
# Usage: sudo ./install.sh [--update]
set -euo pipefail

INSTALL_DIR="/opt/newgreedy"
SERVICE_FILE="/etc/systemd/system/newgreedy.service"
CA_DIR="${HOME}/.mitmproxy"
CA_SRC="${CA_DIR}/mitmproxy-ca-cert.pem"
CA_DST="/usr/local/share/ca-certificates/mitmproxy-newgreedy.crt"

info()  { echo -e "\e[32m[+]\e[0m $*"; }
error() { echo -e "\e[31m[!]\e[0m $*" >&2; exit 1; }

[[ $EUID -ne 0 ]] && error "Run as root: sudo ./install.sh"

if [[ "${1:-}" == "--update" ]]; then
    info "Updating NewGreedy..."
    cd "$INSTALL_DIR"
    git fetch origin
    git reset --hard origin/main
    systemctl restart newgreedy.service
    info "Updated and restarted."
    exit 0
fi

info "Installing dependencies..."
apt-get install -y -qq git python3-pip ca-certificates curl
python3 -m pip install --quiet mitmproxy requests

info "Copying files to $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp newgreedy.py newgreedy_addon.py config.ini requirements.txt "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/newgreedy.py"

info "Generating mitmproxy CA..."
if [[ ! -f "$CA_SRC" ]]; then
    mitmdump --quiet & MITM_PID=$!
    sleep 3
    kill "$MITM_PID" 2>/dev/null || true
    wait "$MITM_PID" 2>/dev/null || true
fi
[[ ! -f "$CA_SRC" ]] && error "CA not generated. Run mitmdump once manually."

info "Installing CA into system trust store..."
cp "$CA_SRC" "$CA_DST"
update-ca-certificates
info "CA installed."

info "Creating systemd service..."
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=NewGreedy BitTorrent announce proxy v1.4
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $INSTALL_DIR/newgreedy.py
Restart=on-failure
RestartSec=5
StandardOutput=append:$INSTALL_DIR/newgreedy.log
StandardError=append:$INSTALL_DIR/newgreedy.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable newgreedy.service
systemctl restart newgreedy.service

info "NewGreedy v1.4 installed and running."
info "Logs: tail -f $INSTALL_DIR/newgreedy.log"
