import random, math, time, json, re, logging, hashlib, struct
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from mitmproxy import http

LOG_FILE = "newgreedy.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ]
)
logger = logging.getLogger("NewGreedy")

PEER_ID_PREFIXES = [
    b"-qB4680-", b"-qB4700-", b"-TR3000-", b"-TR3100-",
    b"-DE13E0-", b"-DE14F0-", b"-lt20F0-", b"-lt2110-",
]
UA_MAP = {
    b"-qB4680-": "qBittorrent/4.6.8",
    b"-qB4700-": "qBittorrent/4.7.0",
    b"-TR3000-": "Transmission/3.00",
    b"-TR3100-": "Transmission/3.10",
    b"-DE13E0-": "Deluge/1.3.14",
    b"-DE14F0-": "Deluge/1.4.15",
    b"-lt20F0-": "libtorrent/2.0.15",
    b"-lt2110-": "libtorrent/2.1.1",
}
UA_ACCEPT = {
    "qBittorrent": "text/plain, application/x-bittorrent, */*",
    "Transmission": "text/plain, application/x-bittorrent",
    "Deluge":       "*/*",
    "libtorrent":   "text/plain, application/x-bittorrent",
}

def _rand_peer_id():
    prefix = random.choice(PEER_ID_PREFIXES)
    suffix = bytes([random.randint(48, 122) for _ in range(20 - len(prefix))])
    return prefix + suffix

def _ua_for_prefix(prefix):
    return UA_MAP.get(prefix, "qBittorrent/4.6.8")

def _accept_for_ua(ua):
    for k, v in UA_ACCEPT.items():
        if k in ua:
            return v
    return "*/*"

def _tracker_domain(url):
    try:
        return urlparse(url).netloc.split(":")[0]
    except Exception:
        return ""

