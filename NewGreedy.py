"""
# NewGreedy v0.8

## MrT0t0 - https://github.com/Mrt0t0/NewGreedy/

NewGreedy is a HTTP proxy for BitTorrent clients (GreedyTorrent-like).

It intercepts tracker "announce" requests and intelligently modifies the `uploaded` statistic.
This version uses advanced, configurable logic to simulate realistic upload behavior and avoid detection.

The reported upload is calculated using a dynamic multiplier, with several safety features built-in.

### Features
-   **Automatic Update Checker**: Checks for new versions on GitHub on startup and notifies the user in the logs.
-   **Global Ratio Limiter**: Automatically disables the upload multiplier if the overall ratio exceeds a safe, user-defined limit.
-   **Randomized Multiplier**: Adds a random variation to the multiplier, making upload patterns appear more natural and less robotic.
-   **Simulated Upload Speed Cap**: Prevents unrealistic upload speed spikes by capping the reported upload rate to a configurable maximum.
-   **Maximum Tracker Compatibility**: Uses a direct string replacement method to preserve the original URL structure and prevent tracker errors.
-   **Dual Logging**: Logs all activity to both the console and a persistent file for easy monitoring.
-   **Multi-Threaded**: Handles multiple simultaneous client connections without blocking.
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
import random
import threading

# --- Global State & Configuration ---
CURRENT_VERSION = "0.8"
config = configparser.ConfigParser()
config.read('config.ini')

# Proxy settings
LISTEN_PORT = int(config['DEFAULT'].get('listen_port', 3456))
MAX_MULTIPLIER = float(config['DEFAULT'].get('max_upload_multiplier', 1.6))
RANDOM_FACTOR = float(config['DEFAULT'].get('randomization_factor', 0.1))
MAX_SPEED_MBPS = float(config['DEFAULT'].get('max_simulated_speed_mbps', 11.0))
MAX_SPEED_BPS = MAX_SPEED_MBPS * 1024 * 1024 / 8
GLOBAL_RATIO_LIMIT = float(config['DEFAULT'].get('global_ratio_limit', 1.8))

# Logging settings
LOG_FILE = config['LOGGING'].get('log_file', 'newgreedy.log')

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] - %(message)s',
                    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])

# --- Function Definitions ---
def check_for_updates():
    """Checks for new releases on the GitHub repository."""
    api_url = "https://api.github.com/repos/Mrt0t0/NewGreedy/releases/latest"
    try:
        response = requests.get(api_url, timeout=10)
        if response.status_code == 404:
            logging.info("Update check skipped: No releases found.")
            return
        response.raise_for_status()
        latest_version = response.json().get("tag_name", "").replace('v', '')
        if latest_version and float(latest_version) > float(CURRENT_VERSION):
            logging.info(f"A new version is available: v{latest_version}")
        else:
            logging.info("NewGreedy is up to date.")
    except Exception as e:
        logging.error(f"Could not check for updates: {e}")

class StatsManager:
    """A thread-safe class to manage torrent statistics."""
    def __init__(self):
        self.lock = threading.Lock()
        self.torrents = {}

    def update(self, info_hash, downloaded, uploaded_real, uploaded_reported):
        with self.lock:
            if info_hash not in self.torrents:
                self.torrents[info_hash] = {'downloaded': 0, 'uploaded_real': 0, 'last_update': time.time()}

            self.torrents[info_hash].update({
                'downloaded': downloaded,
                'uploaded_real': uploaded_real,
                'uploaded_reported': uploaded_reported,
                'last_update': time.time()
            })

    def get_stats(self):
        with self.lock: return dict(self.torrents)
    def get_total_downloaded(self):
        with self.lock: return sum(s['downloaded'] for s in self.torrents.values())
    def get_total_reported_upload(self):
        with self.lock: return sum(s['uploaded_reported'] for s in self.torrents.values())
    def get_global_ratio(self):
        dl = self.get_total_downloaded(); ul = self.get_total_reported_upload()
        return ul / dl if dl > 0 else 0

stats_manager = StatsManager()

# --- Core Proxy Logic with Correct Logging ---
class NewGreedyProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            original_url = self.path

            uploaded_match = re.search(r'uploaded=(\d+)', original_url)
            downloaded_match = re.search(r'downloaded=(\d+)', original_url)
            info_hash_match = re.search(r'info_hash=([^&]+)', original_url)

            if not (uploaded_match and downloaded_match and info_hash_match):
                self.forward_request(original_url); return

            info_hash = urllib.parse.unquote(info_hash_match.group(1))
            real_downloaded = int(downloaded_match.group(1))
            real_uploaded = int(uploaded_match.group(1))
            real_uploaded_str = uploaded_match.group(0)

            multiplier = MAX_MULTIPLIER * (1 + random.uniform(-RANDOM_FACTOR, RANDOM_FACTOR))
            if stats_manager.get_global_ratio() > GLOBAL_RATIO_LIMIT:
                multiplier = 1.0

            reported_upload = int(real_downloaded * multiplier)

            last_stats = stats_manager.get_stats().get(info_hash, {})
            time_delta = time.time() - last_stats.get('last_update', time.time())
            if time_delta > 0:
                max_upload_chunk = MAX_SPEED_BPS * time_delta
                capped_upload = last_stats.get('uploaded_reported', 0) + int(max_upload_chunk)
                if reported_upload > capped_upload:
                    reported_upload = capped_upload

            stats_manager.update(info_hash, real_downloaded, real_uploaded, reported_upload)

            logging.info(
                f"Torrent {info_hash[:10]}... | "
                f"DL: {real_downloaded / (1024*1024):.2f} MB | "
                f"Real UL: {real_uploaded / (1024*1024):.2f} MB | "
                f"Reported UL: {reported_upload / (1024*1024):.2f} MB"
            )

            new_url = original_url.replace(real_uploaded_str, f'uploaded={reported_upload}')
            self.forward_request(new_url)

        except Exception as e:
            logging.error(f"Proxy handler error: {e}"); self.send_error(500)

    def forward_request(self, url):
        headers = dict(self.headers)
        headers['Host'] = urllib.parse.urlsplit(url).netloc
        try:
            with requests.get(url, headers=headers, timeout=15) as response:
                self.send_response(response.status_code)
                for k, v in response.headers.items():
                    if k.lower() not in ['transfer-encoding', 'content-length', 'connection']:
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(response.content)
        except Exception as e:
            logging.error(f"Forwarding request failed: {e}")
            if not self.wfile.closed: self.send_error(502)

    def log_message(self, format, *args):
        return

# --- Main Execution for Service Stability ---
if __name__ == '__main__':
    logging.info(f"--- Starting NewGreedy v{CURRENT_VERSION} // Mrt0t0---")

    update_thread = threading.Thread(target=check_for_updates, daemon=True)
    update_thread.start()

    logging.info(f"Proxy listening on port {LISTEN_PORT}")
    logging.info(f"Max Multiplier: {MAX_MULTIPLIER}x, Random Factor: +/-{RANDOM_FACTOR*100}%")
    logging.info(f"Max Simulated Speed: {MAX_SPEED_MBPS} Mbps, Global Ratio Limit: {GLOBAL_RATIO_LIMIT}")

    server = socketserver.ThreadingTCPServer(("", LISTEN_PORT), NewGreedyProxyHandler)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    logging.info("NewGreedy Proxy server is running in the background.")

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logging.info("Shutting down...")
        server.shutdown()
        server.server_close()

    logging.info("--- NewGreedy Proxy Shut Down ---")
