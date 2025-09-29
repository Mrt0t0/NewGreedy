#!/bin/bash

# ==============================================================================
# Installation Script for NewGreedy Proxy (v1.0)
# ==============================================================================
# This script automates the installation of the NewGreedy proxy as a
# systemd service on modern Linux systems (like Ubuntu, Debian, etc.).
#
# It performs the following actions:
#   1. Sets up a dedicated installation directory in the user's home folder.
#   2. Copies the main Python script and its configuration file.
#   3. Creates and configures a systemd service to run the script at boot.
#   4. Enables and starts the service, ensuring it runs automatically.
# ==============================================================================

# --- Script Configuration ---
# Installation directory in the user's home folder.
INSTALL_DIR="$HOME/NewGreedy"
# The main Python script file.
SCRIPT_NAME="NewGreedy.py"
# The configuration file.
CONFIG_NAME="config.ini"
# The name of the systemd service.
SERVICE_NAME="newgreedy.service"
# Automatically find the python3 executable path.
PYTHON_PATH=$(which python3)

# --- Pre-flight Checks ---
# Check if the script is run as root, which is needed for systemd setup.
if [ "$EUID" -ne 0 ]; then
  echo "Please run this script with sudo: sudo ./install.sh"
  exit 1
fi

# Check if required files are present in the current directory.
if [ ! -f "$SCRIPT_NAME" ] || [ ! -f "$CONFIG_NAME" ]; then
  echo "Error: Make sure '$SCRIPT_NAME' and '$CONFIG_NAME' are in the same directory as this script."
  exit 1
fi

# Check if python3 is installed.
if [ -z "$PYTHON_PATH" ]; then
    echo "Error: python3 is not installed or not in the system's PATH. Please install it."
    exit 1
fi

echo "--- Starting NewGreedy Installation ---"

# --- Step 1: Create Installation Directory ---
echo "Creating installation directory at $INSTALL_DIR..."
# The script must be run with sudo, but the home directory belongs to the user who ran sudo.
# We use $SUDO_USER to get the original user's home directory.
REAL_HOME=$(eval echo ~$SUDO_USER)
INSTALL_DIR="$REAL_HOME/NewGreedy"
mkdir -p "$INSTALL_DIR"
echo "Directory created."

# --- Step 2: Copy Application Files ---
echo "Copying script and configuration files..."
cp "$SCRIPT_NAME" "$INSTALL_DIR/"
cp "$CONFIG_NAME" "$INSTALL_DIR/"
# Set the correct ownership for the files.
chown -R $SUDO_USER:$SUDO_USER "$INSTALL_DIR"
echo "Files copied."

# --- Step 3: Create systemd Service File ---
SERVICE_FILE_PATH="/etc/systemd/system/$SERVICE_NAME"
echo "Creating systemd service file at $SERVICE_FILE_PATH..."

# Using a heredoc to write the service file content.
# This avoids permission issues when writing to /etc/systemd/system.
cat > "$SERVICE_FILE_PATH" << EOL
[Unit]
Description=NewGreedy Torrent Proxy Service
# Ensures the service starts after the network is ready.
After=network.target

[Service]
Type=simple
# Run the service as the user who invoked sudo, not as root.
User=$SUDO_USER
# Set the working directory to where the script is located.
WorkingDirectory=$INSTALL_DIR
# The command to execute. Uses the detected python3 path.
ExecStart=$PYTHON_PATH $INSTALL_DIR/$SCRIPT_NAME
# Automatically restart the service if it fails.
Restart=on-failure
RestartSec=10

[Install]
# This target ensures the service is started on multi-user logins.
WantedBy=multi-user.target
EOL

echo "Service file created."

# --- Step 4: Enable and Start the Service ---
echo "Reloading systemd, enabling and starting the service..."
# Reload the systemd manager configuration.
systemctl daemon-reload
# Enable the service to start on boot.
systemctl enable "$SERVICE_NAME"
# Start the service immediately.
systemctl start "$SERVICE_NAME"

# --- Final Step: Display Status ---
echo ""
echo "--- Installation Complete! ---"
echo "The NewGreedy proxy is now running as a systemd service."
echo "You can check its status at any time with:"
echo "sudo systemctl status $SERVICE_NAME"
echo ""
echo "To view live logs, use:"
echo "journalctl -u $SERVICE_NAME -f"
echo ""

# Display the initial status of the service.
systemctl status "$SERVICE_NAME" --no-pager
