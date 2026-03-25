"""
NewGreedy v1.4 - mitmproxy addon
Intercepts HTTP and HTTPS tracker announces on a single port.
UDP tracker announces are passed through without modification.

v1.4 anti-detection improvements:
  1. target_ratio randomized 1.42-1.54 at first run (anti-clustering)
  2. Non-linear ratio progression (exponential decay by announce age)
  3. Simulated stagnation: 15% chance of no credit per announce
  4. peer_id rotated every 4-6h (not fixed per session)
  5. downloaded from simulated session (not UL x fixed ratio)
  6. UA-specific Qt header sets per client
  7. Random delay 0.5-8s between multi-tracker announces (same torrent)
  8. Spontaneous event=stopped/started anomalies (~3% per torrent session)
  9. Per-announce detectability score logged at DEBUG level
"""

import binascii, configparser, json, logging, math, random, re
import signal, sys, threading, time, urllib.parse
from pathlib import Path
from mitmproxy import http

VERSION     = "1.4"
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

# Realistic Qt/libtorrent header sets -- varies per client build
QBIT_HEADERS = {
    "qBittorrent/5.0.0": {"Accept": "*/*", "Accept-Encoding": "gzip", "Connection": "close"},
    "qBittorrent/4.6.7": {"Accept": "*/*", "Accept-Encoding": "gzip", "Connection": "close"},
    "qBittorrent/4.6.5": {"Accept": "*/*", "Accept-Encoding": "gzip", "Connection": "close"},
    "qBittorrent/4.5.5": {"Accept": "*/*", "Accept-Encoding": "gzip", "Connection": "close"},
    "qBittorrent/4.4.5": {"Accept": "*/*", "Accept-Encoding": "gzip"},
    "Deluge/2.1.1":      {"Accept-Encoding": "gzip", "Accept": "*/*"},
    "Transmission/3.00": {"Accept": "*/*",
                          "Accept-Encoding": "gzip;q=1.0, deflate, identity"},
    "uTorrent/3.6.0":    {"Accept-Encoding": "gzip", "Connection": "close"},
}

QBIT_USER_AGENTS    = list(QBIT_UA_MAP.keys())
KNOWN_CLIENT_PREFIX = ("qbittorrent", "deluge", "transmission", "utorrent", "libtorrent")
HEADERS_REMOVE      = frozenset([
    "x-forwarded-for", "via", "proxy-connection",
    "x-real-ip", "forwarded", "x-proxy-id",
])

_BASE_DIR = Path(__file__).parent

# Anti-clustering: if config still has exactly 1.5, randomize at startup
_DEFAULT_RATIO_SENTINEL = 1.5
_RATIO_LOW,  _RATIO_HIGH = 1.42, 1.54


def _setup_logging():
    """Single named logger, stdout only.
    systemd writes stdout to log file via StandardOutput=append:.
    A FileHandler here would cause every line to appear twice.
    """
    log = logging.getLogger("newgreedy")
    if log.handlers:
        return log
    log.setLevel(logging.DEBUG)
    log.propagate = False
    fmt = logging.Formatter("[%(asctime)s] %(message)s", "%H:%M:%S")
    h = logging.StreamHandler(sys.stdout)
    h.setLevel(logging.INFO)   # INFO+ to stdout; DEBUG only via log file if added later
    h.setFormatter(fmt)
    log.addHandler(h)
    return log

logger = _setup_logging()


# -- Helpers --------------------------------------------------------------------

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
                  new_peer_id="", new_downloaded=-1, new_event=""):
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
    if new_event:
        q = _sub(q, "event", new_event)
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


# -- Config ---------------------------------------------------------------------

def load_config(path=None):
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(path or (_BASE_DIR / "config.ini"), encoding="utf-8")
    return cfg


