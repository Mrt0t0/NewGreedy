#!/bin/bash
# Installation for NewGreedy v0.9

INSTALL_DIR="$HOME/NewGreedy"
SCRIPT_NAME="NewGreedy.py"
CONFIG_NAME="config.ini"
SERVICE_NAME="newgreedy.service"
PYTHON_PATH=$(which python3)

if [[ $EUID -ne 0 ]]; then
   echo "Please run as root or with sudo"
   exit 1
fi

mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_NAME" "$INSTALL_DIR/"
cp "$CONFIG_NAME" "$INSTALL_DIR/"
chown -R $SUDO_USER:$SUDO_USER "$INSTALL_DIR"

$PYTHON_PATH -m pip install --upgrade requests

cat > /etc/systemd/system/$SERVICE_NAME << EOF
[Unit]
Description=NewGreedy Torrent Proxy Service
After=network.target

[Service]
Type=simple
User=$SUDO_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$PYTHON_PATH $INSTALL_DIR/$SCRIPT_NAME
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl restart $SERVICE_NAME

echo "NewGreedy v0.9 installed and started as a service."
echo "Check status with: sudo systemctl status $SERVICE_NAME"
echo "View logs with: journalctl -u $SERVICE_NAME -f"
