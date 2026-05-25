import random, math, time, json, re, logging, struct, configparser, asyncio
from urllib.parse import urlparse, parse_qs, unquote_to_bytes
from mitmproxy import http
import pathlib

_BASE = pathlib.Path(__file__).parent.resolve()
LOG_FILE  = str(_BASE / "newgreedy.log")
STATS_FILE= str(_BASE / "stats.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
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
    b"-qB4680-": "qBittorrent/4.6.8",  b"-qB4700-": "qBittorrent/4.7.0",
    b"-TR3000-": "Transmission/3.00",   b"-TR3100-": "Transmission/3.10",
    b"-DE13E0-": "Deluge/1.3.14",       b"-DE14F0-": "Deluge/1.4.15",
    b"-lt20F0-": "libtorrent/2.0.15",   b"-lt2110-": "libtorrent/2.1.1",
}
UA_ACCEPT = {
    "qBittorrent": "text/plain, application/x-bittorrent, */*",
    "Transmission": "text/plain, application/x-bittorrent",
    "Deluge": "*/*",
    "libtorrent": "text/plain, application/x-bittorrent",
}
_UNRESERVED = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")

def _rand_peer_id():
    prefix = random.choice(PEER_ID_PREFIXES)
    return prefix + bytes([random.randint(48, 122) for _ in range(20 - len(prefix))])

def _ua_for_prefix(p):
    return UA_MAP.get(p, "qBittorrent/4.6.8")

def _accept_for_ua(ua):
    for k, v in UA_ACCEPT.items():
        if k in ua: return v
    return "*/*"

def _tracker_domain(url):
    try: return urlparse(url).netloc.split(":")[0]
    except: return ""

def _load_cfg():
    c = configparser.ConfigParser()
    c.read(str(_BASE / "config.ini"))
    return c

def _extract_infohash(raw_query: bytes) -> str:
    for part in raw_query.split(b"&"):
        if part.startswith(b"info_hash="):
            raw = part[len(b"info_hash="):]
            try: return unquote_to_bytes(raw).hex()
            except: pass
    return ""

def _patch_query_bytes(raw_query: bytes, patches: dict) -> bytes:
    parts   = raw_query.split(b"&")
    result  = []
    patched = set()
    for part in parts:
        if b"=" not in part:
            result.append(part); continue
        k, _ = part.split(b"=", 1)
        key   = k.decode("utf-8", errors="replace")
        if key in patches:
            result.append(k + b"=" + str(patches[key]).encode())
            patched.add(key)
        else:
            result.append(part)
    for key, val in patches.items():
        if key not in patched:
            result.append(key.encode() + b"=" + str(val).encode())
    return b"&".join(result)