def _randomize_ratio_if_default(cfg_path):
    """
    v1.4 anti-clustering: if target_ratio == 1.5 (default sentinel),
    randomize it to a value in [1.42, 1.54] and write back to config.ini.
    Prevents multiple NewGreedy users appearing identical to the tracker.
    Only runs once -- subsequent starts use the persisted value.
    """
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(cfg_path, encoding="utf-8")
    try:
        current = cfg.getfloat("spoofing", "target_ratio")
        if abs(current - _DEFAULT_RATIO_SENTINEL) < 0.001:
            new_ratio = round(random.uniform(_RATIO_LOW, _RATIO_HIGH), 3)
            cfg.set("spoofing", "target_ratio", str(new_ratio))
            with open(cfg_path, "w", encoding="utf-8") as f:
                cfg.write(f)
            logger.info(
                "target_ratio randomized: %.3f (range %.2f-%.2f, anti-clustering)",
                new_ratio, _RATIO_LOW, _RATIO_HIGH,
            )
    except Exception:
        pass


def validate_config(cfg):
    gf = lambda s, k, d: cfg.getfloat(s, k) if cfg.has_option(s, k) else d
    gs = lambda s, k, d: cfg.get(s, k, fallback=d).strip()
    logger.info("-- Config validation v%s --", VERSION)
    mode = gs("spoofing", "upload_mode", "ratio_based")
    if mode not in ("ratio_based", "multiplier"):
        logger.error("  ERROR: upload_mode must be ratio_based or multiplier")
        return False
    for cond, msg in [
        (gf("spoofing", "target_ratio",        1.5)  < 1.0,  "target_ratio < 1.0"),
        (gf("spoofing", "target_ratio",        1.5)  > 5.0,  "target_ratio > 5.0"),
        (gf("spoofing", "catch_up_factor",     0.15) > 1.0,  "catch_up_factor > 1.0"),
        (gf("spoofing", "upload_noise_pct",    3.0)  > 15.0, "upload_noise_pct > 15"),
        (gf("anti_detection", "peer_variance", 0.15) > 0.5,  "peer_variance > 0.5"),
    ]:
        if cond:
            logger.warning("  WARNING: %s", msg)
    logger.info("-- Config OK (mode=%s) --", mode)
    return True


# -- Detectability scorer -------------------------------------------------------

def detectability_score(real_ul, reported, cumul_dl, cumul_rep_ul, ann_cnt,
                        is_stagnation, is_duplicate, noise_pct):
    """
    v1.4: Compute a suspicion score (0=clean, 10=very suspicious) for DEBUG.
    This is an internal heuristic -- not sent anywhere.
    """
    score = 0.0
    # Perfect ratio multiple times = suspicious
    if cumul_dl > 0:
        ratio = (cumul_rep_ul + reported) / cumul_dl
        if abs(ratio - round(ratio, 1)) < 0.02:
            score += 2.0  # ratio suspiciously round

    # Upload growing too fast vs real
    if real_ul > 0 and reported > real_ul * 5:
        score += 2.0
    elif real_ul == 0 and reported > 0:
        score += 1.0  # upload with no real activity

    # Very early announces with big credit
    if ann_cnt <= 3 and reported > 50_000_000:
        score += 1.5

    # No stagnation after many announces
    if ann_cnt > 30 and not is_stagnation:
        score += 0.5

    # Low noise = too consistent
    if noise_pct < 1.0:
        score += 1.0

    score = min(10.0, score)
    return round(score, 1)


# -- Tracker filter -------------------------------------------------------------

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


# -- Interval guard -------------------------------------------------------------

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


# -- Stats manager --------------------------------------------------------------

