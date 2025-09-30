# NewGreedy v0.8 - Advanced CLI HTTP proxy for BitTorrent clients
Mrt0t0 / [https://github.com/Mrt0t0/NewGreedy](https://github.com/Mrt0t0/NewGreedy)

### Description

NewGreedy is a HTTP proxy for BitTorrent clients (GreedyTorrent-like).

It intercepts tracker "announce" requests and intelligently modifies the `uploaded` statistic. This version uses advanced, configurable logic to simulate realistic upload behavior and avoid detection.

The reported upload is calculated using a dynamic multiplier, with several safety features built-in.

### Features

-   **Automatic Update Checker**: Checks for new versions on GitHub on startup and notifies the user in the logs.
-   **Global Ratio Limiter**: Automatically disables the upload multiplier if the overall ratio exceeds a safe, user-defined limit.
-   **Randomized Multiplier**: Adds a random variation to the multiplier, making upload patterns appear more natural and less robotic.
-   **Simulated Upload Speed Cap**: Prevents unrealistic upload speed spikes by capping the reported upload rate to a configurable maximum.
-   **Maximum Tracker Compatibility**: Uses a direct string replacement method to preserve the original URL structure and prevent tracker errors.
-   **Dual Logging**: Logs all activity to both the console and a persistent file for easy monitoring.
-   **Multi-Threaded**: Handles multiple simultaneous client connections without blocking.

### Dependencies

-   Python 3.x
-   `requests` library (`pip install requests`)

### Configuration (`config.ini`)

-   `listen_port`: The local port the proxy listens on.
-   `max_upload_multiplier`: The target multiplier to be applied to your download amount.
-   `randomization_factor`: The percentage of random variation (e.g., `0.1` for +/-10%).
-   `max_simulated_speed_mbps`: The maximum upload speed (in Megabits-per-second) that the script will simulate.
-   `global_ratio_limit`: The global ratio at which the script will stop multiplying.
-   `log_file`: The path for the persistent log file.

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
