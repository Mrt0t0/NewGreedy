#!/bin/bash
#
# Installation Script for NewGreedy v0.8
# This script sets up the Python proxy as a systemd service.
#

# --- Variables ---
SCRIPT_NAME="NewGreedy.py"
CONFIG_NAME="config.ini"
SERVICE_NAME="newgreedy.service"

# Find the python3 executable path
PYTHON_PATH=$(which python3)

# Determine the user who is running the script with sudo
if [ -n "$SUDO_USER" ]; then
    USER_NAME=$SUDO_USER
else
    USER_NAME=$(whoami)
fi
USER_HOME=$(eval echo ~$USER_NAME)
INSTALL_DIR="$USER_HOME/NewGreedy"

# --- Pre-flight Checks ---

# 1. Check for root privileges
if [ "$EUID" -ne 0 ]; then
  echo "Please run this script with sudo."
  exit 1
fi

# 2. Check if required files exist
if [ ! -f "$SCRIPT_NAME" ] || [ ! -f "$CONFIG_NAME" ]; then
  echo "Error: Make sure '$SCRIPT_NAME' and '$CONFIG_NAME' are in the same directory as this script."
  exit 1
fi

# 3. Check if python3 is installed
if [ -z "$PYTHON_PATH" ]; then
  echo "Error: python3 is not found. Please install it."
  exit 1
fi

# --- Installation Process ---

echo "--- Installing NewGreedy v0.8 ---"

# 1. Install Python dependencies
echo "Installing Python dependencies (requests)..."
$PYTHON_PATH -m pip install --upgrade requests

# 2. Create installation directory and copy files
echo "Creating installation directory at $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_NAME" "$INSTALL_DIR/"
cp "$CONFIG_NAME" "$INSTALL_DIR/"

# 3. Set correct ownership to prevent permission errors
echo "Setting permissions for $USER_NAME..."
chown -R $USER_NAME:$USER_NAME "$INSTALL_DIR"

# 4. Create the systemd service file
echo "Creating systemd service file..."
cat > /etc/systemd/system/$SERVICE_NAME << EOL
[Unit]
Description=NewGreedy Torrent Proxy Service
After=network.target

[Service]
# Run the service as the user, not as root
Type=simple
User=$USER_NAME

# Set the working directory so the script can find config.ini
WorkingDirectory=$INSTALL_DIR

# Use full paths for robustness
ExecStart=$PYTHON_PATH $INSTALL_DIR/$SCRIPT_NAME

# Automatically restart the service if it fails
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOL

# 5. Reload systemd, enable and start the service
echo "Reloading systemd and starting the service..."
systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl start $SERVICE
