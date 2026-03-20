#!/usr/bin/env python3
"""
NewGreedy v1.1 — Standard HTTP/HTTPS proxy for BitTorrent clients
"""

import binascii
import configparser
import json
import logging
import os
import random
import re
import select
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

VERSION     = "1.1"
GITHUB_REPO = "Mrt0t0/NewGreedy"

QBIT_USER_AGENTS = [
    "qBittorrent/5.0.0",
    "qBittorrent/4.6.7",
    "qBittorrent/4.6.5",
    "qBittorrent/4.5.5",
    "qBittorrent/4.4.5",
    "Deluge/2.1.1",
    "Transmission/3.00",
    "uTorrent/3.6.0",
]

KNOWN_CLIENT_PREFIXES = ("qbittorrent", "deluge", "transmission", "utorrent", "libtorrent")

# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logging(log_file):
    logger = logging.getLogger("newgreedy")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger

logger = setup_logging("newgreedy.log")

# ── Helpers ───────────────────────────────────────────────────────────────────

def normalize_info_hash(raw):
    """Decode URL-encoded binary info_hash to readable hex string."""
    if not raw:
        return "unknown"
    try:
        decoded  = urllib.parse.unquote_to_bytes(raw)
        hex_hash = binascii.hexlify(decoded).decode("ascii")
        if len(hex_hash) == 40:
            return hex_hash
    except Exception:
        pass
    return "".join(c if c.isprintable() and c not in "\r\n" else "?" for c in raw)

def resolve_user_agent(mode, ua_value, original_ua):
    """
    Resolve User-Agent to send to tracker.
    mode: random | fixed | passthrough
    """
    if mode == "passthrough":
        return original_ua
    if mode == "fixed":
        return ua_value if ua_value else random.choice(QBIT_USER_AGENTS)
    # random (default)
    if original_ua and any(p in original_ua.lower() for p in KNOWN_CLIENT_PREFIXES):
        return original_ua
    return random.choice(QBIT_USER_AGENTS)

# ── Config ────────────────────────────────────────────────────────────────────

def load_config(path="config.ini"):
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(path, encoding="utf-8")
    return cfg

def validate_config(cfg):
    ok = True
    def gf(s, k, fb):
        try: return cfg.getfloat(s, k)
        except: return fb
    def gi(s, k, fb):
        try: return cfg.getint(s, k)
        except: return fb

    mul   = gf("multiplier",    "max_upload_multiplier",     1.6)
    seed  = gf("multiplier",    "seeding_multiplier",        1.2)
    rand  = gf("multiplier",    "randomization_factor",      0.25)
    rl    = gf("multiplier",    "global_ratio_limit",        1.8)
    slope = gf("multiplier",    "max_upload_slope",          2.0)
    pvar  = gf("anti_detection","peer_variance",             0.15)
    to    = gi("proxy",         "tracker_timeout",           5)
    port  = gi("proxy",         "listen_port",               3456)

    try:
        ua_mode = cfg.get("anti_detection", "user_agent_mode", fallback="random").strip().lower()
    except Exception:
        ua_mode = "random"

    logger.info("── Config validation ──────────────────────────────────")
    if mul > 5.0:
        logger.warning(f"  max_upload_multiplier={mul} > 5.0 — high detection risk")
    if seed > 3.0:
        logger.warning(f"  seeding_multiplier={seed} > 3.0 — high detection risk")
    if rand > 0.5:
        logger.warning(f"  randomization_factor={rand} > 0.5 — excessive variance")
    if rl < 0.5:
        logger.warning(f"  global_ratio_limit={rl} < 0.5 — cooldown triggers immediately")
    if slope > 5.0:
        logger.warning(f"  max_upload_slope={slope} > 5.0 — incoherent progression risk")
    if pvar > 0.5:
        logger.warning(f"  peer_variance={pvar} > 0.5 — suspicious peer variance")
    if port < 1024:
        logger.warning(f"  listen_port={port} < 1024 — requires root/admin privileges")
    if ua_mode not in ("random", "fixed", "passthrough"):
        logger.warning(f"  user_agent_mode=\"{ua_mode}\" unknown — falling back to random")
    if to < 1:
        logger.error(f"  tracker_timeout={to} < 1 — blocking value")
        ok = False

    ua_desc = {"random": "random (built-in list)", "fixed": "fixed", "passthrough": "passthrough"}.get(ua_mode, ua_mode)
    logger.info(f"  listen_port={port} | multiplier={mul} | seeding={seed} | ratio_limit={rl}")
    logger.info(f"  slope={slope} | peer_variance={pvar} | timeout={to}s | ua_mode={ua_desc}")
    logger.info("── Config OK ──────────────────────────────────────────")
    return ok