class StatsManager:

    def __init__(self, cfg):
        self._lock            = threading.Lock()
        self._torrents        = {}
        self._last_accumulate = {}   # {info_hash: timestamp}  dedup multi-tracker
        self._peer_id_cache   = {}   # {ua: (peer_id, generated_at)}  rotation 4-6h
        self._sim_dl_cache    = {}   # {info_hash: simulated_dl}  session downloaded
        self._event_anomaly   = {}   # {info_hash: next_anomaly_ann}  stopped/started
        self._cfg_path        = str(_BASE_DIR / "config.ini")
        self._load_cfg(cfg)
        if self._persist:
            self._load_stats()
            threading.Thread(target=self._autosave, daemon=True, name="ng-save").start()
        try:
            signal.signal(signal.SIGHUP, self._on_sighup)
            logger.info("SIGHUP handler registered")
        except (OSError, AttributeError):
            pass

    # -- config ------------------------------------------------------------------

    def _load_cfg(self, cfg):
        gf = lambda s, k, d: cfg.getfloat(s, k)   if cfg.has_option(s, k) else d
        gb = lambda s, k, d: cfg.getboolean(s, k) if cfg.has_option(s, k) else d
        gs = lambda s, k, d: cfg.get(s, k, fallback=d).strip()

        self._upload_mode   = gs("spoofing", "upload_mode",             "ratio_based").lower()
        self._target_ratio  = gf("spoofing", "target_ratio",             1.5)
        self._max_ratio     = gf("spoofing", "max_ratio_per_torrent",    3.0)
        self._seed_credit   = int(gf("spoofing", "seed_credit_mb",       5.0) * 1_000_000)
        self._catch_up      = gf("spoofing", "catch_up_factor",          0.15)
        self._max_speed_bps = gf("spoofing", "max_simulated_speed_mbps", 10.0) * 125_000
        self._noise_pct     = gf("spoofing", "upload_noise_pct",         3.0)
        self._dl_ratio      = gf("spoofing", "seeding_dl_ratio",         0.85)

        self._spoof_ua      = gb("anti_detection", "spoof_user_agent",   True)
        self._ua_mode       = gs("anti_detection", "user_agent_mode",    "random").lower()
        self._ua_value      = gs("anti_detection", "user_agent_value",   "qBittorrent/4.6.7")
        self._spoof_pid     = gb("anti_detection", "spoof_peer_id",      True)
        self._spoof_port    = gb("anti_detection", "spoof_port",         True)
        self._spoof_peers   = gb("anti_detection", "spoof_peers",        True)
        self._peer_var      = gf("anti_detection", "peer_variance",      0.15)
        self._spoof_hdrs    = gb("anti_detection", "spoof_headers",      True)
        self._persist       = gb("stats", "persist_stats",               True)
        self._stats_file    = _BASE_DIR / gs("stats", "stats_file",      "stats.json")

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

    # -- persistence -------------------------------------------------------------

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

    # -- core compute ------------------------------------------------------------

    def compute(self, info_hash, real_ul, real_dl, left, interval=1800.0):
        with self._lock:
            is_seed      = (left == 0)
            dhash        = normalize_info_hash(info_hash)[:8]
            prev         = self._torrents.get(info_hash, {})
            ann_cnt      = int(prev.get("announce_count", 0)) + 1
            cumul_dl     = float(prev.get("cumul_dl",     0)) + real_dl
            cumul_rep_ul = float(prev.get("cumul_rep_ul", 0))

            # v1.4 #7 -- Deduplicate multi-tracker announces.
            # Random delay 0.5-8s is applied in NewGreedyAddon.request()
            # before forwarding the duplicate to de-synchronize tracker timestamps.
            now       = time.time()
            duplicate = (now - self._last_accumulate.get(info_hash, 0)) < 10.0

            is_stagnation = False
            if duplicate:
                reported = 0
                rep_dl   = real_dl
            else:
                self._last_accumulate[info_hash] = now

                # v1.4 #3 -- 15% stagnation: simulate no upload this announce
                if random.random() < 0.15:
                    reported      = real_ul
                    is_stagnation = True
                elif self._upload_mode == "ratio_based":
                    reported = self._ratio_based(
                        real_ul, is_seed, cumul_dl, cumul_rep_ul, interval, ann_cnt
                    )
                else:
                    mul      = max(1.0, random.gauss(1.4, 0.15))
                    reported = max(apply_noise(int(real_ul * mul), self._noise_pct), real_ul)

                # v1.4 #5 -- simulated downloaded for pure seeders
                rep_dl = real_dl
                if is_seed and real_dl == 0 and reported > 0:
                    rep_dl = self._sim_downloaded(info_hash, reported)

            new_rep = cumul_rep_ul + reported

            # Display
            if cumul_dl > 0:
                ratio_t   = new_rep / cumul_dl
                ratio_str = "Ratio:%.3f    " % ratio_t
            elif is_seed:
                ratio_t   = 0.0
                ratio_str = "SeedUL:%7.2fMB" % (new_rep / 1e6)
            else:
                ratio_t   = 0.0
                ratio_str = "Ratio:0.000    "

            # Tags
            tags = []
            if duplicate:    tags.append("DUP")
            if is_stagnation: tags.append("STAG")
            tag_str = (" [" + "|".join(tags) + "]") if tags else ""

            # v1.4 #9 -- Detectability score (DEBUG only)
            dscore = detectability_score(
                real_ul, reported, cumul_dl, new_rep, ann_cnt,
                is_stagnation, duplicate, self._noise_pct,
            )
            logger.debug("[DETECT_SCORE] %s score=%.1f/10", dhash, dscore)

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
                "[%-11s] %s | DL:%8.2fMB | RealUL:%8.2fMB | RepUL:%8.2fMB | %s | Ann#%d%s",
                "SEEDING" if is_seed else "DOWNLOADING", dhash,
                real_dl / 1e6, real_ul / 1e6, reported / 1e6, ratio_str, ann_cnt, tag_str,
            )
            return reported, rep_dl

    def _ratio_based(self, real_ul, is_seed, cumul_dl, cumul_rep_ul, interval, ann_cnt=1):
        if cumul_dl > 0:
            # v1.4 #2 -- exponential decay: heavy credit early, tapers with age
            # ann#1  -> +30% boost ; ann#20 -> +11% ; ann#50 -> ~2%
            age_factor = math.exp(-ann_cnt / 20.0)
            eff = random.gauss(
                self._target_ratio * (1 + age_factor * 0.3),
                self._target_ratio * self._noise_pct / 100,
            )
            eff   = max(1.0, min(eff, self._target_ratio * 1.4))
            delta = max(0.0, cumul_dl * eff - cumul_rep_ul)
            rep   = min(int(delta * self._catch_up), int(self._max_speed_bps * interval))
            rep   = max(rep, real_ul)
            if (cumul_rep_ul + rep) / cumul_dl >= self._max_ratio:
                rep = real_ul
        elif is_seed:
            # v1.4 #2 -- seed credit also decays with age
            age_factor = math.exp(-ann_cnt / 15.0)
            base = self._seed_credit * (1 + age_factor * 2)
            lo   = int(base * 0.4)
            hi   = int(base * 1.8)
            rep  = max(real_ul, int(random.triangular(lo, hi, int(base))))
        else:
            rep = real_ul
        return max(apply_noise(rep, self._noise_pct), real_ul)

    def _sim_downloaded(self, info_hash, reported):
        """
        v1.4 #5 -- Simulated downloaded for pure seeders.
        Generated once per torrent (not UL x fixed ratio).
        Small plausible increment added each announce.
        """
        if info_hash not in self._sim_dl_cache:
            base = int(reported * random.uniform(0.6, 1.2))
            self._sim_dl_cache[info_hash] = apply_noise(base, 5.0)
        increment = int(random.gauss(reported * 0.05, reported * 0.02))
        self._sim_dl_cache[info_hash] += max(0, increment)
        return apply_noise(self._sim_dl_cache[info_hash], 3.0)

    def get_event_anomaly(self, info_hash, ann_cnt, current_event):
        """
        v1.4 #8 -- Spontaneous event=stopped/started anomaly (~3% per session).
        Simulates a client reconnection (network hiccup, sleep/wake cycle).
        Only triggers on regular announces (not on real stopped/started).
        Returns the event string to inject, or "" to keep original.
        """
        if current_event in ("stopped", "started", "completed"):
            return ""  # never override real events

        target = self._event_anomaly.get(info_hash)
        if target is None:
            # Schedule the next anomaly: 3% chance, roughly every 30-60 announces
            if random.random() < 0.03:
                target = ann_cnt + random.randint(1, 3)
                self._event_anomaly[info_hash] = target
            return ""

        if ann_cnt >= target:
            del self._event_anomaly[info_hash]
            logger.debug("[EVENT_ANOMALY] %s -- injecting stopped/started",
                         normalize_info_hash(info_hash)[:8])
            return "stopped"  # next announce will be a normal one (simulates reconnect)

        return ""

    # -- UA / peer_id / headers --------------------------------------------------

    def get_ua(self, original):
        if not self._spoof_ua or self._ua_mode == "passthrough":
            return original
        if self._ua_mode == "fixed":
            return self._ua_value or random.choice(QBIT_USER_AGENTS)
        if original and any(p in original.lower() for p in KNOWN_CLIENT_PREFIX):
            return original
        return random.choice(QBIT_USER_AGENTS)

    def get_peer_id(self, ua):
        """
        v1.4 #4 -- peer_id rotated every 4-6h.
        A stable peer_id with a varying UA is a detection signal.
        """
        if not self._spoof_pid:
            return ""
        now    = time.time()
        cached = self._peer_id_cache.get(ua)
        ttl    = random.uniform(4 * 3600, 6 * 3600)
        if cached is None or (now - cached[1]) > ttl:
            pid  = generate_peer_id(ua)
            self._peer_id_cache[ua] = (pid, now)
            return pid
        return cached[0]

    def get_headers(self, ua):
        """
        v1.4 #6 -- UA-specific header set.
        Different Qt versions and clients have different header combinations.
        """
        return QBIT_HEADERS.get(ua, {"Accept": "*/*", "Accept-Encoding": "gzip"})

    @property
    def peer_var(self):   return self._peer_var if self._spoof_peers else 0.0
    @property
    def spoof_port(self): return self._spoof_port
    @property
    def port_range(self): return self._port_range
    @property
    def spoof_hdrs(self): return self._spoof_hdrs
    @property
    def noise_pct(self):  return self._noise_pct


