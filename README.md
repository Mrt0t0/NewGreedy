# NewGreedy v0.6 - Progressive Upload Multiplier Proxy

### Description

NewGreedy v0.6 is an advanced HTTP proxy for BitTorrent clients. (GreedyTorrent Like) It intercepts tracker "announce" requests and intelligently modifies the `uploaded` statistic. This version uses a **progressive multiplier**, which starts at 1.0 and linearly increases to a configurable maximum over a set duration, making the reported upload values appear more natural.

The reported upload is calculated as: `Reported Upload = Real Downloaded * Progressive Multiplier`.

Logs are displayed directly in the console for real-time monitoring, with data values shown in Megabytes (MB).

### Features

-   **Progressive Multiplier**: The upload multiplier ramps up over time, simulating a more realistic upload behavior.
-   **Download-Based Calculation**: Bases the fake upload on the amount of data downloaded, ensuring a steady increase in reported ratio.
-   **Safe Parameter Handling**: Uses regular expressions to modify *only* the `uploaded` value, preventing corruption of other critical parameters.
-   **Real-time Console Logging**: All activity is logged directly to the console for immediate feedback.
-   **Human-Readable Values**: Reports downloaded and uploaded amounts in Megabytes (MB) in the logs.
-   **Multi-Threaded**: Handles multiple simultaneous client connections without blocking.

### How It Works

1.  The proxy listens for tracker requests from your torrent client.
2.  It calculates the current multiplier based on how long the script has been running.
3.  It reads the `downloaded` value and computes the new `uploaded` value using the progressive multiplier.
4.  It safely replaces the original `uploaded` value in the URL and forwards the modified request to the tracker.
5.  All steps are logged to the console in real-time.

### Dependencies

-   Python 3.x
-   `requests` library (`pip install requests`)

### Configuration (`config.ini`)

-   `listen_port`: The local port the proxy listens on.
-   `max_upload_multiplier`: The target multiplier to be reached over time (e.g., `5.0` for 5x).
-   `ramp_up_seconds`: The duration (in seconds) for the multiplier to increase from 1.0 to its maximum value (e.g., `3600` for 1 hour).

### Usage

1.  Customize `config.ini` with your desired settings.
2.  Run the script: `python NewGreedy.py`.
3.  Configure your torrent client's HTTP proxy settings to `localhost` and the `listen_port`.
4.  Monitor the console output to see the multiplier and data modification in action.
"""
