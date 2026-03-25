"""
NewGreedy v1.3 - mitmproxy addon
Intercepts HTTP and HTTPS tracker announces on a single port.
"""

import binascii, configparser, json, logging, random, re
import signal, sys, threading, time, urllib.parse
from pathlib import Path
from mitmproxy import http

VERSION     = "1.3"
ANNOUNCE_RE = re.compile(r"/announce", re.IGNORECASE)
SCRAPE_RE   = re.compile(r"/scrape",   re.IGNORECASE)

QBIT_UA_MAP = {
    "qBittorrent/5.0.0": "-qB5000-",
    "qBittorrent/4.6.7": "-qB4670-",
    "qBittorrent/4.6.5": "-qB4650-",
    "qBittorrent/4.5.5": "-qB4550-",
    "qBittorrent/4.4.5": "-qB4450-",
    "Deluge/2.1.1":      "-DE211s-",
    "Transmission/3.00": "-TR3000-",
    "uTorrent/3.6.0":    "-UT3600-",
}
QBIT_USER_AGENTS    = list(QBIT_UA_MAP.keys())
KNOWN_CLIENT_PREFIX = ("qbittorrent", "deluge", "transmission", "utorrent", "libtorrent")
HEADERS_REMOVE      = frozenset([
    "x-forwarded-for", "via", "proxy-connection",
    "x-real-ip", "forwarded", "x-proxy-id",
])

_BASE_DIR = Path(__file__).parent
_LOG_FILE = _BASE_DIR / "newgreedy.log"


def _setup_logging():
    log = logging.getLogger("newgreedy")
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("[%(asctime)s] %(message)s", "%H:%M:%S.%f"[:-3])
    for h in (
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ):
        h.setFormatter(fmt)
        log.addHandler(h)
    return log

logger = _setup_logging()


# ── Helpers ────────────────────────────────────────────────────────────────────

def normalize_info_hash(raw):
    if not raw:
        return "unknown"
    try:
        b = raw.encode("latin-1") if isinstance(raw, str) else raw
        h = binascii.hexlify(b).decode("ascii")
        if len(h) == 40:
            return h
    except Exception:
        pass
    return "".join(c if c.isprintable() and c not in "\r\n" else "?" for c in str(raw))


def apply_noise(value, pct):
    if pct <= 0 or value <= 0:
        return value
    return max(0, int(random.gauss(value, value * pct / 100)))


def generate_peer_id(ua):
    prefix = QBIT_UA_MAP.get(ua, "-qB4670-")
    suffix = "".join(random.choices(
        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz", k=12
    ))
    return prefix + suffix


def rewrite_query(path, new_uploaded, peer_var=0.0,
                  spoof_port=False, port_range=(6881, 6999),
                  new_peer_id="", new_downloaded=-1):
    if "?" not in path:
        return path
    base, _, q = path.partition("?")

    def _sub(q, key, val):
        pat = r"(^|&)(" + re.escape(key) + r"=)[^&]*"
        rep = lambda m: m.group(1) + key + "=" + str(val)
        return re.sub(pat, rep, q) if re.search(pat, q) else q + "&" + key + "=" + str(val)

    q = _sub(q, "uploaded", new_uploaded)
    if new_downloaded >= 0:
        q = _sub(q, "downloaded", new_downloaded)
    if new_peer_id:
        q = _sub(q, "peer_id", urllib.parse.quote(new_peer_id, safe=""))
    if peer_var > 0:
        for key in ("numwant", "num_peers", "num_seeds", "num_seeders"):
            def _rp(m, k=key):
                try:
                    v = int(m.group(3))
                    return m.group(1) + k + "=" + str(
                        max(0, v + int(v * peer_var * (random.random() * 2 - 1))))
                except Exception:
                    return m.group(0)
            q = re.sub(r"(^|&)(" + re.escape(key) + r"=)([^&]*)", _rp, q)
    if spoof_port:
        p = random.randint(port_range[0], port_range[1])
        q = _sub(q, "port", p)

    return base + "?" + q


# ── Config ─────────────────────────────────────────────────────────────────────

def load_config(path=None):
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(path or (_BASE_DIR / "config.ini"), encoding="utf-8")
    return cfg