class TorrentStats:
    def __init__(self, cfg):
        self._cfg = cfg
        self._stagnation_p = cfg.getfloat("spoofing", "stagnation_probability", fallback=0.08)
        self._catch_up     = cfg.getfloat("spoofing", "catch_up_factor",        fallback=0.22)
        self._max_speed    = cfg.getfloat("spoofing", "max_simulated_speed_mbps", fallback=10.0) * 1e6
        self._noise_pct    = cfg.getfloat("spoofing", "upload_noise_pct",        fallback=3.0) / 100
        self._max_ratio_t  = cfg.getfloat("spoofing", "max_ratio_per_torrent",   fallback=3.0)
        self._seed_credit  = cfg.getfloat("spoofing", "seed_credit_mb",          fallback=5.0) * 1e6
        self._seed_dl_r    = cfg.getfloat("spoofing", "seeding_dl_ratio",        fallback=0.85)
        self._stall_thr    = cfg.getint  ("advanced", "stall_announce_threshold", fallback=8)
        self._min_ann_stag = cfg.getint  ("advanced", "min_announces_before_stagnation", fallback=3)
        self._target_buf   = cfg.getfloat("spoofing", "target_ratio_buffer",     fallback=0.03)
        self._corrupt_p    = cfg.getfloat("advanced", "corrupt_field_probability", fallback=0.20)
        self._jitter_pct   = cfg.getfloat("advanced", "interval_jitter_pct",    fallback=0.08)

        base_target = cfg.getfloat("spoofing", "target_ratio", fallback=1.5)
        self._target_ratio = base_target + self._target_buf

        self._cumul_rep_ul  = 0.0
        self._cumul_rep_dl  = 0.0
        self._cumul_real_ul = 0.0
        self._ann_count     = 0
        self._zero_dl_count = 0
        self._is_stalled    = False
        self._peer_id       = _rand_peer_id()
        self._ua            = _ua_for_prefix(self._peer_id[:8])
        self._prev_rep_ul   = 0.0

    def _smart_stagnation(self, ann_count, ratio_progress):
        if ann_count < self._min_ann_stag:
            return False
        if ratio_progress < 0.30:
            return False
        base_p = self._stagnation_p
        if ratio_progress > 0.85:
            base_p *= 1.5
        elif ratio_progress > 0.60:
            base_p *= 1.1
        return random.random() < base_p

    def _calc_upload(self, real_ul, cumul_dl, interval, ann_count):
        if cumul_dl <= 0:
            if real_ul > 0:
                bonus = real_ul * random.uniform(1.2, 1.6)
                bonus = min(bonus, self._max_speed * interval)
                return self._cumul_rep_ul + bonus, False
            seed_inc = min(self._seed_credit * random.uniform(0.8, 1.2),
                           self._max_speed * interval)
            return self._cumul_rep_ul + seed_inc, False

        target_ul = cumul_dl * self._target_ratio
        ratio_progress = self._cumul_rep_ul / target_ul if target_ul > 0 else 1.0

        if self._smart_stagnation(ann_count, ratio_progress):
            return self._cumul_rep_ul, True

        remaining = target_ul - self._cumul_rep_ul
        if remaining <= 0:
            plateau_inc = real_ul * random.uniform(0.9, 1.1) if real_ul > 0 else 0
            return self._cumul_rep_ul + plateau_inc, False

        decay = math.exp(-0.08 * max(ann_count - 1, 0))
        effective_catch_up = self._catch_up * (1 + 0.5 * decay)
        inc = remaining * effective_catch_up
        inc = min(inc, self._max_speed * interval)
        noise = inc * random.gauss(0, self._noise_pct)
        inc = max(0, inc + noise)

        target_max = cumul_dl * self._max_ratio_t
        new_cumul = min(self._cumul_rep_ul + inc, target_max)
        new_cumul = max(new_cumul, self._cumul_rep_ul)
        return new_cumul, False

    def compute(self, real_ul_bytes, real_dl_bytes, interval, event=None):
        self._ann_count    += 1
        self._cumul_real_ul += real_ul_bytes
        self._cumul_rep_dl  += real_dl_bytes

        if real_dl_bytes == 0 and event not in ("started", "stopped"):
            self._zero_dl_count += 1
            if self._zero_dl_count >= self._stall_thr:
                self._is_stalled = True
        else:
            self._zero_dl_count = 0
            self._is_stalled = False

        new_rep_ul, is_stag = self._calc_upload(
            real_ul_bytes, self._cumul_rep_dl, interval, self._ann_count
        )

        if new_rep_ul < self._prev_rep_ul:
            new_rep_ul = self._prev_rep_ul

        delta_ul = new_rep_ul - self._cumul_rep_ul
        self._cumul_rep_ul = new_rep_ul
        self._prev_rep_ul  = new_rep_ul

        corrupt_val = None
        if self._corrupt_p > 0 and random.random() < self._corrupt_p:
            corrupt_val = random.randint(0, 65535)

        return new_rep_ul, self._cumul_rep_dl, delta_ul, is_stag, corrupt_val

