"""NewGreedy v1.1 — mitmproxy addon (HTTP + HTTPS announce interception)"""

import binascii, configparser, json, logging, random, re, sys, threading, time, urllib.parse
from pathlib import Path
from mitmproxy import http

VERSION     = "1.1"
ANNOUNCE_RE = re.compile(r"/announce", re.IGNORECASE)

QBIT_USER_AGENTS = [
    "qBittorrent/5.0.0", "qBittorrent/4.6.7", "qBittorrent/4.6.5",
    "qBittorrent/4.5.5", "qBittorrent/4.4.5",
    "Deluge/2.1.1", "Transmission/3.00", "uTorrent/3.6.0",
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

def normalize_info_hash(raw):
    if not raw:
        return "unknown"
    try:
        h = binascii.hexlify(urllib.parse.unquote_to_bytes(raw)).decode("ascii")
        if len(h) == 40:
            return h
    except Exception:
        pass
    return "".join(c if c.isprintable() and c not in "\r\n" else "?" for c in raw)

def resolve_ua(mode, ua_value, original_ua):
    if mode == "passthrough":
        return original_ua
    if mode == "fixed":
        return ua_value or random.choice(QBIT_USER_AGENTS)
    if original_ua and any(p in original_ua.lower() for p in KNOWN_CLIENT_PREFIXES):
        return original_ua
    return random.choice(QBIT_USER_AGENTS)

def load_config(path="config.ini"):
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(path, encoding="utf-8")
    return cfg

def validate_config(cfg):
    ok = True
    gf = lambda s, k, d: cfg.getfloat(s, k) if cfg.has_option(s, k) else d
    gi = lambda s, k, d: cfg.getint(s, k)   if cfg.has_option(s, k) else d
    logger.info("── Config validation (addon) ──────────────────────────")
    if gf("multiplier", "max_upload_multiplier", 1.6) > 5.0:
        logger.warning("  max_upload_multiplier > 5.0 — high detection risk")
    if gf("multiplier", "randomization_factor", 0.25) > 0.5:
        logger.warning("  randomization_factor > 0.5 — excessive variance")
    if gf("multiplier", "global_ratio_limit", 1.8) < 0.5:
        logger.warning("  global_ratio_limit < 0.5 — cooldown triggers immediately")
    if gf("multiplier", "max_upload_slope", 2.0) > 5.0:
        logger.warning("  max_upload_slope > 5.0 — incoherent progression")
    if gf("anti_detection", "peer_variance", 0.15) > 0.5:
        logger.warning("  peer_variance > 0.5 — suspicious")
    to = gi("proxy", "tracker_timeout", 5)
    if to < 1:
        logger.error(f"  tracker_timeout={to} < 1 — blocking value")
        ok = False
    ua_mode = cfg.get("anti_detection", "user_agent_mode", fallback="random").strip().lower()
    if ua_mode not in ("random", "fixed", "passthrough"):
        logger.warning(f'  user_agent_mode="{ua_mode}" unknown — using random')
    logger.info(f"  multiplier={gf('multiplier','max_upload_multiplier',1.6)} | ua_mode={ua_mode} | timeout={to}s")
    logger.info("── Config OK ────────────────────────────────────────────")
    return ok

class StatsManager:
    def __init__(self, cfg):
        self._lock           = threading.Lock()
        self._global_ul      = 0.0
        self._global_dl      = 0.0
        self._torrents       = {}
        self._cooldown_until = 0.0
        gf = lambda s, k, d: cfg.getfloat(s, k)   if cfg.has_option(s, k) else d
        gb = lambda s, k, d: cfg.getboolean(s, k) if cfg.has_option(s, k) else d
        gs = lambda s, k, d: cfg.get(s, k, fallback=d).strip()
        self._ratio_limit   = gf("multiplier",     "global_ratio_limit",        1.8)
        self._cooldown_dur  = gf("multiplier",     "cooldown_duration_minutes", 10.0) * 60
        self._mul           = gf("multiplier",     "max_upload_multiplier",     1.6)
        self._seed_mul      = gf("multiplier",     "seeding_multiplier",        1.2)
        self._rand          = gf("multiplier",     "randomization_factor",      0.25)
        self._max_speed_bps = gf("multiplier",     "max_simulated_speed_mbps",  7.6) * 125_000
        self._slope         = gf("multiplier",     "max_upload_slope",          2.0)
        self._spoof_peers   = gb("anti_detection", "spoof_peers",               True)
        self._peer_var      = gf("anti_detection", "peer_variance",             0.15)
        self._spoof_ua      = gb("anti_detection", "spoof_user_agent",          True)
        self._ua_mode       = gs("anti_detection", "user_agent_mode",           "random").lower()
        self._ua_value      = gs("anti_detection", "user_agent_value",          "qBittorrent/4.6.7")
        self._persist       = gb("stats",          "persist_stats",             True)
        self._stats_file    = gs("stats",          "stats_file",                "stats.json")
        if self._ua_mode not in ("random", "fixed", "passthrough"):
            self._ua_mode = "random"
        if self._persist:
            self._load()
            threading.Thread(target=self._autosave, daemon=True, name="ng-save").start()

    def _load(self):
        p = Path(self._stats_file)
        if not p.exists():
            return
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            self._global_ul      = float(d.get("global_real_uploaded",   0))
            self._global_dl      = float(d.get("global_real_downloaded", 0))
            self._torrents       = d.get("torrents", {})
            self._cooldown_until = float(d.get("cooldown_until", 0))
            logger.info(f"Stats loaded: {len(self._torrents)} torrents")
        except Exception as e:
            logger.warning(f"Could not load stats: {e}")

    def save(self):
        try:
            with self._lock:
                payload = {
                    "version": VERSION,
                    "global_real_uploaded":   self._global_ul,
                    "global_real_downloaded": self._global_dl,
                    "torrents":               self._torrents,
                    "cooldown_until":         self._cooldown_until,
                    "saved_at":               time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            p = Path(self._stats_file)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(p)
        except OSError as e:
            logger.warning(f"Could not save stats: {e}")

    def _autosave(self):
        while True:
            time.sleep(60)
            self.save()

    def compute_reported_upload(self, info_hash, real_ul, real_dl, left, interval=1800.0):
        with self._lock:
            now   = time.time()
            dhash = normalize_info_hash(info_hash)[:8]
            if now < self._cooldown_until:
                logger.info(f"[COOLDOWN    ] {dhash} — {int(self._cooldown_until-now)}s remaining")
                self._store(info_hash, real_ul, real_dl, "COOLDOWN", real_ul)
                return real_ul
            if self._global_dl > 0 and (self._global_ul/self._global_dl) >= self._ratio_limit:
                self._cooldown_until = now + self._cooldown_dur
                logger.info(f"[COOLDOWN TRIGGERED] ratio={self._global_ul/self._global_dl:.3f}")
                self._store(info_hash, real_ul, real_dl, "COOLDOWN", real_ul)
                return real_ul
            base = self._seed_mul if left == 0 else self._mul
            mul  = max(1.0, base + base * self._rand * (random.random() * 2 - 1))
            prev     = self._torrents.get(info_hash, {})
            prev_rep = float(prev.get("last_reported_uploaded", 0))
            delta_dl = max(0.0, real_dl - float(prev.get("last_real_downloaded", 0)))
            if delta_dl > 0 and (real_ul * mul - prev_rep) > delta_dl * self._slope:
                mul = max(1.0, (prev_rep + delta_dl * self._slope) / max(real_ul, 1))
            reported = max(real_ul, int(min(real_ul * mul,
                                            prev_rep + self._max_speed_bps * interval)))
            mode = "SEEDING" if left == 0 else "DOWNLOADING"
            self._global_ul += real_ul
            self._global_dl += real_dl
            self._store(info_hash, real_ul, real_dl, mode, reported)
            logger.info(f"[{mode:<11}] {dhash} | DL:{real_dl/1e6:>8.2f}MB | "
                        f"Real UL:{real_ul/1e6:>8.2f}MB | "
                        f"Reported UL:{reported/1e6:>8.2f}MB | "
                        f"Mul:{mul:.3f} | Protocol:HTTPS")
            return reported

    def _store(self, info_hash, real_ul, real_dl, mode, reported):
        self._torrents[info_hash] = {
            "info_hash_hex":          normalize_info_hash(info_hash),
            "last_real_uploaded":     real_ul,
            "last_real_downloaded":   real_dl,
            "last_reported_uploaded": reported,
            "mode": mode,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

    def spoof_peers(self, params):
        if not self._spoof_peers:
            return params
        for k in ("numwant", "num_peers", "num_seeds", "num_seeders"):
            if k in params:
                try:
                    v = int(params[k])
                    params[k] = str(max(0, v + int(v * self._peer_var * (random.random()*2-1))))
                except (ValueError, TypeError):
                    pass
        return params

    def get_ua(self, original):
        return resolve_ua(self._ua_mode, self._ua_value, original) if self._spoof_ua else original

cfg   = load_config()
ok    = validate_config(cfg)
if not ok:
    logger.error("Config validation failed — addon loaded with errors.")
stats = StatsManager(cfg)

class NewGreedyAddon:
    def request(self, flow: http.HTTPFlow) -> None:
        parsed = urllib.parse.urlparse(flow.request.pretty_url)
        if not ANNOUNCE_RE.search(parsed.path):
            return
        params = dict(urllib.parse.parse_qsl(
            parsed.query, keep_blank_values=True, encoding="latin-1"
        ))
        info_hash = params.get("info_hash", "")
        try:
            real_ul = int(params.get("uploaded",   0))
            real_dl = int(params.get("downloaded", 0))
            left    = int(params.get("left",       1))
        except (ValueError, TypeError):
            return
        reported           = stats.compute_reported_upload(info_hash, real_ul, real_dl, left)
        params["uploaded"] = str(reported)
        params             = stats.spoof_peers(params)
        clean = {k: v if isinstance(v, str) else v.encode("latin-1").decode("latin-1")
                 for k, v in params.items()}
        flow.request.query = list(clean.items())
        flow.request.headers["User-Agent"] = stats.get_ua(
            flow.request.headers.get("User-Agent", "")
        )

def start():
    logger.info(f"NewGreedy v{VERSION} addon loaded — intercepting HTTP+HTTPS announces.")

addons = [NewGreedyAddon()]