# ── Stats ─────────────────────────────────────────────────────────────────────

class StatsManager:
    def __init__(self, cfg):
        self._lock           = threading.Lock()
        self._global_ul      = 0.0
        self._global_dl      = 0.0
        self._torrents       = {}
        self._cooldown_until = 0.0

        def gf(s, k, fb):
            try: return cfg.getfloat(s, k)
            except: return fb
        def gi(s, k, fb):
            try: return cfg.getint(s, k)
            except: return fb
        def gb(s, k, fb):
            try: return cfg.getboolean(s, k)
            except: return fb
        def gs(s, k, fb):
            try: return cfg.get(s, k, fallback=fb).strip()
            except: return fb

        self._ratio_limit    = gf("multiplier",    "global_ratio_limit",        1.8)
        self._cooldown_dur   = gf("multiplier",    "cooldown_duration_minutes", 10.0) * 60
        self._mul            = gf("multiplier",    "max_upload_multiplier",     1.6)
        self._seed_mul       = gf("multiplier",    "seeding_multiplier",        1.2)
        self._rand           = gf("multiplier",    "randomization_factor",      0.25)
        self._max_speed_bps  = gf("multiplier",    "max_simulated_speed_mbps",  7.6) * 1_000_000 / 8
        self._slope          = gf("multiplier",    "max_upload_slope",          2.0)
        self._spoof_peers    = gb("anti_detection","spoof_peers",               True)
        self._peer_variance  = gf("anti_detection","peer_variance",             0.15)
        self._spoof_ua       = gb("anti_detection","spoof_user_agent",          True)
        self._ua_mode        = gs("anti_detection","user_agent_mode",           "random").lower()
        self._ua_value       = gs("anti_detection","user_agent_value",          "qBittorrent/4.6.7")
        self._persist        = gb("stats",         "persist_stats",             True)
        self._stats_file     = gs("stats",         "stats_file",                "stats.json")

        if self._ua_mode not in ("random", "fixed", "passthrough"):
            self._ua_mode = "random"

        if self._persist:
            self._load_stats()
            threading.Thread(target=self._autosave_loop, daemon=True, name="ng-autosave").start()

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load_stats(self):
        path = Path(self._stats_file)
        if not path.exists():
            return
        try:
            with path.open(encoding="utf-8") as f:
                data = json.load(f)
            self._global_ul      = float(data.get("global_real_uploaded",   0))
            self._global_dl      = float(data.get("global_real_downloaded", 0))
            self._torrents       = data.get("torrents", {})
            self._cooldown_until = float(data.get("cooldown_until", 0))
            logger.info(f"Stats loaded from {path} ({len(self._torrents)} torrents)")
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning(f"Could not load stats: {e}")

    def save_stats(self):
        try:
            with self._lock:
                payload = {
                    "version":                VERSION,
                    "global_real_uploaded":   self._global_ul,
                    "global_real_downloaded": self._global_dl,
                    "torrents":               self._torrents,
                    "cooldown_until":         self._cooldown_until,
                    "saved_at":               time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            path = Path(self._stats_file)
            tmp  = path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            tmp.replace(path)
        except OSError as e:
            logger.warning(f"Could not save stats: {e}")

    def _autosave_loop(self):
        while True:
            time.sleep(60)
            self.save_stats()

    # ── Core logic ─────────────────────────────────────────────────────────────

    def compute_reported_upload(self, info_hash, real_ul, real_dl, left, interval=1800.0):
        with self._lock:
            now          = time.time()
            is_seeding   = (left == 0)
            display_hash = normalize_info_hash(info_hash)[:8]

            # Cooldown active
            if now < self._cooldown_until:
                remaining = int(self._cooldown_until - now)
                logger.info(f"[COOLDOWN]     {display_hash} — {remaining}s remaining")
                self._store(info_hash, real_ul, real_dl, "COOLDOWN", real_ul)
                return real_ul

            # Trigger cooldown if ratio exceeded
            if self._global_dl > 0:
                ratio = self._global_ul / self._global_dl
                if ratio >= self._ratio_limit:
                    self._cooldown_until = now + self._cooldown_dur
                    logger.info(f"[COOLDOWN TRIGGERED] ratio={ratio:.3f} >= {self._ratio_limit}")
                    self._store(info_hash, real_ul, real_dl, "COOLDOWN", real_ul)
                    return real_ul

            # Multiplier + randomization
            base = self._seed_mul if is_seeding else self._mul
            mul  = max(1.0, base + base * self._rand * (random.random() * 2 - 1))

            # Slope guard (coherent progression)
            prev      = self._torrents.get(info_hash, {})
            prev_rep  = float(prev.get("last_reported_uploaded", 0))
            prev_dl   = float(prev.get("last_real_downloaded",   0))
            delta_dl  = max(0.0, real_dl - prev_dl)
            if delta_dl > 0:
                max_delta = delta_dl * self._slope
                if (real_ul * mul - prev_rep) > max_delta:
                    mul = max(1.0, (prev_rep + max_delta) / max(real_ul, 1))

            # Speed cap
            cap      = prev_rep + self._max_speed_bps * interval
            reported = int(min(real_ul * mul, cap))
            reported = max(reported, real_ul)

            mode = "SEEDING" if is_seeding else "DOWNLOADING"
            self._global_ul += real_ul
            self._global_dl += real_dl
            self._store(info_hash, real_ul, real_dl, mode, reported)

            logger.info(
                f"[{mode:<11}] {display_hash} | "
                f"DL: {real_dl/1e6:>8.2f} MB | "
                f"Real UL: {real_ul/1e6:>8.2f} MB | "
                f"Reported UL: {reported/1e6:>8.2f} MB | "
                f"Mul: {mul:.3f}"
            )
            return reported

    def _store(self, info_hash, real_ul, real_dl, mode, reported):
        self._torrents[info_hash] = {
            "info_hash_hex":          normalize_info_hash(info_hash),
            "last_real_uploaded":     real_ul,
            "last_real_downloaded":   real_dl,
            "last_reported_uploaded": reported,
            "mode":                   mode,
            "updated_at":             time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def spoof_peers_params(self, params):
        if not self._spoof_peers:
            return params
        for key in ("numwant", "num_peers", "num_seeds", "num_seeders"):
            if key in params:
                try:
                    val = int(params[key])
                    delta = int(val * self._peer_variance * (random.random() * 2 - 1))
                    params[key] = str(max(0, val + delta))
                except (ValueError, TypeError):
                    pass
        return params

    def get_user_agent(self, original_ua):
        if not self._spoof_ua:
            return original_ua
        return resolve_user_agent(self._ua_mode, self._ua_value, original_ua)

# ── Proxy handler ─────────────────────────────────────────────────────────────

ANNOUNCE_RE = re.compile(r"/announce", re.IGNORECASE)

class ProxyHandler(BaseHTTPRequestHandler):
    stats  = None
    cfg    = None

    def log_message(self, fmt, *args):
        pass

    def _rewrite_announce(self, params):
        info_hash = params.get("info_hash", "")
        try:
            real_ul = int(params.get("uploaded",   0))
            real_dl = int(params.get("downloaded", 0))
            left    = int(params.get("left",       1))
        except (ValueError, TypeError):
            return params
        reported = self.stats.compute_reported_upload(info_hash, real_ul, real_dl, left)
        params["uploaded"] = str(reported)
        return self.stats.spoof_peers_params(params)

    def _forward(self, method, url, body=b""):
        headers = dict(self.headers)
        headers["User-Agent"] = self.stats.get_user_agent(headers.get("User-Agent", ""))
        # Strip hop-by-hop headers
        for h in ("proxy-connection", "proxy-authenticate", "proxy-authorization",
                  "te", "trailers", "transfer-encoding", "upgrade", "connection", "keep-alive"):
            headers.pop(h, None)

        parsed = urllib.parse.urlparse(url)
        if ANNOUNCE_RE.search(parsed.path):
            params    = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
            params    = self._rewrite_announce(params)
            new_query = urllib.parse.urlencode(params)
            url       = urllib.parse.urlunparse(parsed._replace(query=new_query))

        try:
            timeout = self.cfg.getint("proxy", "tracker_timeout", fallback=5)
            verify  = self.cfg.getboolean("ssl", "ssl_verify_trackers", fallback=True)
            resp = requests.request(method, url, headers=headers, data=body,
                                    timeout=timeout, verify=verify, allow_redirects=True)
        except requests.exceptions.SSLError as e:
            logger.error(f"SSL error → {url}: {e}")
            self.send_error(502, "SSL error"); return
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error → {url}: {e}")
            self.send_error(502, "Connection error"); return
        except requests.exceptions.Timeout:
            logger.warning(f"Tracker timeout → {url}")
            self.send_error(504, "Tracker timeout"); return
        except Exception as e:
            logger.error(f"Forward error → {url}: {e}")
            self.send_error(502, str(e)); return

        self.send_response(resp.status_code)
        skip = {"content-encoding", "transfer-encoding", "connection",
                "keep-alive", "proxy-authenticate", "te", "trailers", "upgrade"}
        for k, v in resp.headers.items():
            if k.lower() not in skip:
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(resp.content)

    def do_GET(self):
        if not self.path.startswith("http"):
            self.send_error(400, "Absolute URL required"); return
        self._forward("GET", self.path)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length) if length else b""
        self._forward("POST", self.path, body)

    def do_CONNECT(self):
        """TCP tunnel for HTTPS — announces NOT modified in this mode."""
        host, _, port_str = self.path.partition(":")
        port = int(port_str) if port_str.isdigit() else 443
        try:
            remote = socket.create_connection((host, port), timeout=10)
        except OSError as e:
            self.send_error(502, str(e)); return
        self.send_response(200, "Connection established")
        self.end_headers()
        client = self.connection
        client.setblocking(False)
        remote.setblocking(False)
        try:
            while True:
                r, _, _ = select.select([client, remote], [], [], 30)
                if not r:
                    break
                for s in r:
                    data = s.recv(65536)
                    if not data:
                        return
                    (remote if s is client else client).sendall(data)
        except OSError:
            pass
        finally:
            remote.close()

# ── SSL cert auto-generation ──────────────────────────────────────────────────

def ensure_cert(certfile, keyfile):
    if Path(certfile).exists() and Path(keyfile).exists():
        return True
    try:
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048",
             "-keyout", keyfile, "-out", certfile,
             "-days", "3650", "-nodes", "-subj", "/CN=NewGreedy"],
            check=True, capture_output=True,
        )
        logger.info(f"Self-signed cert generated: {certfile}")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning(f"openssl unavailable: {e}")
        return False

