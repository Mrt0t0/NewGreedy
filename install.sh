#!/usr/bin/env bash
set -e
UPDATE=false
for arg in "$@"; do [ "$arg" = "--update" ] && UPDATE=true; done
DEST=/opt/newgreedy
SERVICE=/etc/systemd/system/newgreedy.service
echo "[NewGreedy] Installing v1.5.1..."
python3 -m pip install -q -r requirements.txt
if [ ! -d "$DEST" ] || $UPDATE; then
    mkdir -p $DEST
    cp -r . $DEST/
    echo "[NewGreedy] Files copied to $DEST"
fi
if ! $UPDATE; then
    cat > $SERVICE <<EOF
[Unit]
Description=NewGreedy v1.5.1
After=network.target

[Service]
WorkingDirectory=$DEST
ExecStart=/usr/bin/python3 $DEST/newgreedy.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now newgreedy
fi
if $UPDATE; then
    systemctl restart newgreedy || true
fi
echo "[NewGreedy] Done."