class TorrentStats:
    def __init__(self, cfg):
        self._stagnation_p  = cfg.getfloat("spoofing",  "stagnation_probability",     fallback=0.08)
        self._catch_up      = cfg.getfloat("spoofing",  "catch_up_factor",             fallback=0.22)
        self._max_speed     = cfg.getfloat("spoofing",  "max_simulated_speed_mbps",    fallback=10.0) * 1e6
        self._noise_pct     = cfg.getfloat("spoofing",  "upload_noise_pct",            fallback=3.0) / 100
        self._max_ratio_t   = cfg.getfloat("spoofing",  "max_ratio_per_torrent",       fallback=3.0)
        self._seed_credit   = cfg.getfloat("spoofing",  "seed_credit_mb",              fallback=5.0) * 1e6
        self._stall_thr     = cfg.getint  ("advanced",  "stall_announce_threshold",    fallback=8)
        self._min_ann_stag  = cfg.getint  ("advanced",  "min_announces_before_stagnation", fallback=3)
        self._target_buf    = cfg.getfloat("spoofing",  "target_ratio_buffer",         fallback=0.03)
        self._corrupt_p     = cfg.getfloat("advanced",  "corrupt_field_probability",   fallback=0.20)
        self._smooth_alpha  = cfg.getfloat("anti_detection", "dul_smooth_alpha",        fallback=0.4)
        base_target         = cfg.getfloat("spoofing",  "target_ratio",               fallback=1.5)
        self._target_ratio  = base_target + self._target_buf

        self._cumul_rep_ul  = 0.0
        self._cumul_rep_dl  = 0.0
        self._cumul_real_ul = 0.0
        self._ann_count     = 0
        self._zero_dl_count = 0
        self._is_stalled_net  = False
        self._is_stalled_algo = False
        self._prev_rep_ul   = 0.0
        self._stag_count    = 0
        self._dul_carry     = 0.0
        self._last_ann_ts   = 0.0
        self._ended         = False
        self._end_ts        = 0.0
        self._conv_done     = False
        self._prev_ratios   = []

    def _smart_stagnation(self, ann, rp):
        if ann < self._min_ann_stag or rp < 0.30: return False
        p = self._stagnation_p * (1.5 if rp > 0.85 else 1.1 if rp > 0.60 else 1.0)
        return random.random() < p

    def _smooth_dul(self, raw_dul: float) -> float:
        inject = raw_dul * self._smooth_alpha + self._dul_carry * self._smooth_alpha
        self._dul_carry = raw_dul * (1 - self._smooth_alpha)
        return max(0.0, inject)

    def _calc_upload(self, real_ul, cum_dl, interval, ann):
        if cum_dl <= 0:
            inc = real_ul * random.uniform(1.2, 1.6) if real_ul > 0 else self._seed_credit * random.uniform(0.8, 1.2)
            return self._cumul_rep_ul + min(inc, self._max_speed * interval), False
        target_ul = cum_dl * self._target_ratio
        rp = self._cumul_rep_ul / target_ul if target_ul > 0 else 1.0
        if self._smart_stagnation(ann, rp): return self._cumul_rep_ul, True
        remaining = target_ul - self._cumul_rep_ul
        if remaining <= 0:
            return self._cumul_rep_ul + (real_ul * random.uniform(0.9, 1.1) if real_ul > 0 else 0), False
        decay = math.exp(-0.08 * max(ann - 1, 0))
        raw_inc = min(remaining * self._catch_up * (1 + 0.5 * decay), self._max_speed * interval)
        raw_inc = max(0, raw_inc + raw_inc * random.gauss(0, self._noise_pct))
        smoothed = self._smooth_dul(raw_inc)
        return max(self._cumul_rep_ul, min(self._cumul_rep_ul + smoothed, cum_dl * self._max_ratio_t)), False

    def compute(self, real_ul, real_dl, interval, event=None):
        self._ann_count     += 1
        self._cumul_real_ul += real_ul
        self._last_ann_ts    = time.time()

        if real_dl > 0 and self._cumul_rep_dl > real_dl * 1.5:
            self._cumul_rep_dl = real_dl
        else:
            self._cumul_rep_dl = max(self._cumul_rep_dl, real_dl)

        if real_dl == 0 and event not in ("started", "stopped"):
            self._zero_dl_count += 1
            if self._zero_dl_count >= self._stall_thr:
                self._is_stalled_net  = True
                self._is_stalled_algo = (real_ul == 0)
        else:
            self._zero_dl_count   = 0
            self._is_stalled_net  = False
            self._is_stalled_algo = False

        new_ul, is_stag = self._calc_upload(real_ul, self._cumul_rep_dl, interval, self._ann_count)

        if new_ul < self._prev_rep_ul:
            new_ul = self._prev_rep_ul

        if is_stag:
            self._stag_count += 1
        else:
            self._stag_count = 0

        delta = new_ul - self._cumul_rep_ul
        self._cumul_rep_ul = self._prev_rep_ul = new_ul

        if self._cumul_rep_dl > 0:
            ratio = new_ul / self._cumul_rep_dl
            self._prev_ratios.append(ratio)
            if len(self._prev_ratios) > 5: self._prev_ratios.pop(0)
            if len(self._prev_ratios) >= 3:
                deltas = [abs(self._prev_ratios[i] - self._prev_ratios[i-1]) for i in range(1, len(self._prev_ratios))]
                if all(d < 0.005 for d in deltas) and ratio >= self._target_ratio * 0.97:
                    self._conv_done = True

        corrupt = random.randint(0, 65535) if self._corrupt_p > 0 and random.random() < self._corrupt_p else None
        return new_ul, self._cumul_rep_dl, delta, is_stag, corrupt

    def to_dict(self):
        return {
            "cumul_rep_ul":  self._cumul_rep_ul,
            "cumul_rep_dl":  self._cumul_rep_dl,
            "cumul_real_ul": self._cumul_real_ul,
            "ann_count":     self._ann_count,
            "zero_dl_count": self._zero_dl_count,
            "is_stalled_net":  self._is_stalled_net,
            "is_stalled_algo": self._is_stalled_algo,
            "stag_count":    self._stag_count,
            "dul_carry":     self._dul_carry,
            "prev_rep_ul":   self._prev_rep_ul,
            "ended":         self._ended,
            "end_ts":        self._end_ts,
            "conv_done":     self._conv_done,
            "last_ann_ts":   self._last_ann_ts,
            "target_ratio":  self._target_ratio,
        }

    def from_dict(self, d):
        self._cumul_rep_ul   = d.get("cumul_rep_ul",   0.0)
        self._cumul_rep_dl   = d.get("cumul_rep_dl",   0.0)
        self._cumul_real_ul  = d.get("cumul_real_ul",  0.0)
        self._ann_count      = d.get("ann_count",      0)
        self._zero_dl_count  = d.get("zero_dl_count",  0)
        self._is_stalled_net  = d.get("is_stalled_net",  False)
        self._is_stalled_algo = d.get("is_stalled_algo", False)
        self._stag_count     = d.get("stag_count",     0)
        self._dul_carry      = d.get("dul_carry",      0.0)
        self._prev_rep_ul    = d.get("prev_rep_ul",    self._cumul_rep_ul)
        self._ended          = d.get("ended",          False)
        self._end_ts         = d.get("end_ts",         0.0)
        self._conv_done      = d.get("conv_done",      False)
        self._last_ann_ts    = d.get("last_ann_ts",    0.0)


