# NewGreedy v0.6 - Progressive Upload Multiplier BitTorrent Client Proxy

### Description

NewGreedy v0.6 is an advanced HTTP proxy for BitTorrent clients. (Greedy Torrent Like). 

It intercepts tracker "announce" requests and intelligently modifies the `uploaded` statistic.
This version uses a **progressive multiplier**, which starts at 1.0 and linearly increases to a configurable maximum over a set duration, making the reported upload values appear more natural.

The reported upload is calculated as: `Reported Upload = Real Downloaded * Progressive Multiplier`.

Logs are displayed directly in the console for real-time monitoring, with data values shown in Megabytes (MB).

### Features

-   **Progressive Multiplier**: Simulates realistic upload behavior.
-   **Download-Based Calculation**: Ensures a steady increase in reported ratio.
-   **Safe Parameter Handling**: Prevents corruption of critical tracker parameters.
-   **Real-time Console Logging**: Provides immediate feedback on proxy activity.
-   **Multi-Threaded & Robust**: Handles multiple connections and restarts on failure via `systemd`.

### Dependencies

-   Python 3.x
-   `requests` library (`pip install requests`)

### Installation (Linux with systemd)

1.  **Clone the repository:**
    ```
    git clone https://github.com/Mrt0t0/NewGreedy.git
    cd NewGreedy
    ```

2.  **Customize the configuration (optional):**
    Edit `config.ini` to change the port, multiplier, or ramp-up time.

3.  **Run the installation script:**
    The script will copy the files, set up, and start the `systemd` service for you.
    ```
    chmod +x install.sh
    sudo ./install.sh
    ```

### Usage After Installation

The proxy will now run automatically in the background.

-   **Check the service status:**
    ```
    sudo systemctl status newgreedy.service
    ```

-   **View live logs:**
    ```
    journalctl -u newgreedy.service -f
    ```

-   **Configure your torrent client** (qBittorrent, Transmission, etc.) to use an HTTP proxy at `localhost` on the port specified in `config.ini` (default: `3456`).

### Configuration (`config.ini`)

-   `listen_port`: The local port the proxy listens on.
-   `max_upload_multiplier`: The target multiplier to be reached over time.
-   `ramp_up_seconds`: The duration (in seconds) for the multiplier to increase from 1.0 to its maximum.
