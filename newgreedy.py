# NewGreedy v1.0
# MrT0t0 - https://github.com/Mrt0t0/NewGreedy/

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
import ssl
import subprocess
from pathlib import Path

CURRENT_VERSION = "1.0"

# ── Configuration ──────────────────────────────────────────────────────────────
# Load settings from config.ini, falling back to safe defaults if keys are missing
config = configparser.ConfigParser()
config.read('config.ini')

LISTEN_PORT           = int(config['DEFAULT'].get('listen_port', 3456))
MAX_MULTIPLIER        = float(config['DEFAULT'].get('max_upload_multiplier', 1.6))
SEEDING_MULTIPLIER    = float(config['DEFAULT'].get('seeding_multiplier', 1.2))
RANDOM_FACTOR         = float(config['DEFAULT'].get('randomization_factor', 0.25))
MAX_SPEED_MBPS        = float(config['DEFAULT'].get('max_simulated_speed_mbps', 7.6))
MAX_SPEED_BPS         = MAX_SPEED_MBPS * 1024 * 1024 / 8  # Convert Mbps to bytes/sec
GLOBAL_RATIO_LIMIT    = float(config['DEFAULT'].get('global_ratio_limit', 1.8))
COOLDOWN_MINUTES      = int(config['DEFAULT'].get('cooldown_duration_minutes', 10))
LOG_FILE              = config['LOGGING'].get('log_file', 'newgreedy.log')
ENABLE_HTTPS          = config['DEFAULT'].getboolean('enable_https', False)
SSL_CERTFILE          = config['DEFAULT'].get('ssl_certfile', 'cert.pem')
SSL_KEYFILE           = config['DEFAULT'].get('ssl_keyfile', 'key.pem')
SSL_VERIFY_TRACKERS   = config['DEFAULT'].getboolean('ssl_verify_trackers', True)
SSL_AUTOGENERATE_CERT = config['DEFAULT'].getboolean('ssl_autogenerate_cert', True)

# ── Logging ────────────────────────────────────────────────────────────────────
# Dual output: log file + console stream
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] - %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)

# ── Cooldown state ─────────────────────────────────────────────────────────────
# Shared mutable state — protected by _cooldown_lock for thread safety
is_in_cooldown    = False
cooldown_end_time = 0
_cooldown_lock    = threading.Lock()

# ── SSL helpers ────────────────────────────────────────────────────────────────

def ensure_ssl_certificate(certfile: str, keyfile: str) -> bool:
    """
    Check whether the certificate and private key files exist.
    If missing and ssl_autogenerate_cert is enabled, generate a self-signed
    certificate via openssl (for development/local use only).
    Returns True if a usable certificate is available, False otherwise.
    """
    cert_path = Path(certfile)
    key_path  = Path(keyfile)

    if cert_path.exists() and key_path.exists():
        logging.info(f"SSL: Certificate found — {certfile} / {keyfile}")
        return True

    if not SSL_AUTOGENERATE_CERT:
        logging.error(f"SSL: Certificate not found ({certfile}) and auto-generation is disabled.")
        return False

    logging.warning("SSL: Certificate missing — generating a self-signed certificate (dev only)...")
    try:
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:4096",
            "-keyout", keyfile, "-out", certfile,
            "-days", "365", "-nodes",
            "-subj", "/CN=localhost"
        ], check=True, capture_output=True, text=True)
        logging.info(f"SSL: Self-signed certificate generated — {certfile} / {keyfile}")
        return True
    except FileNotFoundError:
        logging.error("SSL: openssl not found. Install openssl or provide a certificate manually.")
        return False
    except subprocess.CalledProcessError as e:
        logging.error(f"SSL: Certificate generation failed — {e.stderr}")
        return False