class NewGreedyAddon:
    def __init__(self):
        self._cfg            = _load_cfg()
        self._stats: dict[str, TorrentStats] = {}
        self._tracker_cumul  = {}
        self._peer_ids       = {}
        self._ports          = {}
        self._uas            = {}
        self._last_seen      = {}
        self._startup_ts     = time.time()
        self._last_save_ts   = time.time()
        self._connect_errors = 0
        self._tracker_errors: dict[str, dict] = {}

        self._persist       = self._cfg.getboolean("stats",          "persist_stats",              fallback=True)
        self._spoof_ua      = self._cfg.getboolean("anti_detection",  "spoof_user_agent",           fallback=True)
        self._spoof_pid     = self._cfg.getboolean("anti_detection",  "spoof_peer_id",              fallback=True)
        self._spoof_port    = self._cfg.getboolean("anti_detection",  "spoof_port",                 fallback=True)
        self._spoof_peers   = self._cfg.getboolean("anti_detection",  "spoof_peers",                fallback=True)
        self._spoof_hdr     = self._cfg.getboolean("anti_detection",  "spoof_headers",              fallback=True)
        self._max_global_r  = self._cfg.getfloat  ("spoofing",        "max_global_ratio_per_tracker",fallback=2.5)
        self._min_interval  = self._cfg.getint    ("advanced",        "min_announce_interval",      fallback=1800)
        self._jitter_pct    = self._cfg.getfloat  ("advanced",        "interval_jitter_pct",        fallback=0.15)
        self._save_interval = self._cfg.getint    ("advanced",        "stats_save_interval",        fallback=300)
        self._dead_thr      = self._cfg.getint    ("advanced",        "dead_tracker_threshold",     fallback=3)
        self._dead_ttl      = self._cfg.getint    ("advanced",        "dead_tracker_ttl_hours",     fallback=12)
        self._stag_warn_thr = self._cfg.getint    ("advanced",        "stag_warn_threshold",        fallback=3)
        self._end_ttl       = self._cfg.getint    ("advanced",        "end_ttl_minutes",            fallback=60)
        self._purge_ttl     = self._cfg.getint    ("advanced",        "purge_ttl_minutes",          fallback=120)
        self._grace_period  = self._cfg.getint    ("advanced",        "startup_grace_seconds",      fallback=15)

        port_range = self._cfg.get("anti_detection", "port_range", fallback="6881-6999").split("-")
        self._port_lo = int(port_range[0]); self._port_hi = int(port_range[-1])

        self._event_anom_p = self._cfg.getfloat("advanced", "event_anomaly_probability", fallback=0.03)
        self._intercept_scrape = self._cfg.getboolean("anti_detection", "intercept_scrape", fallback=True)
        whitelist_raw = self._cfg.get("anti_detection", "tracker_whitelist", fallback="")
        blacklist_raw = self._cfg.get("anti_detection", "tracker_blacklist", fallback="")
        self._whitelist = [x.strip() for x in whitelist_raw.split(",") if x.strip()]
        self._blacklist = [x.strip() for x in blacklist_raw.split(",") if x.strip()]

        if self._persist: self._load_stats()
        logger.info("NewGreedy v1.6.0 started — proxy listening on port %s",
                    self._cfg.get("proxy", "listen_port", fallback="3456"))

    def _get_stats(self, ih: str) -> TorrentStats:
        if ih not in self._stats:
            self._stats[ih] = TorrentStats(self._cfg)
        return self._stats[ih]

    def _parse_int(self, qs, key, default=0):
        try: return int(qs.get(key, [default])[0])
        except: return default

    def _save_stats(self):
        data = {}
        for ih, st in self._stats.items():
            data[ih] = st.to_dict()
        try:
            with open(STATS_FILE, "w") as f:
                json.dump(data, f)
            self._last_save_ts = time.time()
        except Exception as e:
            logger.warning("Stats save failed: %s", e)

    def _load_stats(self):
        try:
            with open(STATS_FILE) as f:
                data = json.load(f)
            for ih, d in data.items():
                if not re.match(r"^[0-9a-f]{8,40}$", ih): continue
                st = TorrentStats(self._cfg)
                st.from_dict(d)
                self._stats[ih] = st
            logger.info("Stats loaded — %d torrents restored", len(self._stats))
        except Exception:
            pass

    def _check_periodic_save(self):
        if time.time() - self._last_save_ts >= self._save_interval:
            self._save_stats()
            logger.debug("Periodic stats save OK")

    def _tracker_error(self, domain: str, status: int):
        e = self._tracker_errors.setdefault(domain, {"count": 0, "dead": False, "dead_since": 0.0})
        if e["dead"]:
            if time.time() - e["dead_since"] >= self._dead_ttl * 3600:
                e["dead"] = False; e["count"] = 0
                logger.info("[TRACKER_REVIVED] %s back after %dh blacklist", domain, self._dead_ttl)
            return
        e["count"] += 1
        if e["count"] >= self._dead_thr:
            e["dead"] = True; e["dead_since"] = time.time()
            logger.warning("[DEAD_TRACKER] %s — %d consecutive errors (HTTP %d) — blacklisted for %dh",
                           domain, e["count"], status, self._dead_ttl)

    def _is_dead_tracker(self, domain: str) -> bool:
        e = self._tracker_errors.get(domain)
        if not e or not e["dead"]: return False
        if time.time() - e["dead_since"] >= self._dead_ttl * 3600:
            e["dead"] = False; e["count"] = 0
            return False
        return True

    def _tracker_ok(self, domain: str):
        if domain in self._tracker_errors:
            self._tracker_errors[domain]["count"] = 0

    def _lifecycle_check(self):
        now = time.time()
        to_purge = []
        for ih, st in self._stats.items():
            if st._last_ann_ts == 0: continue
            elapsed = (now - st._last_ann_ts) / 60
            if not st._ended and elapsed >= self._end_ttl:
                st._ended = True; st._end_ts = now
                logger.info("[END] %s — no announce for %d min", ih[:8], int(elapsed))
            if st._ended and (now - st._end_ts) / 60 >= self._purge_ttl:
                to_purge.append(ih)
        for ih in to_purge:
            del self._stats[ih]
            logger.info("[PURGE] %s removed from stats", ih[:8])

    def request(self, flow: http.HTTPFlow):
        now = time.time()

        if now - self._startup_ts < self._grace_period:
            return

        url = flow.request.pretty_url
        if "/announce" not in url and not (self._intercept_scrape and "/scrape" in url):
            return
        if "/scrape" in url and self._intercept_scrape:
            return

        domain = _tracker_domain(url)
        if self._whitelist and domain not in self._whitelist: return
        if domain in self._blacklist: return
        if self._is_dead_tracker(domain): return

        raw_path  = flow.request.data.path
        q_start   = raw_path.find(b"?")
        raw_query = raw_path[q_start + 1:] if q_start != -1 else b""
        if not raw_query: return

        ih_key = _extract_infohash(raw_query)
        if not ih_key: return

        qs = parse_qs(raw_query.decode("utf-8", errors="replace"), keep_blank_values=True)

        last_key = ih_key + domain
        now_t    = time.time()
        elapsed  = now_t - self._last_seen.get(last_key, 0)
        base_int = max(elapsed, self._min_interval) if elapsed > 0 else self._min_interval
        jitter   = base_int * self._jitter_pct * random.uniform(-1, 1)
        interval = max(self._min_interval * 0.8, base_int + jitter)
        self._last_seen[last_key] = now_t

        event   = qs.get("event", [""])[0]
        real_ul = self._parse_int(qs, "uploaded")
        real_dl = self._parse_int(qs, "downloaded")
        left    = self._parse_int(qs, "left")

        st = self._get_stats(ih_key)
        st._ended = False

        new_ul, new_dl, delta_ul, is_stag, corrupt_val = st.compute(real_ul, real_dl, interval, event)

        tc = self._tracker_cumul.setdefault(domain, {"ul": 0.0, "dl": 0.0})
        tc["ul"] += delta_ul; tc["dl"] += real_dl
        if tc["dl"] > 0 and tc["ul"] / tc["dl"] > self._max_global_r:
            new_ul = max(st._prev_rep_ul, new_ul - (tc["ul"] - self._max_global_r * tc["dl"]))

        cycle_ul = int(new_ul)

        is_pure_seeder = (left == 0 and real_dl == 0)
        patches = {"uploaded": cycle_ul}
        if not is_pure_seeder:
            patches["downloaded"] = int(new_dl + struct.pack(">I", random.randint(1, 4096))[0])

        if self._spoof_port:
            if ih_key not in self._ports:
                self._ports[ih_key] = random.randint(self._port_lo, self._port_hi)
            patches["port"] = self._ports[ih_key]

        if self._spoof_peers:
            nw = self._parse_int(qs, "numwant", 50)
            patches["numwant"] = int(nw * random.uniform(0.85, 1.15))

        if corrupt_val is not None:
            patches["corrupt"] = corrupt_val

        if random.random() < self._event_anom_p and event not in ("started", "stopped"):
            patches["event"] = "started"

        if self._spoof_pid:
            if ih_key not in self._peer_ids:
                pid = _rand_peer_id()
                self._peer_ids[ih_key] = pid
                self._uas[ih_key] = _ua_for_prefix(pid[:8])
            pid_pct = b"peer_id=" + b"".join(
                bytes([b]) if b in _UNRESERVED else (b"%" + format(b, "02X").encode())
                for b in self._peer_ids[ih_key]
            )
            parts     = raw_query.split(b"&")
            parts     = [p for p in parts if not p.startswith(b"peer_id=")]
            parts.append(pid_pct)
            raw_query = b"&".join(parts)

        new_raw_query = _patch_query_bytes(raw_query, patches)
        path_only     = raw_path[:q_start] if q_start != -1 else raw_path
        flow.request.data.path = path_only + b"?" + new_raw_query

        if self._spoof_hdr:
            ua = (self._uas.get(ih_key, "qBittorrent/4.6.8") if self._spoof_ua
                  else self._cfg.get("anti_detection", "user_agent_value", fallback="qBittorrent/4.6.8"))
            flow.request.headers["User-Agent"]      = ua
            flow.request.headers["Accept"]          = _accept_for_ua(ua)
            flow.request.headers["Accept-Language"] = "en-US,en;q=0.9"
            flow.request.headers["Connection"]      = "keep-alive"

        mode      = "SEEDING    " if left == 0 else "DOWNLOADING"
        cum_dl    = st._cumul_rep_dl / 1e6
        cum_ul    = new_ul / 1e6
        delta     = delta_ul / 1e6
        ratio     = new_ul / st._cumul_rep_dl if st._cumul_rep_dl > 0 else 0.0
        avg_d     = new_ul / st._ann_count if st._ann_count > 1 else delta_ul
        eta       = int(max(0, st._cumul_rep_dl * st._target_ratio - new_ul) / avg_d) if avg_d > 0 else 0
        stag_t    = " [STAG]"       if is_stag            else ""
        stall_n   = " [STALL_NET]"  if st._is_stalled_net  else ""
        stall_a   = " [STALL_ALGO]" if st._is_stalled_algo else ""
        conv_t    = " [CONV_DONE]"  if st._conv_done        else ""

        if st._stag_count >= self._stag_warn_thr:
            logger.warning("[STAG_PROLONGED] %s — %d consecutive stagnations", ih_key[:8], st._stag_count)

        if st._cumul_rep_dl > 0:
            logger.info("[%s] %s | DL:%8.2fMB | RealUL:%8.2fMB | CumUL:%8.2fMB | +DUL:%7.2fMB | SentUL:%8.2fMB | Ratio:%.3f ETA:~%dann | Ann#%d%s%s%s%s",
                mode, ih_key[:8], cum_dl, real_ul/1e6, cum_ul, delta, cum_ul, ratio, eta, st._ann_count,
                stag_t, stall_n, stall_a, conv_t)
        else:
            logger.info("[%s] %s | DL:%8.2fMB | RealUL:%8.2fMB | CumUL:%8.2fMB | +DUL:%7.2fMB | SeedUL:%8.2fMB | Ann#%d%s%s%s",
                mode, ih_key[:8], cum_dl, real_ul/1e6, cum_ul, delta, cum_ul, st._ann_count,
                stag_t, stall_n, stall_a)

        if real_ul > 0 and not getattr(st, "_realul_started", False):
            st._realul_started = True
            logger.info("[SEED_ACTIVE] %s — real upload started (%.2f MB)", ih_key[:8], real_ul/1e6)

        self._lifecycle_check()
        self._check_periodic_save()

    def response(self, flow: http.HTTPFlow):
        url    = flow.request.pretty_url
        domain = _tracker_domain(url)
        if "/announce" not in url: return
        status = flow.response.status_code if flow.response else 0
        if status and status >= 400:
            self._tracker_error(domain, status)
            logger.warning("[TRACKER_ERROR] %s — HTTP %d", domain, status)
        elif status == 200:
            self._tracker_ok(domain)

    def error(self, flow: http.HTTPFlow):
        self._connect_errors += 1
        url = flow.request.pretty_url if flow.request else "unknown"
        domain = _tracker_domain(url)
        logger.warning("[CONNECT_ERROR] %s — total errors: %d", domain, self._connect_errors)

    def done(self):
        if self._persist: self._save_stats()
        logger.info("NewGreedy v1.6.0 stopping — stats saved.")


addons = [NewGreedyAddon()]