def validate_config(cfg):
    ok  = True
    gf  = lambda s, k, d: cfg.getfloat(s, k)   if cfg.has_option(s, k) else d
    gs  = lambda s, k, d: cfg.get(s, k, fallback=d).strip()
    gi  = lambda s, k, d: cfg.getint(s, k)      if cfg.has_option(s, k) else d
    logger.info("-- Config validation v%s --", VERSION)
    mode = gs("spoofing", "upload_mode", "ratio_based")
    if mode not in ("ratio_based", "multiplier"):
        logger.error("  ERROR: upload_mode must be ratio_based or multiplier")
        ok = False
    for cond, msg in [
        (gf("spoofing", "target_ratio",            1.5)  < 1.0,  "target_ratio < 1.0"),
        (gf("spoofing", "target_ratio",            1.5)  > 5.0,  "target_ratio > 5.0"),
        (gf("spoofing", "catch_up_factor",         0.15) > 1.0,  "catch_up_factor > 1.0"),
        (gf("spoofing", "upload_noise_pct",        3.0)  > 15.0, "upload_noise_pct > 15"),
        (gf("anti_detection", "peer_variance",     0.15) > 0.5,  "peer_variance > 0.5"),
    ]:
        if cond:
            logger.warning("  WARNING: %s", msg)
    logger.info("-- Config OK (mode=%s) --", mode)
    return ok


# ── Tracker filter ─────────────────────────────────────────────────────────────

class TrackerFilter:
    def __init__(self, cfg):
        def _p(key):
            raw = cfg.get("anti_detection", key, fallback="").strip()
            return {h.strip().lower() for h in raw.split(",") if h.strip()} if raw else set()
        self._white = _p("tracker_whitelist")
        self._black = _p("tracker_blacklist")

    def should_spoof(self, host):
        h = host.lower()
        if self._black and any(b in h for b in self._black):
            return False
        if self._white:
            return any(w in h for w in self._white)
        return True


# ── Interval guard ─────────────────────────────────────────────────────────────

class IntervalGuard:
    def __init__(self, default=1800):
        self._lock    = threading.Lock()
        self._last    = {}
        self._ivl     = {}
        self._default = default

    def allow(self, host):
        with self._lock:
            now = time.time()
            if now - self._last.get(host, 0) < self._ivl.get(host, self._default) * 0.9:
                return False
            self._last[host] = now
            return True

    def update(self, host, interval):
        with self._lock:
            self._ivl[host] = max(60, interval)


# ── Stats manager ──────────────────────────────────────────────────────────────