class NewGreedyAddon:
    def __init__(self, cfg):
        self._cfg        = cfg
        self._stats      = {}
        self._tracker_cumul = {}
        self._max_global_r = cfg.getfloat("spoofing", "max_global_ratio_per_tracker", fallback=2.5)
        self._min_interval = cfg.getint("advanced", "min_announce_interval",  fallback=1800)
        self._wl = [x.strip() for x in cfg.get("anti_detection", "tracker_whitelist", fallback="").split(",") if x.strip()]
        self._bl = [x.strip() for x in cfg.get("anti_detection", "tracker_blacklist", fallback="").split(",") if x.strip()]
        self._spoof_ua    = cfg.getboolean("anti_detection", "spoof_user_agent", fallback=True)
        self._spoof_pid   = cfg.getboolean("anti_detection", "spoof_peer_id",    fallback=True)
        self._spoof_peers = cfg.getboolean("anti_detection", "spoof_peers",      fallback=True)
        self._spoof_port  = cfg.getboolean("anti_detection", "spoof_port",       fallback=True)
        self._spoof_hdr   = cfg.getboolean("anti_detection", "spoof_headers",    fallback=True)
        self._intercept_scrape = cfg.getboolean("anti_detection", "intercept_scrape", fallback=True)
        port_range = cfg.get("anti_detection", "port_range", fallback="6881-6999")
        lo, hi = port_range.split("-")
        self._port_lo, self._port_hi = int(lo), int(hi)
        self._event_anomaly_p = cfg.getfloat("advanced", "event_anomaly_probability", fallback=0.03)
        self._jitter_pct = cfg.getfloat("advanced", "interval_jitter_pct", fallback=0.08)
        self._last_seen  = {}
        self._ports      = {}
        self._peer_ids   = {}
        self._uas        = {}
        self._persist    = cfg.getboolean("stats", "persist_stats", fallback=True)
        self._stats_file = cfg.get("stats", "stats_file", fallback="stats.json")
        if self._persist:
            self._load_stats()

    def _load_stats(self):
        VALID = re.compile(r"^[0-9a-f]{8,40}$")
        try:
            with open(self._stats_file) as f:
                raw = json.load(f)
            loaded = 0
            for k, d in raw.items():
                if not VALID.match(k):
                    continue
                if not isinstance(d, dict) or "cumul_rep_ul" not in d:
                    continue
                s = TorrentStats(self._cfg)
                s._cumul_rep_ul  = d.get("cumul_rep_ul",  0.0)
                s._cumul_rep_dl  = d.get("cumul_rep_dl",  0.0)
                s._cumul_real_ul = d.get("cumul_real_ul", 0.0)
                s._ann_count     = d.get("ann_count",     0)
                s._prev_rep_ul   = d.get("cumul_rep_ul",  0.0)
                s._is_stalled    = d.get("stalled",       False)
                self._stats[k]   = s
                loaded += 1
            logger.info("Stats loaded: %d torrent(s) tracked", loaded)
        except Exception as e:
            logger.warning("Stats load: %s", e)

    def _save_stats(self):
        try:
            data = {
                ih: {
                    "cumul_rep_ul":  s._cumul_rep_ul,
                    "cumul_rep_dl":  s._cumul_rep_dl,
                    "cumul_real_ul": s._cumul_real_ul,
                    "ann_count":     s._ann_count,
                    "stalled":       s._is_stalled,
                    "prev_rep_ul":   s._prev_rep_ul,
                }
                for ih, s in self._stats.items()
            }
            with open(self._stats_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning("Stats save failed: %s", e)

    def _is_announce(self, url):
        p = urlparse(url)
        return "announce" in p.path and "scrape" not in p.path

    def _tracker_allowed(self, url):
        domain = _tracker_domain(url)
        if self._bl and any(b in domain for b in self._bl):
            return False
        if self._wl:
            return any(w in domain for w in self._wl)
        return True

    def _get_stats(self, info_hash):
        if info_hash not in self._stats:
            self._stats[info_hash] = TorrentStats(self._cfg)
        return self._stats[info_hash]

    def _parse_int(self, qs, key, default=0):
        try:
            return int(qs.get(key, [default])[0])
        except (ValueError, TypeError):
            return default

    def _jitter_interval(self, interval):
        jitter = interval * self._jitter_pct
        return interval + random.uniform(-jitter, jitter)

    def request(self, flow: http.HTTPFlow):
        url = flow.request.pretty_url
        if not self._tracker_allowed(url):
            return
        if self._intercept_scrape and "scrape" in urlparse(url).path:
            return
        if not self._is_announce(url):
            return

        domain = _tracker_domain(url)
        parsed = urlparse(url)
        qs     = parse_qs(parsed.query, keep_blank_values=True)

        info_hash = qs.get("info_hash", [b""])[0]
        if isinstance(info_hash, bytes):
            ih_hex = info_hash.hex()
        else:
            ih_hex = info_hash.encode("latin-1").hex() if len(info_hash) == 20 else info_hash[:8]
        ih_key = ih_hex[:8]

        now  = time.time()
        last = self._last_seen.get(ih_key + domain, 0)
        interval = max(now - last, self._min_interval) if last > 0 else self._min_interval
        interval = self._jitter_interval(interval)
        self._last_seen[ih_key + domain] = now

        event   = qs.get("event", [""])[0]
        real_ul = self._parse_int(qs, "uploaded")
        real_dl = self._parse_int(qs, "downloaded")
        left    = self._parse_int(qs, "left")

        st = self._get_stats(ih_key)
        new_ul, new_dl, delta_ul, is_stag, corrupt_val = st.compute(
            real_ul, real_dl, interval, event
        )

        tracker_cumul = self._tracker_cumul.get(domain, {})
        tracker_ul = tracker_cumul.get("ul", 0.0) + delta_ul
        tracker_dl = tracker_cumul.get("dl", 0.0) + real_dl
        tracker_ratio = tracker_ul / tracker_dl if tracker_dl > 0 else 0
        if tracker_ratio > self._max_global_r and tracker_dl > 0:
            cap    = self._max_global_r * tracker_dl
            excess = tracker_ul - cap
            if excess > 0:
                new_ul = max(st._prev_rep_ul, new_ul - excess)
        tracker_cumul["ul"] = tracker_ul
        tracker_cumul["dl"] = tracker_dl
        self._tracker_cumul[domain] = tracker_cumul

        if self._spoof_pid:
            if ih_key not in self._peer_ids:
                pid = _rand_peer_id()
                self._peer_ids[ih_key] = pid
                self._uas[ih_key]      = _ua_for_prefix(pid[:8])
            qs["peer_id"] = [self._peer_ids[ih_key]]

        if self._spoof_port:
            if ih_key not in self._ports:
                self._ports[ih_key] = random.randint(self._port_lo, self._port_hi)
            qs["port"] = [str(self._ports[ih_key])]

        if self._spoof_peers:
            numwant = self._parse_int(qs, "numwant", 50)
            qs["numwant"] = [str(int(numwant * random.uniform(0.85, 1.15)))]

        if corrupt_val is not None:
            qs["corrupt"] = [str(corrupt_val)]

        if random.random() < self._event_anomaly_p and event not in ("started", "stopped"):
            qs["event"] = ["started"]

        res_bytes = struct.pack(">I", random.randint(1, 4096))
        qs["uploaded"]   = [str(int(new_ul))]
        qs["downloaded"] = [str(int(new_dl + res_bytes[0]))]

        new_query = urlencode({k: v[0] for k, v in qs.items()})
        flow.request.path = parsed.path + "?" + new_query

        if self._spoof_hdr:
            ua = self._uas.get(ih_key, "qBittorrent/4.6.8") if self._spoof_ua else                  self._cfg.get("anti_detection", "user_agent_value", fallback="qBittorrent/4.6.8")
            flow.request.headers["User-Agent"]      = ua
            flow.request.headers["Accept"]          = _accept_for_ua(ua)
            flow.request.headers["Accept-Language"] = "en-US,en;q=0.9"
            flow.request.headers["Connection"]      = "keep-alive"

        mode     = "SEEDING    " if left == 0 else "DOWNLOADING"
        cum_dl   = st._cumul_rep_dl / 1e6
        rul_mb   = real_ul / 1e6
        cum_ul   = new_ul / 1e6
        delta_mb = delta_ul / 1e6
        ratio    = new_ul / st._cumul_rep_dl if st._cumul_rep_dl > 0 else 0.0

        target_ul = st._cumul_rep_dl * st._target_ratio
        remaining = max(0, target_ul - new_ul)
        avg_delta = new_ul / st._ann_count if st._ann_count > 1 else delta_ul
        eta_ann   = int(remaining / avg_delta) if avg_delta > 0 else 0

        stag_tag  = " [STAG]"  if is_stag       else ""
        stall_tag = " [STALL]" if st._is_stalled else ""

        if st._cumul_rep_dl > 0:
            logger.info(
                "[%s] %s | DL:%8.2fMB | RealUL:%8.2fMB | CumUL:%8.2fMB | +DUL:%7.2fMB | SentUL:%8.2fMB | Ratio:%.3f ETA:~%dann | Ann#%d%s%s",
                mode, ih_key, cum_dl, rul_mb, cum_ul, delta_mb, cum_ul,
                ratio, eta_ann, st._ann_count, stag_tag, stall_tag
            )
        else:
            logger.info(
                "[%s] %s | DL:%8.2fMB | RealUL:%8.2fMB | CumUL:%8.2fMB | +DUL:%7.2fMB | SeedUL:%8.2fMB | Ann#%d%s%s",
                mode, ih_key, cum_dl, rul_mb, cum_ul, delta_mb, cum_ul,
                st._ann_count, stag_tag, stall_tag
            )

        if self._persist and st._ann_count % 5 == 0:
            self._save_stats()

    def done(self):
        if self._persist:
            self._save_stats()

def start(cfg):
    import configparser
    c = configparser.ConfigParser()
    c.read("config.ini")
    logger.info("-- Config validation v1.5.1 --")
    mode = c.get("spoofing", "upload_mode", fallback="ratio_based")
    logger.info("-- Config OK (mode=%s) --", mode)
    return NewGreedyAddon(c)
