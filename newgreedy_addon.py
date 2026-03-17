# NewGreedy v1.0 — mitmproxy addon
# MrT0t0 - https://github.com/Mrt0t0/NewGreedy/
#
# Full HTTPS announce interception via mitmproxy SSL inspection.
# Use this instead of newgreedy.py when your trackers are HTTPS-only.
#
# Usage:
#   mitmdump -p 3456 --ssl-insecure -s newgreedy_addon.py
#
# Requirements:
#   pip install mitmproxy
#   Install mitmproxy CA certificate in qBittorrent (see README — HTTPS Setup)

import re
import time
import random
import logging
import threading
import configparser
import socket
import urllib.parse
import requests
from mitmproxy import http
from pathlib import Path

CURRENT_VERSION = "1.0"

# ── Configuration ──────────────────────────────────────────────────────────────
config = configparser.ConfigParser()
config.read('config.ini')

MAX_MULTIPLIER     = float(config['DEFAULT'].get('max_upload_multiplier', 1.6))
SEEDING_MULTIPLIER = float(config['DEFAULT'].get('seeding_multiplier', 1.2))
RANDOM_FACTOR      = float(config['DEFAULT'].get('randomization_factor', 0.25))
MAX_SPEED_MBPS     = float(config['DEFAULT'].get('max_simulated_speed_mbps', 7.6))
MAX_SPEED_BPS      = MAX_SPEED_MBPS * 1024 * 1024 / 8
GLOBAL_RATIO_LIMIT = float(config['DEFAULT'].get('global_ratio_limit', 1.8))
COOLDOWN_MINUTES   = int(config['DEFAULT'].get('cooldown_duration_minutes', 10))
LOG_FILE           = config['LOGGING'].get('log_file', 'newgreedy.log')

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] - %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)

# ── Cooldown state ─────────────────────────────────────────────────────────────
is_in_cooldown    = False
cooldown_end_time = 0
_cooldown_lock    = threading.Lock()

# ── Network helpers ────────────────────────────────────────────────────────────

def resolve_host(ip: str) -> str:
    """Attempt a reverse DNS lookup. Returns IP on failure."""
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return ip

# ── GitHub update check ────────────────────────────────────────────────────────

def check_for_updates():
    """Query the GitHub Releases API and log a notice if a newer version exists."""
    api_url = "https://api.github.com/repos/Mrt0t0/NewGreedy/releases/latest"
    try:
        response = requests.get(api_url, timeout=10)
        if response.status_code == 404:
            logging.info("Update check: no releases found on GitHub.")
            return
        response.raise_for_status()
        latest = response.json().get("tag_name", "").lstrip("v")
        if latest and float(latest) > float(CURRENT_VERSION):
            logging.info(f"New version available: v{latest} — https://github.com/Mrt0t0/NewGreedy/releases")
        else:
            logging.info("NewGreedy is up to date.")
    except Exception as e:
        logging.warning(f"Could not check for updates: {e}")

# ── Stats manager ──────────────────────────────────────────────────────────────

class StatsManager:
    """
    Thread-safe in-memory store for per-torrent transfer statistics.
    Used to compute the global ratio and enforce the upload speed cap.
    """
    def __init__(self):
        self._lock     = threading.Lock()
        self._torrents = {}

    def update(self, info_hash: str, downloaded: int, uploaded_real: int, uploaded_reported: int):
        """Insert or overwrite the stats entry for a given info_hash."""
        with self._lock:
            self._torrents[info_hash] = {
                'downloaded':        downloaded,
                'uploaded_real':     uploaded_real,
                'uploaded_reported':  uploaded_reported,
                'last_update':       time.monotonic(),
            }

    def get(self, info_hash: str) -> dict:
        """Return a snapshot of the stats entry for info_hash, or {} if unknown."""
        with self._lock:
            return dict(self._torrents.get(info_hash, {}))

    def global_ratio(self) -> float:
        """
        Compute the overall upload/download ratio across all tracked torrents.
        Returns 0.0 when no data has been downloaded yet (avoids division by zero).
        """
        with self._lock:
            total_dl = sum(t['downloaded']        for t in self._torrents.values())
            total_ul = sum(t['uploaded_reported'] for t in self._torrents.values())
        return total_ul / total_dl if total_dl > 0 else 0.0

stats_manager = StatsManager()

# ── mitmproxy Addon ────────────────────────────────────────────────────────────

