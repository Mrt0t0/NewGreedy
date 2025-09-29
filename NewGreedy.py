"""
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
"""

import http.server
import socketserver
import urllib.parse
import configparser
import requests
import logging
import os
import time
import re
from threading import Thread

# --- Global Start Time ---
SCRIPT_START_TIME = time.time()

# --- Configuration Loading ---
config = configparser.ConfigParser()
config.read('config.ini')

LISTEN_PORT = int(config['DEFAULT'].get('listen_port', 3456))
MAX_UPLOAD_MULTIPLIER = float(config['DEFAULT'].get('max_upload_multiplier', 5.0))
RAMP_UP_SECONDS = int(config['DEFAULT'].get('ramp_up_seconds', 3600))
LOG_FILE = config['LOGGING'].get('log_file', 'newgreedy.log')
LOG_RETENTION_DAYS = int(config['LOGGING'].get('log_retention_days', 7))

# --- Dual Logging Setup ---
# Configure logging to output to both a file and the console.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

def get_progressive_multiplier(max_multiplier, ramp_up_duration):
    """Calculates a multiplier that increases linearly over time."""
    elapsed_time = time.time() - SCRIPT_START_TIME
    if elapsed_time >= ramp_up_duration:
        return max_multiplier
    else:
        return 1.0 + (max_multiplier - 1.0) * (elapsed_time / ramp_up_duration)

def cleanup_old_logs(logfile_path, retention_days):
    """Deletes the log file if it's older than the retention period."""
    if os.path.exists(logfile_path):
        file_age_seconds = time.time() - os.path.getmtime(logfile_path)
        if file_age_seconds > (retention_days * 86400):
            try:
                os.remove(logfile_path)
                logging.info(f"Old log file '{logfile_path}' deleted.")
            except OSError as e:
                logging.error(f"Error deleting log file: {e}")

def log_cleanup_worker():
    """Background thread that runs log cleanup once a day."""
    while True:
        cleanup_old_logs(LOG_FILE, LOG_RETENTION_DAYS)
        time.sleep(86400)

class NewGreedyProxyHandler(http.server.BaseHTTPRequestHandler):
    """HTTP proxy handler with progressive multiplier and dual logging."""
    def do_GET(self):
        try:
            full_url = self.path
            logging.info(f"Intercepted GET: {full_url[:120]}...")

            parsed_url = urllib.parse.urlsplit(full_url)
            query_string = parsed_url.query
            new_query_string = query_string

            downloaded_match = re.search(r'downloaded=(\d+)', query_string)
            uploaded_match = re.search(r'uploaded=(\d+)', query_string)

            if downloaded_match and uploaded_match:
                real_downloaded_bytes = int(downloaded_match.group(1))
                multiplier = get_progressive_multiplier(MAX_UPLOAD_MULTIPLIER, RAMP_UP_SECONDS)
                new_uploaded_bytes = int(real_downloaded_bytes * multiplier)

                original_uploaded_str = uploaded_match.group(0)
                new_uploaded_str = f'uploaded={new_uploaded_bytes}'

                new_query_string = query_string.replace(original_uploaded_str, new_uploaded_str)

                logging.info(
                    f"Multiplier: {multiplier:.3f} | "
                    f"Downloaded: {real_downloaded_bytes / (1024*1024):.2f} MB | "
                    f"Reported Upload: {new_uploaded_bytes / (1024*1024):.2f} MB"
                )

            target_path_and_query = parsed_url.path + ('?' + new_query_string if new_query_string else '')

            forward_headers = dict(self.headers)
            forward_headers['Host'] = parsed_url.netloc

            with requests.Session() as session:
                session.headers.update(forward_headers)
                target_url = f"http://{parsed_url.netloc}{target_path_and_query}"

                response = session.get(target_url, stream=True, timeout=15)

            self.send_response(response.status_code)
            for key, value in response.headers.items():
                if key.lower() != 'transfer-encoding':
                    self.send_header(key, value)
            self.end_headers()

            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    self.wfile.write(chunk)

        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}")
            if not self.wfile.closed:
                self.send_error(500, "Internal Server Error")

    def log_message(self, format, *args):
        # Suppress default server logging to avoid duplicate messages.
        return

def run_proxy_server():
    """Sets up and runs the threaded HTTP proxy server."""
    # Start the log cleanup worker in a separate thread.
    cleanup_thread = Thread(target=log_cleanup_worker, daemon=True)
    cleanup_thread.start()

    with socketserver.ThreadingTCPServer(("", LISTEN_PORT), NewGreedyProxyHandler) as httpd:
        print("--- NewGreedy v0.7 (Dual Logging) ---")
        print(f"Listening on port: {LISTEN_PORT}")
        print(f"Max Upload Multiplier: x{MAX_UPLOAD_MULTIPLIER}")
        print(f"Logs are being written to console and to '{LOG_FILE}'")
        logging.info("Server startup complete.")
        httpd.serve_forever()

if __name__ == '__main__':
    run_proxy_server()