class StatsManager:

    def __init__(self, cfg):
        self._lock     = threading.Lock()
        self._torrents = {}
        self._cfg_path = str(_BASE_DIR / "config.ini")
        self._load_cfg(cfg)
        if self._persist:
            self._load_stats()
            threading.Thread(target=self._autosave, daemon=True, name="ng-save").start()
        try:
            signal.signal(signal.SIGHUP, self._on_sighup)
            logger.info("SIGHUP handler registered")
        except (OSError, AttributeError):
            pass

    # ── config ──────────────────────────────────────────────────────────────

    def _load_cfg(self, cfg):
        gf = lambda s, k, d: cfg.getfloat(s, k)   if cfg.has_option(s, k) else d
        gb = lambda s, k, d: cfg.getboolean(s, k) if cfg.has_option(s, k) else d
        gs = lambda s, k, d: cfg.get(s, k, fallback=d).strip()

        self._upload_mode   = gs("spoofing", "upload_mode",              "ratio_based").lower()
        self._target_ratio  = gf("spoofing", "target_ratio",              1.5)
        self._max_ratio     = gf("spoofing", "max_ratio_per_torrent",     3.0)
        self._seed_credit   = int(gf("spoofing", "seed_credit_mb",        5.0) * 1_000_000)
        self._catch_up      = gf("spoofing", "catch_up_factor",           0.15)
        self._max_speed_bps = gf("spoofing", "max_simulated_speed_mbps",  10.0) * 125_000
        self._noise_pct     = gf("spoofing", "upload_noise_pct",          3.0)
        self._dl_ratio      = gf("spoofing", "seeding_dl_ratio",          0.85)

        self._spoof_ua      = gb("anti_detection", "spoof_user_agent",    True)
        self._ua_mode       = gs("anti_detection", "user_agent_mode",     "random").lower()
        self._ua_value      = gs("anti_detection", "user_agent_value",    "qBittorrent/4.6.7")
        self._spoof_pid     = gb("anti_detection", "spoof_peer_id",       True)
        self._spoof_port    = gb("anti_detection", "spoof_port",          True)
        self._spoof_peers   = gb("anti_detection", "spoof_peers",         True)
        self._peer_var      = gf("anti_detection", "peer_variance",       0.15)
        self._spoof_hdrs    = gb("anti_detection", "spoof_headers",       True)
        self._persist       = gb("stats", "persist_stats",   True)
        self._stats_file    = _BASE_DIR / gs("stats", "stats_file", "stats.json")

        pr = gs("anti_detection", "port_range", "6881-6999")
        try:
            lo, hi = pr.split("-")
            self._port_range = (int(lo), int(hi))
        except Exception:
            self._port_range = (6881, 6999)

        if self._ua_mode not in ("random", "fixed", "passthrough"):
            self._ua_mode = "random"

    def _on_sighup(self, *_):
        logger.info("SIGHUP received -- reloading config...")
        try:
            nc = load_config(self._cfg_path)
            if validate_config(nc):
                with self._lock:
                    self._load_cfg(nc)
                logger.info("Config reloaded OK.")
        except Exception as e:
            logger.error("Config reload error: %s", e)

    # ── persistence ─────────────────────────────────────────────────────────

    def _load_stats(self):
        p = Path(self._stats_file)
        if not p.exists():
            return
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            self._torrents = d.get("torrents", {})
            logger.info("Stats loaded: %d torrent(s) tracked", len(self._torrents))
        except Exception as e:
            logger.warning("Could not load stats: %s", e)

    def save(self):
        try:
            with self._lock:
                payload = {
                    "version":  VERSION,
                    "torrents": self._torrents,
                    "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            p   = Path(self._stats_file)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(p)
        except OSError as e:
            logger.warning("Could not save stats: %s", e)

    def _autosave(self):
        while True:
            time.sleep(60)
            self.save()

    # ── core calculation ─────────────────────────────────────────────────────

    def compute(self, info_hash, real_ul, real_dl, left, interval=1800.0):
        with self._lock:
            is_seed  = (left == 0)
            dhash    = normalize_info_hash(info_hash)[:8]
            prev     = self._torrents.get(info_hash, {})
            ann_cnt  = int(prev.get("announce_count", 0)) + 1
            cumul_dl     = float(prev.get("cumul_dl",     0)) + real_dl
            cumul_rep_ul = float(prev.get("cumul_rep_ul", 0))

            if self._upload_mode == "ratio_based":
                reported = self._ratio_based(real_ul, is_seed, cumul_dl, cumul_rep_ul, interval)
            else:
                mul      = max(1.0, random.gauss(1.4, 0.15))
                reported = max(apply_noise(int(real_ul * mul), self._noise_pct), real_ul)

            # Inject downloaded for pure seeders
            rep_dl = real_dl
            if is_seed and real_dl == 0 and reported > 0 and self._dl_ratio > 0:
                rep_dl = apply_noise(int(reported * self._dl_ratio), self._noise_pct)

            new_rep  = cumul_rep_ul + reported
            ratio_t  = new_rep / cumul_dl if cumul_dl > 0 else 0.0

            self._torrents[info_hash] = {
                "info_hash_hex":  normalize_info_hash(info_hash),
                "cumul_dl":       cumul_dl,
                "cumul_rep_ul":   new_rep,
                "announce_count": ann_cnt,
                "mode":           "SEEDING" if is_seed else "DOWNLOADING",
                "ratio":          round(ratio_t, 4),
                "updated_at":     time.strftime("%Y-%m-%dT%H:%M:%S"),
            }

            logger.info(
                "[%-11s] %s | DL:%8.2fMB | RealUL:%8.2fMB | RepUL:%8.2fMB | Ratio:%.3f | Ann#%d",
                "SEEDING" if is_seed else "DOWNLOADING", dhash,
                real_dl / 1e6, real_ul / 1e6, reported / 1e6, ratio_t, ann_cnt,
            )
            return reported, rep_dl

    def _ratio_based(self, real_ul, is_seed, cumul_dl, cumul_rep_ul, interval):
        if cumul_dl > 0:
            eff   = random.gauss(self._target_ratio, self._target_ratio * self._noise_pct / 100)
            eff   = max(1.0, min(eff, self._target_ratio * 1.4))
            delta = max(0.0, cumul_dl * eff - cumul_rep_ul)
            rep   = min(int(delta * self._catch_up), int(self._max_speed_bps * interval))
            rep   = max(rep, real_ul)
            if cumul_dl > 0 and (cumul_rep_ul + rep) / cumul_dl >= self._max_ratio:
                rep = real_ul
        elif is_seed:
            lo  = int(self._seed_credit * 0.4)
            hi  = int(self._seed_credit * 1.8)
            rep = max(real_ul, int(random.triangular(lo, hi, self._seed_credit)))
        else:
            rep = real_ul
        return max(apply_noise(rep, self._noise_pct), real_ul)

    # ── UA / peer_id helpers ─────────────────────────────────────────────────

    def get_ua(self, original):
        if not self._spoof_ua or self._ua_mode == "passthrough":
            return original
        if self._ua_mode == "fixed":
            return self._ua_value or random.choice(QBIT_USER_AGENTS)
        if original and any(p in original.lower() for p in KNOWN_CLIENT_PREFIX):
            return original
        return random.choice(QBIT_USER_AGENTS)

    def get_peer_id(self, ua):
        return generate_peer_id(ua) if self._spoof_pid else ""

    @property
    def peer_var(self):   return self._peer_var if self._spoof_peers else 0.0
    @property
    def spoof_port(self): return self._spoof_port
    @property
    def port_range(self): return self._port_range
    @property
    def spoof_hdrs(self): return self._spoof_hdrs


# ── Module-level singletons ────────────────────────────────────────────────────

_cfg    = load_config()
validate_config(_cfg)
_stats  = StatsManager(_cfg)
_filter = TrackerFilter(_cfg)
_guard  = IntervalGuard(_cfg.getint("advanced", "min_announce_interval", fallback=1800))


# ── mitmproxy addon ────────────────────────────────────────────────────────────

class NewGreedyAddon:

    def request(self, flow: http.HTTPFlow):
        path = flow.request.path
        host = flow.request.pretty_host

        if not ANNOUNCE_RE.search(path):
            return
        if not _filter.should_spoof(host):
            return
        if not _guard.allow(host):
            logger.debug("[INTERVAL GUARD] %s -- skipped", host)
            return
        if "?" not in path:
            return

        _, _, raw_q = path.partition("?")
        try:
            params = dict(urllib.parse.parse_qsl(raw_q, keep_blank_values=True, encoding="latin-1"))
        except Exception as e:
            logger.debug("Cannot parse query params for announce request: %s", e)
            return

        ih = params.get("info_hash", "")
        if not ih:
            return

        try:
            real_ul = int(params.get("uploaded",   0))
            real_dl = int(params.get("downloaded", 0))
            left    = int(params.get("left",       1))
        except (ValueError, TypeError):
            return

        rep_ul, rep_dl = _stats.compute(ih, real_ul, real_dl, left)
        ua      = _stats.get_ua(flow.request.headers.get("User-Agent", ""))
        peer_id = _stats.get_peer_id(ua)
        inj_dl  = rep_dl if (left == 0 and real_dl == 0 and rep_dl != real_dl) else -1

        flow.request.path = rewrite_query(
            path, rep_ul,
            _stats.peer_var, _stats.spoof_port, _stats.port_range,
            peer_id, inj_dl,
        )

        if _stats.spoof_hdrs:
            for h in list(flow.request.headers.keys()):
                if h.lower() in HEADERS_REMOVE:
                    del flow.request.headers[h]
            flow.request.headers["User-Agent"]      = ua
            flow.request.headers["Accept-Encoding"] = "gzip"
            flow.request.headers["Accept"]          = "*/*"
            flow.request.headers["Connection"]      = "close"


addons = [NewGreedyAddon()]
