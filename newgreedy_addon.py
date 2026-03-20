"""
NewGreedy v1.1 — mitmproxy addon
Full HTTP + HTTPS announce interception with:
  - User-Agent spoofing (random | fixed | passthrough)
  - Coherent upload progression (slope guard)
  - Numpeers / numseeds spoofing
  - Config validation
  - Stats persistence
  - info_hash hex display fix
"""

import binascii
import configparser
import json
import logging
import random
import re
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from mitmproxy import http

VERSION     = "1.1"
ANNOUNCE_RE = re.compile(r"/announce", re.IGNORECASE)

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("newgreedy.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("newgreedy_addon")

# ── Helpers ───────────────────────────────────────────────────────────────────

def normalize_info_hash(raw):
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
    if mode == "passthrough":
        return original_ua
    if mode == "fixed":
        return ua_value if ua_value else random.choice(QBIT_USER_AGENTS)
    # random
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
    try:
        ua_mode = cfg.get("anti_detection", "user_agent_mode", fallback="random").strip().lower()
    except Exception:
        ua_mode = "random"

    mul   = gf("multiplier",    "max_upload_multiplier", 1.6)
    rand  = gf("multiplier",    "randomization_factor",  0.25)
    rl    = gf("multiplier",    "global_ratio_limit",    1.8)
    slope = gf("multiplier",    "max_upload_slope",      2.0)
    pvar  = gf("anti_detection","peer_variance",         0.15)
    to    = gi("proxy",         "tracker_timeout",       5)

    logger.info("── Config validation (addon) ──────────────────────────")
    if mul > 5.0:   logger.warning(f"  max_upload_multiplier={mul} > 5.0 — high detection risk")
    if rand > 0.5:  logger.warning(f"  randomization_factor={rand} > 0.5 — excessive variance")
    if rl < 0.5:    logger.warning(f"  global_ratio_limit={rl} < 0.5 — cooldown triggers immediately")
    if slope > 5.0: logger.warning(f"  max_upload_slope={slope} > 5.0 — incoherent progression")
    if pvar > 0.5:  logger.warning(f"  peer_variance={pvar} > 0.5 — suspicious")
    if ua_mode not in ("random", "fixed", "passthrough"):
        logger.warning(f"  user_agent_mode=\"{ua_mode}\" unknown — falling back to random")
    if to < 1:
        logger.error(f"  tracker_timeout={to} < 1 — blocking value")
        ok = False
    logger.info(f"  multiplier={mul} | slope={slope} | ratio_limit={rl} | ua_mode={ua_mode}")
    logger.info("── Config OK ────────────────────────────────────────────")
    return ok

# ── StatsManager ─────────────────────────────────────────────────────────────

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
        def gb(s, k, fb):
            try: return cfg.getboolean(s, k)
            except: return fb
        def gs(s, k, fb):
            try: return cfg.get(s, k, fallback=fb).strip()
            except: return fb

        self._ratio_limit   = gf("multiplier",    "global_ratio_limit",        1.8)
        self._cooldown_dur  = gf("multiplier",    "cooldown_duration_minutes", 10.0) * 60
        self._mul           = gf("multiplier",    "max_upload_multiplier",     1.6)
        self._seed_mul      = gf("multiplier",    "seeding_multiplier",        1.2)
        self._rand          = gf("multiplier",    "randomization_factor",      0.25)
        self._max_speed_bps = gf("multiplier",    "max_simulated_speed_mbps",  7.6) * 1_000_000 / 8
        self._slope         = gf("multiplier",    "max_upload_slope",          2.0)
        self._spoof_peers   = gb("anti_detection","spoof_peers",               True)
        self._peer_variance = gf("anti_detection","peer_variance",             0.15)
        self._spoof_ua      = gb("anti_detection","spoof_user_agent",          True)
        self._ua_mode       = gs("anti_detection","user_agent_mode",           "random").lower()
        self._ua_value      = gs("anti_detection","user_agent_value",          "qBittorrent/4.6.7")
        self._persist       = gb("stats",         "persist_stats",             True)
        self._stats_file    = gs("stats",         "stats_file",                "stats.json")

        if self._ua_mode not in ("random", "fixed", "passthrough"):
            self._ua_mode = "random"

        if self._persist:
            self._load_stats()
            threading.Thread(target=self._autosave_loop, daemon=True, name="ng-autosave").start()

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

    def compute_reported_upload(self, info_hash, real_ul, real_dl, left, interval=1800.0):
        with self._lock:
            now          = time.time()
            is_seeding   = (left == 0)
            display_hash = normalize_info_hash(info_hash)[:8]

            if now < self._cooldown_until:
                remaining = int(self._cooldown_until - now)
                logger.info(f"[COOLDOWN]     {display_hash} — {remaining}s remaining")
                self._store(info_hash, real_ul, real_dl, "COOLDOWN", real_ul)
                return real_ul

            if self._global_dl > 0:
                ratio = self._global_ul / self._global_dl
                if ratio >= self._ratio_limit:
                    self._cooldown_until = now + self._cooldown_dur
                    logger.info(f"[COOLDOWN TRIGGERED] ratio={ratio:.3f} >= {self._ratio_limit}")
                    self._store(info_hash, real_ul, real_dl, "COOLDOWN", real_ul)
                    return real_ul

            base = self._seed_mul if is_seeding else self._mul
            mul  = max(1.0, base + base * self._rand * (random.random() * 2 - 1))

            prev      = self._torrents.get(info_hash, {})
            prev_rep  = float(prev.get("last_reported_uploaded", 0))
            prev_dl   = float(prev.get("last_real_downloaded",   0))
            delta_dl  = max(0.0, real_dl - prev_dl)
            if delta_dl > 0:
                max_delta = delta_dl * self._slope
                if (real_ul * mul - prev_rep) > max_delta:
                    mul = max(1.0, (prev_rep + max_delta) / max(real_ul, 1))

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
                f"Mul: {mul:.3f} | Protocol: HTTPS"
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

# ── mitmproxy Addon ───────────────────────────────────────────────────────────

cfg   = load_config()
ok    = validate_config(cfg)
if not ok:
    logger.error("Config validation failed — addon loaded with errors.")

stats = StatsManager(cfg)


class NewGreedyAddon:
    def request(self, flow: http.HTTPFlow) -> None:
        url    = flow.request.pretty_url
        parsed = urllib.parse.urlparse(url)
        if not ANNOUNCE_RE.search(parsed.path):
            return

        params    = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        info_hash = params.get("info_hash", "")
        try:
            real_ul = int(params.get("uploaded",   0))
            real_dl = int(params.get("downloaded", 0))
            left    = int(params.get("left",       1))
        except (ValueError, TypeError):
            return

        reported           = stats.compute_reported_upload(info_hash, real_ul, real_dl, left)
        params["uploaded"] = str(reported)
        params             = stats.spoof_peers_params(params)

        flow.request.query = urllib.parse.urlencode(params)

        original_ua = flow.request.headers.get("User-Agent", "")
        flow.request.headers["User-Agent"] = stats.get_user_agent(original_ua)


def start():
    logger.info(f"NewGreedy v{VERSION} addon loaded (mitmproxy mode).")
    logger.info("Intercepting HTTP + HTTPS announce requests.")


addons = [NewGreedyAddon()]
