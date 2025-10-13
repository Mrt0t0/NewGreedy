#!/bin/bash
# Simple installation script for NewGreedy v0.9 in /opt/newgreedy

INSTALL_DIR="/opt/newgreedy"
SCRIPT_NAME="NewGreedy.py"
CONFIG_NAME="config.ini"
SERVICE_NAME="newgreedy.service"
PYTHON_PATH=$(which python3)

if [[ $EUID -ne 0 ]]; then
   echo "Please run this script as root or with sudo"
   exit 1
fi

echo "Installing NewGreedy v0.9 to $INSTALL_DIR..."

mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_NAME" "$INSTALL_DIR/"
cp "$CONFIG_NAME" "$INSTALL_DIR/"
chown -R root:root "$INSTALL_DIR"

echo "Installing dependencies..."
$PYTHON_PATH -m pip install --upgrade requests

echo "Creating systemd service..."

cat > /etc/systemd/system/$SERVICE_NAME << EOF
[Unit]
Description=NewGreedy Torrent Proxy Service
After=network.target

[Service]
Type=simple
User=root
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

echo "Installation complete."
echo "Check service status with: sudo systemctl status $SERVICE_NAME"
echo "Follow logs with: journalctl -u $SERVICE_NAME -f"
