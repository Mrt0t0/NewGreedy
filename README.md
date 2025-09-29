# NewGreedy v0.7 - Progressive Upload Multiplier Proxy
# Mrt0t0

### Description

NewGreedy v0.7 is a HTTP proxy for BitTorrent clients. (GreedyTorrent Like).

It intercepts tracker "announce" requests and intelligently modifies the `uploaded` statistic. This version uses a **progressive multiplier** that starts at 1.0 and linearly increases to a configurable maximum over a set duration.

This version introduces **dual logging**: all activity is logged simultaneously to the console for real-time monitoring and to a file for persistent records.

The reported upload is calculated as: `Reported Upload = Real Downloaded * Progressive Multiplier`.

### Features

-   **Progressive Multiplier**: Simulates realistic upload behavior over time.
-   **Download-Based Calculation**: Ensures a steady increase in reported ratio.
-   **Dual Logging**: Logs activity to both the console and a file.
-   **Log Rotation**: Automatically deletes old log files to save space.
-   **Safe Parameter Handling**: Prevents corruption of critical tracker parameters.
-   **Multi-Threaded**: Handles multiple simultaneous client connections.

### Dependencies

-   Python 3.x
-   `requests` library (`pip install requests`)

### Configuration (`config.ini`)

-   `listen_port`: The local port the proxy listens on.
-   `max_upload_multiplier`: The target multiplier to be reached.
-   `ramp_up_seconds`: The duration for the multiplier to increase to its max.
-   `log_file`: The path for the persistent log file.
-   `log_retention_days`: How long to keep the log file before deleting it.

### Installation & Usage

1.  **Clone the repository:**
    ```
    git clone https://github.com/Mrt0t0/NewGreedy.git
    cd NewGreedy
    ```

2.  **Customize `config.ini`** to set your preferences.

3.  **Run the installation script:**
    ```
    chmod +x install.sh
    sudo ./install.sh
    ```

4.  **Monitor the service:**
    -   **Live Console & File Logs:** Logs are now visible in `journalctl` and saved to the path specified by `log_file`.
    -   `sudo systemctl status newgreedy.service`
    -   `journalctl -u newgreedy.service -f`