def build_ssl_context(certfile: str, keyfile: str) -> ssl.SSLContext:
    """Create a server-side SSLContext enforcing TLS 1.2 as the minimum version."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile=certfile, keyfile=keyfile)
    return ctx


# ── HTTPS-capable threaded server ─────────────────────────────────────────────

class ThreadingServer(socketserver.ThreadingTCPServer):
    """
    ThreadingTCPServer with optional SSL/TLS wrapping.
    allow_reuse_address avoids "Address already in use" errors on restart.
    """
    allow_reuse_address = True

    def __init__(self, server_address, handler, ssl_context=None):
        # Defer bind/activate to allow SSL wrapping before the socket is bound
        super().__init__(server_address, handler, bind_and_activate=False)
        if ssl_context:
            self.socket = ssl_context.wrap_socket(self.socket, server_side=True)
        self.server_bind()
        self.server_activate()


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


# ── Network helpers ────────────────────────────────────────────────────────────

def resolve_host(ip: str) -> str:
    """Attempt a reverse DNS lookup on the given IP. Returns the IP on failure."""
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return ip


# ── Stats manager ──────────────────────────────────────────────────────────────

class StatsManager:
    """
    Thread-safe in-memory store tracking per-torrent transfer statistics.
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


# ── Proxy request handler ──────────────────────────────────────────────────────

