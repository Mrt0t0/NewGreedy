"""
# NewGreedy v0.6 - Progressive Upload Multiplier Proxy

### Description

NewGreedy v0.6 is an advanced HTTP proxy for BitTorrent clients. It intercepts tracker "announce" requests and intelligently modifies the `uploaded` statistic. This version uses a **progressive multiplier**, which starts at 1.0 and linearly increases to a configurable maximum over a set duration, making the reported upload values appear more natural.

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

# --- Logging Setup ---
# Log to console instead of a file by removing the 'filename' parameter.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def get_progressive_multiplier(max_multiplier, ramp_up_duration):
    """
    Calculates a multiplier that increases linearly from 1.0 to max_multiplier.
    """
    elapsed_time = time.time() - SCRIPT_START_TIME
    if elapsed_time >= ramp_up_duration:
        return max_multiplier
    else:
        return 1.0 + (max_multiplier - 1.0) * (elapsed_time / ramp_up_duration)

class NewGreedyProxyHandler(http.server.BaseHTTPRequestHandler):
    """
    HTTP proxy handler that uses a progressive multiplier and logs to console.
    """
    def do_GET(self):
        try:
            full_url = self.path
            # Initial log with the raw URL
            logging.info(f"Intercepted GET request for: {full_url[:120]}...") # Truncate for readability

            parsed_url = urllib.parse.urlsplit(full_url)
            query_string = parsed_url.query

            downloaded_match = re.search(r'downloaded=(\d+)', query_string)
            uploaded_match = re.search(r'uploaded=(\d+)', query_string)

            new_query_string = query_string

            if downloaded_match and uploaded_match:
                real_downloaded_bytes = int(downloaded_match.group(1))

                multiplier = get_progressive_multiplier(MAX_UPLOAD_MULTIPLIER, RAMP_UP_SECONDS)
                new_uploaded_bytes = int(real_downloaded_bytes * multiplier)

                original_uploaded_str = uploaded_match.group(0)
                new_uploaded_str = f'uploaded={new_uploaded_bytes}'

                new_query_string = query_string.replace(original_uploaded_str, new_uploaded_str)

                # Log values in Megabytes (MB) for better readability
                logging.info(
                    f"Multiplier: {multiplier:.3f} | "
                    f"Downloaded: {real_downloaded_bytes / (1024*1024):.2f} MB | "
                    f"Reported Upload: {new_uploaded_bytes / (1024*1024):.2f} MB"
                )
            else:
                logging.warning("Missing 'downloaded' or 'uploaded' params. Forwarding original query.")

            target_path_and_query = parsed_url.path
            if new_query_string:
                target_path_and_query += '?' + new_query_string

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
                self.wfile.write(chunk)

        except requests.exceptions.RequestException as e:
            logging.error(f"Network error connecting to tracker: {e}")
            self.send_error(504, "Gateway Timeout")
        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}")
            self.send_error(500, "Internal Server Error")

    def log_message(self, format, *args):
        # Suppress default console logging to avoid duplicate messages
        return

def run_proxy_server():
    """Sets up and runs the threaded HTTP proxy server."""
    with socketserver.ThreadingTCPServer(("", LISTEN_PORT), NewGreedyProxyHandler) as httpd:
        print("--- NewGreedy v0.6 (Progressive Multiplier & Console Log) ---")
        print(f"Listening on port: {LISTEN_PORT}")
        print(f"Max Upload Multiplier: x{MAX_UPLOAD_MULTIPLIER}")
        print(f"Ramp-up Time: {RAMP_UP_SECONDS} seconds")
        print("Proxy is running. Logs will be displayed below.")
        logging.info("Server startup complete.")
        httpd.serve_forever()

if __name__ == '__main__':
    run_proxy_server()