# ── Update check ──────────────────────────────────────────────────────────────

def check_update():
    try:
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest", timeout=5)
        r.raise_for_status()
        latest = r.json().get("tag_name", "").lstrip("v")
        if latest and latest != VERSION:
            logger.info(f"Update available: v{latest}  (current: v{VERSION})")
        else:
            logger.info(f"NewGreedy v{VERSION} is up to date.")
    except Exception:
        pass

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    logger.info(f"NewGreedy v{VERSION} starting...")
    cfg = load_config()
    if not validate_config(cfg):
        logger.error("Aborting — fix config errors above.")
        sys.exit(1)

    threading.Thread(target=check_update, daemon=True, name="ng-update").start()

    stats = StatsManager(cfg)
    ProxyHandler.stats = stats
    ProxyHandler.cfg   = cfg

    port = cfg.getint("proxy", "listen_port", fallback=3456)
    server = HTTPServer(("0.0.0.0", port), ProxyHandler)

    use_https = cfg.getboolean("ssl", "enable_https", fallback=False)
    if use_https:
        certfile = cfg.get("ssl", "ssl_certfile", fallback="cert.pem")
        keyfile  = cfg.get("ssl", "ssl_keyfile",  fallback="key.pem")
        if cfg.getboolean("ssl", "ssl_autogenerate_cert", fallback=True):
            ensure_cert(certfile, keyfile)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        try:
            ctx.load_cert_chain(certfile, keyfile)
            server.socket = ctx.wrap_socket(server.socket, server_side=True)
        except (ssl.SSLError, OSError) as e:
            logger.error(f"SSL setup failed: {e}"); sys.exit(1)
        logger.info(f"HTTPS proxy listening on port {port}")
    else:
        logger.info(f"HTTP proxy listening on port {port}")

    logger.info(f"Configure qBittorrent: HTTP proxy → 127.0.0.1:{port}")
    logger.info("Ready.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutdown requested.")
    finally:
        server.server_close()
        if stats._persist:
            stats.save_stats()
        logger.info("Stopped.")

if __name__ == "__main__":
    main()
