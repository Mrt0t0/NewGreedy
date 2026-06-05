#!/usr/bin/env python3
"""NewGreedy v1.7.0 — core addon"""
import random, math, time, json, re, logging, struct, configparser
from datetime import datetime
from urllib.parse import urlparse, parse_qs, unquote_to_bytes
from mitmproxy import http
import pathlib

VERSION       = "v1.7.0"
SCHEMA_VER    = 3
_BASE         = pathlib.Path(__file__).parent.resolve()
LOG_FILE      = str(_BASE / "newgreedy.log")
STATS_FILE    = str(_BASE / "stats.json")
REGISTRY_FILE = str(_BASE / "torrent_registry.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("NewGreedy")

PEER_ID_PREFIXES = [
    b"-qB4680-", b"-qB4700-", b"-TR3000-", b"-TR3100-",
    b"-DE13E0-", b"-DE14F0-", b"-lt20F0-", b"-lt2110-",
]
UA_MAP = {
    b"-qB4680-": "qBittorrent/4.6.8", b"-qB4700-": "qBittorrent/4.7.0",
    b"-TR3000-": "Transmission/3.00",  b"-TR3100-": "Transmission/3.10",
    b"-DE13E0-": "Deluge/1.3.14",      b"-DE14F0-": "Deluge/1.4.15",
    b"-lt20F0-": "libtorrent/2.0.15",  b"-lt2110-": "libtorrent/2.1.1",
}
UA_ACCEPT = {
    "qBittorrent": "text/plain, application/x-bittorrent, */*",
    "Transmission": "text/plain, application/x-bittorrent",
    "Deluge": "*/*",
    "libtorrent": "text/plain, application/x-bittorrent",
}


def _rand_peer_id():
    prefix = random.choice(PEER_ID_PREFIXES)
    return prefix + bytes([random.randint(48, 122) for _ in range(20 - len(prefix))])


def _ua_for_prefix(p):
    return UA_MAP.get(p, "qBittorrent/4.6.8")


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


def _load_cfg():
    c = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    c.read(str(_BASE / "config.ini"))
    return c


def _extract_infohash(raw_query: bytes) -> str:
    for part in raw_query.split(b"&"):
        if part.startswith(b"info_hash="):
            raw = part[len(b"info_hash="):]
            try:
                return unquote_to_bytes(raw).hex()
            except Exception:
                pass
    return ""


def _patch_query_bytes(raw_query: bytes, patches: dict) -> bytes:
    parts   = raw_query.split(b"&")
    result  = []
    patched = set()
    for part in parts:
        if b"=" not in part:
            result.append(part)
            continue
        k, _ = part.split(b"=", 1)
        key  = k.decode("utf-8", errors="replace")
        if key in patches:
            result.append(k + b"=" + str(patches[key]).encode())
            patched.add(key)
        else:
            result.append(part)
    for key, val in patches.items():
        if key not in patched:
            result.append(key.encode() + b"=" + str(val).encode())
    return b"&".join(result)


def _bencode_get_int(data: bytes, key: bytes) -> int:
    try:
        marker = str(len(key)).encode() + b":" + key
        idx = data.find(marker)
        if idx == -1:
            return -1
        idx += len(marker)
        if data[idx:idx + 1] == b"i":
            end = data.index(b"e", idx + 1)
            return int(data[idx + 1:end])
    except Exception:
        pass
    return -1


def _pareto_noise(inc: float, noise_pct: float) -> float:
    u = random.random()
    if u < 0.85:
        return inc * random.gauss(1.0, noise_pct)
    burst = random.uniform(1.5, 3.0)
    return inc * burst if random.random() < 0.5 else inc / burst


def _parse_hours(spec: str):
    try:
        parts = spec.strip().split("-")
        return int(parts[0]), int(parts[1])
    except Exception:
        return 0, 23


class TorrentStats:
    def __init__(self, cfg):
        self._stagnation_p   = cfg.getfloat("spoofing", "stagnation_probability", fallback=0.03)
        self._catch_up       = cfg.getfloat("spoofing", "catch_up_factor", fallback=0.22)
        self._max_speed      = cfg.getfloat("spoofing", "max_simulated_speed_mbps", fallback=10.0) * 1e6
        self._noise_pct      = cfg.getfloat("spoofing", "upload_noise_pct", fallback=3.0) / 100
        self._max_ratio_t    = cfg.getfloat("spoofing", "max_ratio_per_torrent", fallback=3.0)
        self._seed_credit    = cfg.getfloat("spoofing", "seed_credit_mb", fallback=5.0) * 1e6
        self._seed_target_mb = cfg.getfloat("spoofing", "seed_target_mb", fallback=500.0) * 1e6
        self._stall_thr      = cfg.getint("advanced", "stall_announce_threshold", fallback=8)
        self._min_ann_stag   = cfg.getint("advanced", "min_announces_before_stagnation", fallback=10)
        self._target_buf     = cfg.getfloat("spoofing", "target_ratio_buffer", fallback=0.03)
        self._corrupt_p      = cfg.getfloat("advanced", "corrupt_field_probability", fallback=0.20)
        base_target          = cfg.getfloat("spoofing", "target_ratio", fallback=1.5)
        self._target_ratio   = base_target + self._target_buf
        self._auto_stop      = cfg.getboolean("spoofing", "auto_stop_at_target", fallback=True)
        self._active_hours   = _parse_hours(cfg.get("advanced", "inject_hours", fallback="0-23"))
        self._cumul_rep_ul   = 0.0
        self._cumul_rep_dl   = 0.0
        self._cumul_real_ul  = 0.0
        self._ann_count      = 0
        self._zero_dl_count  = 0
        self._is_net_stalled = False
        self._is_algo_stalled= False
        self._prev_rep_ul    = 0.0
        self._target_reached = False
        self._leecher_count  = -1
        self._seed_fake_dl   = 0.0
        self._history        = []
        self._mode           = "down"
        self._last_announce_ts = 0.0

    def _in_active_hours(self) -> bool:
        hour = datetime.now().hour
        lo, hi = self._active_hours
        if lo <= hi:
            return lo <= hour <= hi
        return hour >= lo or hour <= hi

    def _smart_stagnation(self, ann, rp) -> bool:
        if ann < self._min_ann_stag or rp < 0.30:
            return False
        if ann > 30 and rp < 0.65:
            return False
        p = self._stagnation_p * (1.5 if rp > 0.85 else 1.1 if rp > 0.60 else 1.0)
        if self._leecher_count == 0:
            return True
        return random.random() < p

    def _calc_upload(self, real_ul, cum_dl, interval, ann):
        if not self._in_active_hours():
            return self._cumul_rep_ul, True
        if cum_dl <= 0:
            inc = real_ul * random.uniform(1.2, 1.6) if real_ul > 0 else self._seed_credit * random.uniform(0.8, 1.2)
            return self._cumul_rep_ul + min(inc, self._max_speed * interval), False
        target_ul = cum_dl * self._target_ratio
        rp        = self._cumul_rep_ul / target_ul if target_ul > 0 else 1.0
        if self._auto_stop and rp >= 1.0:
            if not self._target_reached:
                self._target_reached = True
                logger.info("[TARGET_REACHED] ratio target %.2f reached, injection stopped", self._target_ratio)
            return self._cumul_rep_ul + (real_ul * random.uniform(0.9, 1.1) if real_ul > 0 else 0), False
        if self._smart_stagnation(ann, rp):
            return self._cumul_rep_ul, True
        remaining = target_ul - self._cumul_rep_ul
        if remaining <= 0:
            return self._cumul_rep_ul + (real_ul * random.uniform(0.9, 1.1) if real_ul > 0 else 0), False
        decay = math.exp(-0.08 * max(ann - 1, 0))
        inc   = min(remaining * self._catch_up * (1 + 0.5 * decay), self._max_speed * interval)
        inc   = max(0, _pareto_noise(inc, self._noise_pct))
        return max(self._cumul_rep_ul, min(self._cumul_rep_ul + inc, cum_dl * self._max_ratio_t)), False

    def _snapshot(self):
        self._history.append({
            "t":  int(time.time()),
            "ul": int(self._cumul_rep_ul),
            "dl": int(self._cumul_rep_dl),
        })
        if len(self._history) > 100:
            self._history = self._history[-100:]

    def compute(self, real_ul, real_dl, interval, event=None):
        self._ann_count     += 1
        self._cumul_real_ul += real_ul
        if real_dl > 0 and self._cumul_rep_dl > real_dl * 1.5:
            self._cumul_rep_dl = real_dl
        else:
            self._cumul_rep_dl = max(self._cumul_rep_dl, real_dl)
        is_pure_seeder = (real_dl == 0 and self._cumul_rep_dl == 0)
        if real_dl == 0 and event not in ("started", "stopped") and not is_pure_seeder:
            self._zero_dl_count += 1
            if self._zero_dl_count >= self._stall_thr:
                self._is_net_stalled = True
        else:
            self._zero_dl_count  = 0
            self._is_net_stalled = False
        new_ul, is_stag = self._calc_upload(real_ul, self._cumul_rep_dl, interval, self._ann_count)
        self._is_algo_stalled = is_stag
        if new_ul < self._prev_rep_ul:
            new_ul = self._prev_rep_ul
        delta              = new_ul - self._cumul_rep_ul
        self._cumul_rep_ul = self._prev_rep_ul = new_ul
        corrupt = random.randint(0, 65535) if self._corrupt_p > 0 and random.random() < self._corrupt_p else None
        if self._ann_count % 5 == 0:
            self._snapshot()
        return new_ul, self._cumul_rep_dl, delta, is_stag, corrupt


class NewGreedyAddon:
    def __init__(self):
        self._cfg            = _load_cfg()
        c                    = self._cfg
        self._stats          = {}
        self._tracker_cumul  = {}
        self._last_seen      = {}
        self._ports          = {}
        self._peer_ids       = {}
        self._uas            = {}
        self._swarm_leechers = {}
        self._max_global_r   = c.getfloat("spoofing", "max_global_ratio_per_tracker", fallback=2.5)
        self._min_interval   = c.getint("advanced", "min_announce_interval", fallback=1800)
        self._jitter_pct     = c.getfloat("advanced", "interval_jitter_pct", fallback=0.08)
        self._event_anom_p   = c.getfloat("advanced", "event_anomaly_probability", fallback=0.03)
        self._corrupt_p      = c.getfloat("advanced", "corrupt_field_probability", fallback=0.20)
        self._spoof_ua       = c.getboolean("anti_detection", "spoof_user_agent", fallback=True)
        self._spoof_pid      = c.getboolean("anti_detection", "spoof_peer_id", fallback=True)
        self._spoof_peers    = c.getboolean("anti_detection", "spoof_peers", fallback=True)
        self._spoof_port     = c.getboolean("anti_detection", "spoof_port", fallback=True)
        self._spoof_hdr      = c.getboolean("anti_detection", "spoof_headers", fallback=True)
        self._intercept_scrape = c.getboolean("anti_detection", "intercept_scrape", fallback=True)
        lo, hi               = c.get("anti_detection", "port_range", fallback="6881-6999").split("-")
        self._port_lo, self._port_hi = int(lo), int(hi)
        self._wl             = [x.strip() for x in c.get("anti_detection", "tracker_whitelist", fallback="").split(",") if x.strip()]
        self._bl             = [x.strip() for x in c.get("anti_detection", "tracker_blacklist", fallback="").split(",") if x.strip()]
        self._persist        = c.getboolean("stats", "persist_stats", fallback=True)
        self._auto_purge     = c.getboolean("stats", "auto_purge_stopped", fallback=True)
        if self._persist:
            self._load_stats()
        logger.info("NewGreedy %s started — proxy listening on port %s",
                    VERSION, c.get("proxy", "listen_port", fallback="3456"))

    def _load_stats(self):
        VALID = re.compile(r"[0-9a-f]{6,40}")
        try:
            with open(STATS_FILE) as f:
                raw = json.load(f)
            schema = raw.get("_schema_version", 1)
            loaded = 0
            for k, d in raw.items():
                if k.startswith("_"):
                    continue
                k = k.lower().strip()
                if not VALID.match(k) or not isinstance(d, dict):
                    continue
                ul_key = "cumul_rep_ul" if "cumul_rep_ul" in d else "rep_ul"
                dl_key = "cumul_rep_dl" if "cumul_rep_dl" in d else "rep_dl"
                if ul_key not in d:
                    continue
                s = TorrentStats(self._cfg)
                s._cumul_rep_ul  = float(d.get(ul_key, 0))
                s._cumul_rep_dl  = float(d.get(dl_key, 0))
                s._cumul_real_ul = float(d.get("cumul_real_ul", 0))
                s._ann_count     = int(d.get("ann_count", 0))
                s._prev_rep_ul   = s._cumul_rep_ul
                s._is_net_stalled= bool(d.get("stalled", False))
                s._target_reached= bool(d.get("target_reached", False))
                s._seed_fake_dl  = float(d.get("seed_fake_dl", 0))
                s._history       = list(d.get("history", []))
                s._mode          = d.get("mode", "down")
                s._last_announce_ts = float(d.get("last_announce_ts", 0))
                self._stats[k[:8]] = s
                loaded += 1
            logger.info("Stats loaded: %d torrents (schema v%d)", loaded, schema)
        except FileNotFoundError:
            logger.info("No stats file — starting fresh.")
        except Exception as e:
            logger.warning("Stats load error: %s", e)

    def _save_stats(self):
        try:
            cutoff = time.time() - 43200
            data = {"_schema_version": SCHEMA_VER}
            purged = []
            for ih, s in self._stats.items():
                if s._last_announce_ts > 0 and s._last_announce_ts < cutoff and s._ann_count >= 5:
                    purged.append(ih)
                    continue
                data[ih] = {
                    "cumul_rep_ul":    s._cumul_rep_ul,
                    "cumul_rep_dl":    s._cumul_rep_dl,
                    "cumul_real_ul":   s._cumul_real_ul,
                    "ann_count":       s._ann_count,
                    "stalled":         s._is_net_stalled,
                    "prev_rep_ul":     s._prev_rep_ul,
                    "target_reached":  s._target_reached,
                    "seed_fake_dl":    s._seed_fake_dl,
                    "history":         s._history[-100:],
                    "mode":            s._mode,
                    "last_announce_ts":s._last_announce_ts,
                }
            for ih in purged:
                del self._stats[ih]
                logger.info("[AUTO-PURGE] %s — no announce for 12h+", ih)
            with open(STATS_FILE, "w") as f:
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

    def _get_stats(self, ih, ih_full=""):
        if ih not in self._stats:
            st = TorrentStats(self._cfg)
            try:
                with open(REGISTRY_FILE) as f:
                    reg = json.load(f)
                if ih in reg:
                    st._seed_fake_dl = float(reg[ih].get("size_bytes", 0))
            except Exception:
                pass
            self._stats[ih] = st
        return self._stats[ih]

    def _parse_int(self, qs, key, default=0):
        try:
            return int(qs.get(key, [default])[0])
        except Exception:
            return default

    def request(self, flow: http.HTTPFlow):
        url = flow.request.pretty_url
        if not self._tracker_allowed(url):
            return
        if self._intercept_scrape and "scrape" in urlparse(url).path:
            return
        if not self._is_announce(url):
            return
        domain    = _tracker_domain(url)
        raw_path  = flow.request.data.path
        q_start   = raw_path.find(b"?")
        raw_query = raw_path[q_start + 1:] if q_start != -1 else b""
        ih_hex    = _extract_infohash(raw_query)
        if not ih_hex or len(ih_hex) < 6:
            return
        ih_key    = ih_hex[:8]
        parsed    = urlparse(url)
        qs        = parse_qs(parsed.query, keep_blank_values=True)
        now       = time.time()
        last      = self._last_seen.get(ih_key + domain, 0)
        interval  = max(now - last, self._min_interval) if last > 0 else self._min_interval
        interval  += interval * self._jitter_pct * random.uniform(-1, 1)
        self._last_seen[ih_key + domain] = now
        event     = qs.get("event", [""])[0]

        if event == "stopped" and self._auto_purge:
            if ih_key in self._stats:
                del self._stats[ih_key]
                for k in list(self._last_seen.keys()):
                    if k.startswith(ih_key):
                        del self._last_seen[k]
                self._ports.pop(ih_key, None)
                self._peer_ids.pop(ih_key, None)
                self._uas.pop(ih_key, None)
                if self._persist:
                    self._save_stats()
                logger.info("[PURGED] %s — removed after event=stopped", ih_key)
            return

        real_ul  = self._parse_int(qs, "uploaded")
        real_dl  = self._parse_int(qs, "downloaded")
        left     = self._parse_int(qs, "left")
        st       = self._get_stats(ih_key, ih_hex)
        st._mode = "seed" if left == 0 else "down"
        st._last_announce_ts = now

        swarm_key = domain + ih_key
        if swarm_key in self._swarm_leechers:
            st._leecher_count = self._swarm_leechers[swarm_key]

        new_ul, new_dl, delta_ul, is_stag, corrupt_val = st.compute(real_ul, real_dl, interval, event)

        tc       = self._tracker_cumul.setdefault(domain, {"ul": 0.0, "dl": 0.0})
        tc["ul"] += delta_ul
        tc["dl"] += real_dl
        if tc["dl"] > 0 and tc["ul"] / tc["dl"] > self._max_global_r:
            new_ul = max(st._prev_rep_ul, new_ul - (tc["ul"] - self._max_global_r * tc["dl"]))

        is_pure_seeder = (left == 0 and real_dl == 0)
        patches = {"uploaded": int(new_ul)}

        if is_pure_seeder:
            if st._seed_fake_dl == 0:
                ihb = bytes.fromhex(ih_hex[:16]) if len(ih_hex) >= 16 else b"\x00" * 8
                base = int.from_bytes(ihb[:4], "big")
                st._seed_fake_dl = (base % 49000 + 1000) * 1e6
            patches["downloaded"] = int(st._seed_fake_dl)
        else:
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
            _unreserved = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
            pid_pct   = b"peer_id=" + b"".join(
                bytes([b]) if b in _unreserved else (b"%" + format(b, "02X").encode())
                for b in self._peer_ids[ih_key]
            )
            parts     = raw_query.split(b"&")
            parts     = [p for p in parts if not p.startswith(b"peer_id=")]
            parts.append(pid_pct)
            raw_query = b"&".join(parts)

        new_raw_query          = _patch_query_bytes(raw_query, patches)
        path_only              = raw_path[:q_start] if q_start != -1 else raw_path
        flow.request.data.path = path_only + b"?" + new_raw_query

        if self._spoof_hdr:
            ua = (self._uas.get(ih_key, "qBittorrent/4.6.8") if self._spoof_ua
                  else self._cfg.get("anti_detection", "user_agent_value", fallback="qBittorrent/4.6.8"))
            flow.request.headers["User-Agent"]      = ua
            flow.request.headers["Accept"]          = _accept_for_ua(ua)
            flow.request.headers["Accept-Language"] = "en-US,en;q=0.9"
            flow.request.headers["Connection"]      = "keep-alive"

        mode  = "SEED" if left == 0 else "DOWN"
        cum_dl= st._cumul_rep_dl / 1e6
        cum_ul= new_ul / 1e6
        delta = delta_ul / 1e6
        ratio = new_ul / st._cumul_rep_dl if st._cumul_rep_dl > 0 else 0.0
        avg_d = new_ul / st._ann_count if st._ann_count > 1 else delta_ul
        eta   = int(max(0, st._cumul_rep_dl * st._target_ratio - new_ul) / avg_d) if avg_d > 0 else 0

        stag_t   = " [STAG]"           if is_stag               else ""
        stall_t  = " [STALL_NET]"      if st._is_net_stalled    else ""
        algo_t   = " [STALL_ALGO]"     if st._is_algo_stalled   else ""
        target_t = " [TARGET_REACHED]" if st._target_reached    else ""
        flags    = "".join(filter(None, [stag_t, stall_t, algo_t, target_t]))

        if st._cumul_rep_dl > 0:
            logger.info("[%-4s] %-8s | DL:%7.1fM UL:%7.1fM +%6.1fM R:%.2f ETA:~%da #%d%s",
                        mode, ih_key, cum_dl, cum_ul, delta, ratio, eta, st._ann_count, flags)
        else:
            logger.info("[%-4s] %-8s | UL:%7.1fM +%6.1fM #%d%s",
                        mode, ih_key, cum_ul, delta, st._ann_count, flags)

        if self._persist and (st._ann_count == 1 or st._ann_count % 5 == 0):
            self._save_stats()

    def response(self, flow: http.HTTPFlow):
        url = flow.request.pretty_url
        if not self._is_announce(url):
            return
        domain    = _tracker_domain(url)
        raw_path  = flow.request.data.path
        q_start   = raw_path.find(b"?")
        raw_query = raw_path[q_start + 1:] if q_start != -1 else b""
        ih_hex    = _extract_infohash(raw_query)
        if not ih_hex or len(ih_hex) < 6:
            return
        ih_key = ih_hex[:8]
        try:
            body       = flow.response.content
            incomplete = _bencode_get_int(body, b"incomplete")
            if incomplete >= 0:
                swarm_key = domain + ih_key
                self._swarm_leechers[swarm_key] = incomplete
                st = self._get_stats(ih_key)
                st._leecher_count = incomplete
                if incomplete == 0:
                    logger.debug("[SWARM] %s@%s — 0 leechers, stagnation forced", ih_key, domain)
        except Exception:
            pass

    def done(self):
        if self._persist:
            self._save_stats()
        logger.info("NewGreedy %s stopping — stats saved.", VERSION)


addons = [NewGreedyAddon()]