# -- Module-level singletons ----------------------------------------------------

_cfg_path = str(_BASE_DIR / "config.ini")
_randomize_ratio_if_default(_cfg_path)   # v1.4 #1 anti-clustering
_cfg    = load_config(_cfg_path)
validate_config(_cfg)
_stats  = StatsManager(_cfg)
_filter = TrackerFilter(_cfg)
_guard  = IntervalGuard(_cfg.getint("advanced", "min_announce_interval", fallback=1800))


# -- mitmproxy addon ------------------------------------------------------------

class NewGreedyAddon:

    def request(self, flow: http.HTTPFlow):
        """
        Intercepts HTTP/HTTPS /announce requests and rewrites upload stats.
        UDP tracker announces bypass mitmproxy entirely (pass-through).
        """
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
            params = dict(urllib.parse.parse_qsl(
                raw_q, keep_blank_values=True, encoding="latin-1"
            ))
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

        current_event = params.get("event", "")

        # v1.4 #7 -- Random delay for duplicate multi-tracker announces.
        # The dedup window is 10s; duplicates get a 0.5-8s sleep so that
        # each tracker receives the announce at a slightly different timestamp,
        # preventing cross-tracker timing correlation.
        now = time.time()
        prev_acc = _stats._last_accumulate.get(ih, 0)
        if (now - prev_acc) < 10.0:
            delay = random.uniform(0.5, 8.0)
            logger.debug("[DELAY] %s +%.1fs (multi-tracker desync)",
                         normalize_info_hash(ih)[:8], delay)
            time.sleep(delay)

        rep_ul, rep_dl = _stats.compute(ih, real_ul, real_dl, left)
        ua      = _stats.get_ua(flow.request.headers.get("User-Agent", ""))
        peer_id = _stats.get_peer_id(ua)
        inj_dl  = rep_dl if (left == 0 and real_dl == 0 and rep_dl != real_dl) else -1

        # v1.4 #8 -- Spontaneous event anomaly (stopped/started reconnect sim)
        ann_cnt    = _stats._torrents.get(ih, {}).get("announce_count", 1)
        event_inj  = _stats.get_event_anomaly(ih, ann_cnt, current_event)

        flow.request.path = rewrite_query(
            path, rep_ul,
            _stats.peer_var, _stats.spoof_port, _stats.port_range,
            peer_id, inj_dl, event_inj,
        )

        if _stats.spoof_hdrs:
            for h in list(flow.request.headers.keys()):
                if h.lower() in HEADERS_REMOVE:
                    del flow.request.headers[h]
            # v1.4 #6 -- UA-specific headers
            flow.request.headers["User-Agent"] = ua
            for hk, hv in _stats.get_headers(ua).items():
                flow.request.headers[hk] = hv


addons = [NewGreedyAddon()]