class NewGreedyProxyHandler(http.server.BaseHTTPRequestHandler):
    """
    Intercepts GET requests from the BitTorrent client.
    If the request is a tracker announce, it rewrites the 'uploaded' field
    according to the configured multiplier and forwards the modified request.
    Non-announce requests are forwarded transparently.
    """

    # Pre-compiled regex patterns — evaluated once at class definition, not per request
    _RE_UPLOADED   = re.compile(r'uploaded=(\d+)')
    _RE_DOWNLOADED = re.compile(r'downloaded=(\d+)')
    _RE_INFO_HASH  = re.compile(r'info_hash=([^&]+)')
    _RE_LEFT       = re.compile(r'left=(\d+)')
    _RE_PEER_IP    = re.compile(r'ip=([\d\.]+)')

    def do_GET(self):
        global is_in_cooldown, cooldown_end_time

        url       = self.path
        ul_match  = self._RE_UPLOADED.search(url)
        dl_match  = self._RE_DOWNLOADED.search(url)
        hash_match = self._RE_INFO_HASH.search(url)

        # Not a tracker announce — forward as-is
        if not (ul_match and dl_match and hash_match):
            self._forward(url)
            return

        try:
            info_hash     = urllib.parse.unquote(hash_match.group(1))
            real_dl       = int(dl_match.group(1))
            real_ul       = int(ul_match.group(1))
            real_ul_token = ul_match.group(0)         # Full "uploaded=N" token for replacement

            left_match = self._RE_LEFT.search(url)
            left       = int(left_match.group(1)) if left_match else 1  # Assume incomplete if missing

            ip_match = self._RE_PEER_IP.search(url)
            label    = resolve_host(ip_match.group(1)) if ip_match else info_hash[:10]

            now = time.monotonic()

            # ── Determine multiplier and mode (thread-safe) ────────────────────
            with _cooldown_lock:
                # Check if an active cooldown has expired
                if is_in_cooldown and now > cooldown_end_time:
                    is_in_cooldown = False
                    logging.info("Cooldown period ended. Resuming normal multiplier.")

                if is_in_cooldown:
                    multiplier, mode = 1.0, "COOLDOWN"
                elif left == 0:
                    # Torrent fully downloaded — apply the lighter seeding multiplier
                    multiplier, mode = SEEDING_MULTIPLIER, "SEEDING"
                else:
                    multiplier, mode = MAX_MULTIPLIER, "DOWNLOADING"

                # Apply randomized variance to break statistical patterns
                multiplier *= 1 + random.uniform(-RANDOM_FACTOR, RANDOM_FACTOR)

                # Trigger cooldown if the global ratio has exceeded the configured limit
                if not is_in_cooldown and stats_manager.global_ratio() > GLOBAL_RATIO_LIMIT:
                    is_in_cooldown    = True
                    cooldown_end_time = now + COOLDOWN_MINUTES * 60
                    multiplier, mode  = 1.0, "COOLDOWN"
                    logging.warning(
                        f"Global ratio limit reached ({GLOBAL_RATIO_LIMIT}). "
                        f"Entering cooldown for {COOLDOWN_MINUTES} minutes."
                    )

            # ── Compute reported upload ────────────────────────────────────────
            reported_ul = int(real_dl * multiplier)

            # Speed cap: clamp reported upload to what could realistically have
            # been transferred since the last announce at MAX_SPEED_BPS
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
                f"Reported UL: {reported_ul / 1_048_576:.2f} MB"
            )

            # Replace the original uploaded value and forward the modified request
            new_url = url.replace(real_ul_token, f'uploaded={reported_ul}')
            self._forward(new_url)

        except Exception as e:
            logging.error(f"Handler error: {e}", exc_info=True)
            self.send_error(500)

    def _forward(self, url: str):
        """
        Forward the (possibly modified) request to the remote tracker and
        relay its response back to the BitTorrent client.
        Handles SSL errors separately to provide actionable log guidance.
        """
        headers = dict(self.headers)
        parsed  = urllib.parse.urlsplit(url)
        if parsed.netloc:
            headers['Host'] = parsed.netloc  # Ensure Host header matches the target

        try:
            resp = requests.get(
                url, headers=headers, timeout=15,
                verify=SSL_VERIFY_TRACKERS, stream=False
            )
            self.send_response(resp.status_code)
            # Strip hop-by-hop headers that must not be forwarded
            skip = {'transfer-encoding', 'content-length', 'connection'}
            for k, v in resp.headers.items():
                if k.lower() not in skip:
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(resp.content)

        except requests.exceptions.SSLError as e:
            logging.error(
                f"SSL error with tracker: {e}. "
                f"If the tracker uses a self-signed cert, set ssl_verify_trackers = false."
            )
            if not self.wfile.closed:
                self.send_error(502)
        except Exception as e:
            logging.error(f"Failed to forward request: {e}")
            if not self.wfile.closed:
                self.send_error(502)

    def log_message(self, fmt, *args):
        # Suppress the default per-request HTTP log lines (binary tracker
        # responses produce unreadable output — handled by our own logger)
        pass


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    logging.info(f"--- Starting NewGreedy v{CURRENT_VERSION} --- MrT0t0 ---")

    # Run update check in background — non-blocking, daemon thread
    threading.Thread(target=check_for_updates, daemon=True).start()

    # ── SSL setup ──────────────────────────────────────────────────────────────
    ssl_ctx  = None
    protocol = "HTTP"
    if ENABLE_HTTPS:
        if ensure_ssl_certificate(SSL_CERTFILE, SSL_KEYFILE):
            ssl_ctx  = build_ssl_context(SSL_CERTFILE, SSL_KEYFILE)
            protocol = "HTTPS (TLS 1.2+)"
        else:
            logging.critical("Cannot start in HTTPS mode: certificate unavailable. Aborting.")
            raise SystemExit(1)

    logging.info(f"Proxy listening on port {LISTEN_PORT} [{protocol}]")
    logging.info(f"Max multiplier: {MAX_MULTIPLIER}x | Seeding: {SEEDING_MULTIPLIER}x | "
                 f"Random factor: ±{int(RANDOM_FACTOR * 100)}%")
    logging.info(f"Max simulated speed: {MAX_SPEED_MBPS} Mbps | "
                 f"Global ratio limit: {GLOBAL_RATIO_LIMIT}")

    server = ThreadingServer(("", LISTEN_PORT), NewGreedyProxyHandler, ssl_context=ssl_ctx)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    logging.info("Proxy running in background. Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logging.info("Shutting down...")
        server.shutdown()
        server.server_close()
