"""
# NewGreedy v0.9

## MrT0t0 - https://github.com/Mrt0t0/NewGreedy/

## Description

NewGreedy is an HTTP proxy for BitTorrent clients (GreedyTorrent-like). 
It intercepts tracker "announce" requests and intelligently modifies the `uploaded` statistic.

Version 0.9 enhances stealth and realism by:

- Detecting when torrents are complete (left=0) and applying a smaller, configurable seeding multiplier.
- Automatically entering a cooldown period when the global upload/download ratio exceeds a set limit, reporting real upload values during cooldown.
- Logging human-readable torrent hostnames or peer IPs to improve monitoring clarity.
- Periodically checking GitHub for new updates with automatic user notification.

## Features

- **Intelligent Seeding Mode** simulates realistic seeding.
- **Cooldown Mode** reduces upload reporting after ratio limit is reached.
- **Dynamic, Randomized Multiplier** for natural behavior.
- **Upload Speed Capping** to avoid unrealistic spikes.
- **Global Ratio Limiter** to prevent detection risks.
- **Max Tracker Compatibility** by modifying URLs safely.
- **Dual Logging** to console and files.
- **Multi-threaded** for concurrent client handling.
- **Auto Update Checks** to keep the proxy current.
"""

import http.server
import socketserver
import urllib.parse
import configparser
import requests
import logging
import time
import re
import random
import threading
import socket

CURRENT_VERSION = "0.9"
config = configparser.ConfigParser()
config.read('config.ini')

LISTEN_PORT = int(config['DEFAULT'].get('listen_port', 3456))
MAX_MULTIPLIER = float(config['DEFAULT'].get('max_upload_multiplier', 1.6))
SEEDING_MULTIPLIER = float(config['DEFAULT'].get('seeding_multiplier', 1.2))
RANDOM_FACTOR = float(config['DEFAULT'].get('randomization_factor', 0.1))
MAX_SPEED_MBPS = float(config['DEFAULT'].get('max_simulated_speed_mbps', 7.6))
MAX_SPEED_BPS = MAX_SPEED_MBPS * 1024 * 1024 / 8
GLOBAL_RATIO_LIMIT = float(config['DEFAULT'].get('global_ratio_limit', 1.8))
COOLDOWN_MINUTES = int(config['DEFAULT'].get('cooldown_duration_minutes', 10))
LOG_FILE = config['LOGGING'].get('log_file', 'newgreedy.log')

is_in_cooldown = False
cooldown_end_time = 0

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s [%(levelname)s] - %(message)s',
                    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()])

def check_for_updates():
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

def resolve_host(ip):
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ip

class StatsManager:
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
        with self.lock:
            return dict(self.torrents)

    def get_total_downloaded(self):
        with self.lock:
            return sum(t['downloaded'] for t in self.torrents.values())

    def get_total_reported_upload(self):
        with self.lock:
            return sum(t['uploaded_reported'] for t in self.torrents.values())

    def get_global_ratio(self):
        dl = self.get_total_downloaded()
        ul = self.get_total_reported_upload()
        return ul / dl if dl > 0 else 0

stats_manager = StatsManager()

class NewGreedyProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global is_in_cooldown, cooldown_end_time
        try:
            original_url = self.path
            uploaded_match = re.search(r'uploaded=(\d+)', original_url)
            downloaded_match = re.search(r'downloaded=(\d+)', original_url)
            info_hash_match = re.search(r'info_hash=([^&]+)', original_url)
            left_match = re.search(r'left=(\d+)', original_url)
            peer_ip_match = re.search(r'ip=([\d\.]+)', original_url)

            if not (uploaded_match and downloaded_match and info_hash_match):
                self.forward_request(original_url)
                return

            info_hash = urllib.parse.unquote(info_hash_match.group(1))
            real_downloaded = int(downloaded_match.group(1))
            real_uploaded = int(uploaded_match.group(1))
            real_uploaded_str = uploaded_match.group(0)
            left = int(left_match.group(1)) if left_match else 1

            peer_ip = peer_ip_match.group(1) if peer_ip_match else None
            host_name = resolve_host(peer_ip) if peer_ip else info_hash[:10]

            current_time = time.time()
            if is_in_cooldown and current_time > cooldown_end_time:
                is_in_cooldown = False
                logging.info("Cooldown period finished. Resuming normal multiplier.")

            if is_in_cooldown:
                multiplier = 1.0
                mode = "COOLDOWN"
            elif left == 0:
                multiplier = SEEDING_MULTIPLIER
                mode = "SEEDING"
            else:
                multiplier = MAX_MULTIPLIER
                mode = "DOWNLOADING"

            multiplier *= (1 + random.uniform(-RANDOM_FACTOR, RANDOM_FACTOR))

            if not is_in_cooldown and stats_manager.get_global_ratio() > GLOBAL_RATIO_LIMIT:
                is_in_cooldown = True
                cooldown_end_time = current_time + (COOLDOWN_MINUTES * 60)
                multiplier = 1.0
                mode = "COOLDOWN"
                logging.warning(f"Global Ratio Limit reached! Entering cooldown for {COOLDOWN_MINUTES} minutes.")

            reported_upload = int(real_downloaded * multiplier)
            last_stats = stats_manager.get_stats().get(info_hash, {})
            time_delta = current_time - last_stats.get('last_update', current_time)
            if time_delta > 0:
                max_upload_chunk = MAX_SPEED_BPS * time_delta
                capped_upload = last_stats.get('uploaded_reported', 0) + int(max_upload_chunk)
                if reported_upload > capped_upload:
                    reported_upload = capped_upload

            stats_manager.update(info_hash, real_downloaded, real_uploaded, reported_upload)

            logging.info(
                f"[{mode}] Torrent {host_name} | "
                f"DL: {real_downloaded / (1024*1024):.2f} MB | "
                f"Real UL: {real_uploaded / (1024*1024):.2f} MB | "
                f"Reported UL: {reported_upload / (1024*1024):.2f} MB"
            )

            new_url = original_url.replace(real_uploaded_str, f'uploaded={reported_upload}')
            self.forward_request(new_url)
        except Exception as e:
            logging.error(f"Proxy handler error: {e}")
            self.send_error(500)

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
            if not self.wfile.closed:
                self.send_error(502)

    def log_message(self, format, *args):
        # Disable default HTTP server logs to avoid binary blob logs
        return


if __name__ == '__main__':
    logging.info(f"--- Starting NewGreedy v{CURRENT_VERSION} --- Mrt0T0 ---")
    update_thread = threading.Thread(target=check_for_updates, daemon=True)
    update_thread.start()
    logging.info(f"Proxy listening on port {LISTEN_PORT}")
    logging.info(f"Max Multiplier: {MAX_MULTIPLIER}x, Seeding Multiplier: {SEEDING_MULTIPLIER}x")
    logging.info(f"Max Simulated Speed: {MAX_SPEED_MBPS} Mbps, Global Ratio Limit: {GLOBAL_RATIO_LIMIT}")

    server = socketserver.ThreadingTCPServer(("", LISTEN_PORT), NewGreedyProxyHandler)
    server_thread = threading.Thread(target=server.serve_forever)
    server_thread.daemon = True
    server_thread.start()
    logging.info("Proxy server is running in the background.")

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logging.info("Shutting down...")
        server.shutdown()
        server.server_close()
