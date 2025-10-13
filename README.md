# NewGreedy v0.9 - Advanced CLI HTTP proxy for BitTorrent clients
Mrt0t0 / [https://github.com/Mrt0t0/NewGreedy](https://github.com/Mrt0t0/NewGreedy)

### Description

NewGreedy is an HTTP proxy for BitTorrent clients (GreedyTorrent-like). It intercepts tracker "announce" requests and modifies the `uploaded` statistic intelligently.

Version 0.9 integrates advanced behavior to simulate realistic usage and avoid tracker detection by:

- Detecting completed torrents and applying a smaller upload multiplier during seeding.
- Enforcing a cooldown period after reaching a global upload/download ratio limit to simulate user slowdown.
- Logging human-readable torrent hostnames or peer IP addresses for easier monitoring.
- Performing automatic updates checks against GitHub releases.

### Features

- **Intelligent Seeding**: Detects torrent completion (`left=0`) and switches to a customizable seeding multiplier.
- **Cooldown Mode**: Temporarily disables upload boosts after hitting ratio limits.
- **Random Multiplier**: Adds realistic variance to upload speeds.
- **Speed Cap**: Restricts maximum reported upload speed.
- **Global Ratio Limiting**: Prevents suspiciously high ratios.
- **Maximum Tracker Compatibility**: Uses minimal URL rewriting.
- **Dual Logging**: Logs activity concurrently to console and file.
- **Multi-threaded**: Handles multiple simultaneous client requests effectively.
- **Auto Update Checking**: Keeps user informed about new releases.

### Dependencies

-   Python 3.x
-   `requests` library (`pip install requests`)

### Configuration (`config.ini`)

    listen_port: The local port the proxy listens on.

    max_upload_multiplier: The target multiplier to be applied to your download amount.

    seeding_multiplier: The multiplier to apply when the torrent is completed (seeding mode).

    randomization_factor: The percentage of random variation (e.g., 0.1 for +/-10%).

    max_simulated_speed_mbps: The maximum upload speed (in Megabits-per-second) that the script will simulate.

    global_ratio_limit: The global upload/download ratio at which the script will stop multiplying and enter cooldown.

    cooldown_duration_minutes: Length of the cooldown period in minutes.

    log_file: The path for the persistent log file.

### Installation & Usage

1.  **Clone the repository:**
    ```
    git clone https://github.com/Mrt0t0/NewGreedy.git
    cd NewGreedy
    ```

2.  **Customize `config.ini`** to set your preferences.

3.  **Run the installation script** to set up the systemd service:
    ```
    chmod +x install.sh
    sudo ./install.sh
    ```

4.  **Monitor the service:**
    -   **Live Console & File Logs:** Logs are now visible in `journalctl` and saved to the path specified by `log_file`.
    -   Check the status: `sudo systemctl status newgreedy.service`
    -   View live logs: `journalctl -u newgreedy.service -f`
    -   Check log file
  
5. **Update from Github**
   To update your local NewGreedy installation with the latest changes from GitHub, follow these steps:

   -    Open a terminal.
   -    Navigate to your NewGreedy installation directory: `cd ~/NewGreedy`
   -    Pull the latest changes from the GitHub repository: `git pull origin main`
   -    (Optional) Update Python dependencies, in case requirements changed: `python3 -m pip install --upgrade requests`
   -    Restart the NewGreedy service to apply the update: `sudo systemctl restart newgreedy.service`
   -    Check the status and logs to verify proper operation: `sudo systemctl status newgreedy.service` and `journalctl -u newgreedy.service -f`