class NewGreedyAddon:
    """
    mitmproxy addon intercepting BitTorrent tracker announce requests
    over both HTTP and HTTPS via SSL inspection.

    The 'uploaded' query parameter is rewritten according to configured
    multipliers before the request reaches the tracker.
    All non-announce requests are forwarded transparently.
    """

    _RE_UPLOADED   = re.compile(r'uploaded=(\d+)')
    _RE_DOWNLOADED = re.compile(r'downloaded=(\d+)')
    _RE_INFO_HASH  = re.compile(r'info_hash=([^&]+)')
    _RE_LEFT       = re.compile(r'left=(\d+)')
    _RE_PEER_IP    = re.compile(r'ip=([\d\.]+)')

    def __init__(self):
        logging.info(f"--- Starting NewGreedy v{CURRENT_VERSION} (mitmproxy mode) --- MrT0t0 ---")
        logging.info(f"Max multiplier: {MAX_MULTIPLIER}x | Seeding: {SEEDING_MULTIPLIER}x | "
                     f"Random factor: ±{int(RANDOM_FACTOR * 100)}%")
        logging.info(f"Max simulated speed: {MAX_SPEED_MBPS} Mbps | "
                     f"Global ratio limit: {GLOBAL_RATIO_LIMIT}")
        threading.Thread(target=check_for_updates, daemon=True).start()

    def request(self, flow: http.HTTPFlow) -> None:
        """
        Called by mitmproxy for every intercepted request (HTTP and HTTPS).
        Identifies tracker announces by required query parameters and rewrites uploaded.
        """
        global is_in_cooldown, cooldown_end_time

        url        = flow.request.pretty_url
        ul_match   = self._RE_UPLOADED.search(url)
        dl_match   = self._RE_DOWNLOADED.search(url)
        hash_match = self._RE_INFO_HASH.search(url)

        # Not a tracker announce — pass through unmodified
        if not (ul_match and dl_match and hash_match):
            return

        try:
            info_hash  = urllib.parse.unquote(hash_match.group(1))
            real_dl    = int(dl_match.group(1))
            real_ul    = int(ul_match.group(1))

            left_match = self._RE_LEFT.search(url)
            left       = int(left_match.group(1)) if left_match else 1

            ip_match = self._RE_PEER_IP.search(url)
            label    = resolve_host(ip_match.group(1)) if ip_match else info_hash[:10]

            now = time.monotonic()

            # ── Determine multiplier and mode ──────────────────────────────────
            with _cooldown_lock:
                if is_in_cooldown and now > cooldown_end_time:
                    is_in_cooldown = False
                    logging.info("Cooldown period ended. Resuming normal multiplier.")

                if is_in_cooldown:
                    multiplier, mode = 1.0, "COOLDOWN"
                elif left == 0:
                    multiplier, mode = SEEDING_MULTIPLIER, "SEEDING"
                else:
                    multiplier, mode = MAX_MULTIPLIER, "DOWNLOADING"

                multiplier *= 1 + random.uniform(-RANDOM_FACTOR, RANDOM_FACTOR)

                if not is_in_cooldown and stats_manager.global_ratio() > GLOBAL_RATIO_LIMIT:
                    is_in_cooldown    = True
                    cooldown_end_time = now + COOLDOWN_MINUTES * 60
                    multiplier, mode  = 1.0, "COOLDOWN"
                    logging.warning(
                        f"Global ratio limit reached ({GLOBAL_RATIO_LIMIT}). "
                        f"Entering cooldown for {COOLDOWN_MINUTES} minutes."
                    )

            # ── Compute and cap reported upload ────────────────────────────────
            reported_ul = int(real_dl * multiplier)

            prev = stats_manager.get(info_hash)
            if prev:
                delta = now - prev.get('last_update', now)
                if delta > 0:
                    cap         = prev.get('uploaded_reported', 0) + int(MAX_SPEED_BPS * delta)
                    reported_ul = min(reported_ul, cap)

            stats_manager.update(info_hash, real_dl, real_ul, reported_ul)

            logging.info(
                f"[{mode}] {label} | "
                f"DL: {real_dl / 1_048_576:.2f} MB | "
                f"Real UL: {real_ul / 1_048_576:.2f} MB | "
                f"Reported UL: {reported_ul / 1_048_576:.2f} MB | "
                f"Protocol: {flow.request.scheme.upper()}"
            )

            # ── Rewrite uploaded in the request query string ───────────────────
            # mitmproxy exposes query as a mutable MultiDict — direct assignment
            flow.request.query['uploaded'] = str(reported_ul)

        except Exception as e:
            logging.error(f"Addon handler error: {e}", exc_info=True)


# mitmproxy discovers the addon via this module-level variable
addons = [NewGreedyAddon()]
