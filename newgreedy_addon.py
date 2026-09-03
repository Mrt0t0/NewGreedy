#!/usr/bin/env python3
"""NewGreedy v1.7.6 — core addon"""
#
# NewGreedy - BitTorrent announce proxy
# Copyright (C) 2026  Mrt0t0
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
import os, random, math, time, json, re, logging, struct, configparser, threading, tempfile
from logging.handlers import RotatingFileHandler
from datetime import datetime
from urllib.parse import urlparse, parse_qs, unquote_to_bytes
from mitmproxy import http
import pathlib

VERSION       = "v1.7.6"
SCHEMA_VER    = 5
_BASE         = pathlib.Path(__file__).parent.resolve()
LOG_FILE           = str(_BASE / "newgreedy.log")
STATS_FILE         = str(_BASE / "stats.json")
PURGE_PENDING_FILE = str(_BASE / "purge_pending.json")
REGISTRY_FILE      = str(_BASE / "torrent_registry.json")

LOG_MAX_BYTES   = 10 * 1024 * 1024
LOG_BACKUPS     = 3
LAST_SEEN_TTL   = 86400
MEM_CLEAN_EVERY = 3600
HISTORY_MAX     = 200
HISTORY_FULL_S  = 86400
KEY_SEP         = "|"

_LOG_FORMAT = "%(asctime)s %(message)s"

logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT, handlers=[logging.StreamHandler()])
logging.getLogger("passlib").setLevel(logging.ERROR)

logger = logging.getLogger("NewGreedy")
logger.setLevel(logging.INFO)


def _file_handler_attached() -> bool:
    for h in logger.handlers:
        if isinstance(h, RotatingFileHandler):
            try:
                if os.path.abspath(h.baseFilename) == os.path.abspath(LOG_FILE):
                    return True
            except Exception:
                continue
    return False


def _ensure_file_handler():
    if _file_handler_attached():
        return
    fh = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES,
                             backupCount=LOG_BACKUPS, encoding="utf-8")
    fh.setFormatter(logging.Formatter(_LOG_FORMAT))
    fh.setLevel(logging.INFO)
    logger.addHandler(fh)


_ensure_file_handler()

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

TRACKER_BACKOFF_THRESHOLD = 3
TRACKER_BACKOFF_MAX_S     = 1800


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


def _validate_config(c):
    checks = [
        ("spoofing", "target_ratio", float),
        ("spoofing", "target_ratio_buffer", float),
        ("spoofing", "catch_up_factor", float),
        ("spoofing", "max_ratio_per_torrent", float),
        ("spoofing", "max_global_ratio_per_tracker", float),
        ("spoofing", "max_simulated_speed_mbps", float),
        ("spoofing", "upload_noise_pct", float),
        ("spoofing", "stagnation_probability", float),
        ("spoofing", "seed_credit_mb", float),
        ("advanced", "min_announce_interval", int),
        ("advanced", "stall_announce_threshold", int),
        ("advanced", "min_announces_before_stagnation", int),
        ("advanced", "interval_jitter_pct", float),
        ("advanced", "event_anomaly_probability", float),
        ("advanced", "corrupt_field_probability", float),
        ("stats", "tracker_cumul_ttl_days", int),
    ]
    for section, key, typ in checks:
        raw = c.get(section, key, fallback=None)
        if raw is None:
            continue
        try:
            typ(raw)
        except (ValueError, TypeError):
            logger.warning("config.ini [%s] %s='%s' is not a valid %s - fallback will be used",
                           section, key, raw, typ.__name__)


def _load_cfg():
    c = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    c.read(str(_BASE / "config.ini"))
    _validate_config(c)
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
        lo, hi = int(parts[0]), int(parts[1])
        if not (0 <= lo <= 23) or not (0 <= hi <= 23):
            logger.warning("inject_hours '%s' out of range 0-23 - falling back to 0-23", spec)
            return 0, 23
        return lo, hi
    except Exception:
        logger.warning("inject_hours '%s' invalid - falling back to 0-23", spec)
        return 0, 23


def _atomic_write_json(path, data):
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _compress_history(hist, now):
    if not hist:
        return []
    cutoff = now - HISTORY_FULL_S
    recent = [p for p in hist if p.get("t", 0) >= cutoff]
    old    = [p for p in hist if p.get("t", 0) < cutoff]
    bucket = {}
    for p in old:
        bucket[p.get("t", 0) // 3600] = p
    merged = sorted(bucket.values(), key=lambda x: x.get("t", 0)) + recent
    return merged[-HISTORY_MAX:]


class TorrentStats:
    __slots__ = (
        "_stagnation_p", "_catch_up", "_max_speed", "_noise_pct", "_max_ratio_t",
        "_seed_credit", "_seed_target_mb", "_stall_thr", "_min_ann_stag", "_target_buf",
        "_corrupt_p", "_target_ratio", "_auto_stop", "_active_hours", "_cumul_rep_ul",
        "_cumul_rep_dl", "_cumul_real_ul", "_ann_count", "_zero_dl_count", "_is_net_stalled",
        "_is_algo_stalled", "_prev_rep_ul", "_target_reached", "_leecher_count",
        "_seed_fake_dl", "_history", "_mode", "_last_announce_ts",
        "_peer_id", "_port", "_key", "_tracker_intervals", "_trackers",
    )

    def __init__(self, params):
        (self._stagnation_p, self._catch_up, self._max_speed, self._noise_pct,
         self._max_ratio_t, self._seed_credit, self._seed_target_mb, self._stall_thr,
         self._min_ann_stag, self._target_buf, self._corrupt_p, self._target_ratio,
         self._auto_stop, self._active_hours) = params
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
        self._peer_id        = ""
        self._port           = 0
        self._key            = 0
        self._tracker_intervals = {}
        self._trackers       = {}

    def ensure_identity(self, port_lo, port_hi):
        if not self._peer_id:
            self._peer_id = _rand_peer_id().hex()
        if not self._port:
            self._port = random.randint(port_lo, port_hi)
        if not self._key:
            self._key = random.randint(1, 0xFFFFFFFF)

    def peer_id_bytes(self):
        try:
            return bytes.fromhex(self._peer_id)
        except Exception:
            pid = _rand_peer_id()
            self._peer_id = pid.hex()
            return pid

    def user_agent(self):
        return _ua_for_prefix(self.peer_id_bytes()[:8])

    def record_tracker(self, domain, delta_ul, real_dl):
        t = self._trackers.setdefault(domain, {"ul": 0.0, "dl": 0.0})
        t["ul"] += delta_ul
        t["dl"] += real_dl

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
        if self._auto_stop and self._target_reached:
            return self._cumul_rep_ul + (real_ul * random.uniform(0.9, 1.1) if real_ul > 0 else 0), False
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
        now = int(time.time())
        self._history.append({
            "t":  now,
            "ul": int(self._cumul_rep_ul),
            "dl": int(self._cumul_rep_dl),
        })
        if len(self._history) > HISTORY_MAX:
            self._history = _compress_history(self._history, now)

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
        self._tracker_errors = {}
        self._last_seen      = {}
        self._swarm_leechers = {}
        self._save_lock      = threading.Lock()
        self._last_flush_ts  = 0.0
        self._flush_interval = 60.0
        self._last_mem_clean = time.time()
        self._registry       = self._load_registry()
        self._stats_params   = self._build_stats_params(c)
        self._max_global_r   = c.getfloat("spoofing", "max_global_ratio_per_tracker", fallback=2.5)
        self._min_interval   = c.getint("advanced", "min_announce_interval", fallback=1800)
        self._jitter_pct     = c.getfloat("advanced", "interval_jitter_pct", fallback=0.08)
        self._event_anom_p   = c.getfloat("advanced", "event_anomaly_probability", fallback=0.03)
        self._corrupt_p      = c.getfloat("advanced", "corrupt_field_probability", fallback=0.05)
        self._respect_iv     = c.getboolean("advanced", "respect_tracker_interval", fallback=True)
        self._cumul_ttl      = c.getint("stats", "tracker_cumul_ttl_days", fallback=30) * 86400
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
        self._warn_udp_trackers()
        self._stop_evt = threading.Event()
        threading.Thread(target=self._shutdown_watcher, daemon=True).start()
        logger.info("NewGreedy %s started - proxy listening on port %s",
                    VERSION, c.get("proxy", "listen_port", fallback="3456"))

    def _shutdown_watcher(self):
        flag = _BASE / ".shutdown"
        while not self._stop_evt.wait(1.0):
            if flag.exists():
                logger.info("Shutdown signal received - flushing stats")
                if self._persist:
                    self._save_stats(force=True)
                try:
                    flag.unlink()
                except OSError:
                    pass
                self._stop_evt.set()
                return

    def _build_stats_params(self, c):
        return (
            c.getfloat("spoofing", "stagnation_probability", fallback=0.03),
            c.getfloat("spoofing", "catch_up_factor", fallback=0.22),
            c.getfloat("spoofing", "max_simulated_speed_mbps", fallback=10.0) * 1e6,
            c.getfloat("spoofing", "upload_noise_pct", fallback=3.0) / 100,
            c.getfloat("spoofing", "max_ratio_per_torrent", fallback=3.0),
            c.getfloat("spoofing", "seed_credit_mb", fallback=5.0) * 1e6,
            c.getfloat("spoofing", "seed_target_mb", fallback=500.0) * 1e6,
            c.getint("advanced", "stall_announce_threshold", fallback=8),
            c.getint("advanced", "min_announces_before_stagnation", fallback=10),
            c.getfloat("spoofing", "target_ratio_buffer", fallback=0.03),
            c.getfloat("advanced", "corrupt_field_probability", fallback=0.05),
            c.getfloat("spoofing", "target_ratio", fallback=1.5) + c.getfloat("spoofing", "target_ratio_buffer", fallback=0.03),
            c.getboolean("spoofing", "auto_stop_at_target", fallback=True),
            _parse_hours(c.get("advanced", "inject_hours", fallback="0-23")),
        )

    def _new_stats(self):
        return TorrentStats(self._stats_params)

    def _warn_udp_trackers(self):
        udp_domains = set()
        for meta in self._registry.values():
            if not isinstance(meta, dict):
                continue
            for url in meta.get("udp_trackers", []):
                d = _tracker_domain(url)
                if d:
                    udp_domains.add(d)
        for d in sorted(udp_domains):
            logger.warning("[UDP_BYPASS] %s - UDP tracker cannot be intercepted, announces bypass NewGreedy", d)
        if udp_domains:
            logger.warning("[UDP_BYPASS] %d UDP tracker domain(s) detected - real stats are reported to them",
                           len(udp_domains))

    def _load_stats(self):
        VALID = re.compile(r"^[0-9a-f]{6,40}$")
        try:
            with open(STATS_FILE, encoding="utf-8") as f:
                raw = json.load(f)
            schema = raw.get("_schema_version", 1)
            tc_raw = raw.get("_tracker_cumul", {})
            if isinstance(tc_raw, dict):
                for domain, vals in tc_raw.items():
                    if isinstance(vals, dict):
                        self._tracker_cumul[domain] = {
                            "ul": float(vals.get("ul", 0)),
                            "dl": float(vals.get("dl", 0)),
                            "ts": float(vals.get("ts", time.time())),
                        }
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
                s = self._new_stats()
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
                s._peer_id       = str(d.get("peer_id", "") or "")
                s._port          = int(d.get("port", 0) or 0)
                s._key           = int(d.get("key", 0) or 0)
                iv = d.get("tracker_intervals", {})
                s._tracker_intervals = {k2: int(v2) for k2, v2 in iv.items()} if isinstance(iv, dict) else {}
                tr = d.get("trackers", {})
                if isinstance(tr, dict):
                    s._trackers = {
                        k2: {"ul": float(v2.get("ul", 0)), "dl": float(v2.get("dl", 0))}
                        for k2, v2 in tr.items() if isinstance(v2, dict)
                    }
                self._stats[k[:8]] = s
                loaded += 1
            logger.info("Stats loaded: %d torrents (schema v%d)", loaded, schema)
        except FileNotFoundError:
            logger.info("No stats file - starting fresh.")
        except Exception as e:
            logger.warning("Stats load error: %s", e)

    def _save_stats(self, force=False):
        now = time.time()
        if not force and (now - self._last_flush_ts) < self._flush_interval:
            return
        _ensure_file_handler()
        with self._save_lock:
            try:
                cutoff = now - 43200
                data = {"_schema_version": SCHEMA_VER}
                tc_cutoff = now - self._cumul_ttl if self._cumul_ttl > 0 else 0
                tc_out = {}
                dropped_domains = []
                for domain, tc in dict(self._tracker_cumul).items():
                    ts = float(tc.get("ts", now))
                    if tc_cutoff > 0 and ts < tc_cutoff:
                        dropped_domains.append(domain)
                        continue
                    tc_out[domain] = {"ul": tc["ul"], "dl": tc["dl"], "ts": ts}
                for domain in dropped_domains:
                    self._tracker_cumul.pop(domain, None)
                    logger.info("[CUMUL_PURGE] %s - inactive tracker dropped from cumul", domain)
                data["_tracker_cumul"] = tc_out
                pending = []
                try:
                    with open(PURGE_PENDING_FILE, encoding="utf-8") as f:
                        pending = json.load(f)
                    for ih in pending:
                        if ih in self._stats:
                            del self._stats[ih]
                            logger.info("[PURGE_SYNC] %s - removed from memory (web purge)", ih)
                    os.remove(PURGE_PENDING_FILE)
                except FileNotFoundError:
                    pass
                except Exception as e:
                    logger.warning("purge_pending read error: %s", e)
                purged = []
                for ih, s in dict(self._stats).items():
                    if s._last_announce_ts > 0 and s._last_announce_ts < cutoff and s._ann_count >= 5:
                        purged.append(ih)
                        continue
                    data[ih] = {
                        "cumul_rep_ul":     s._cumul_rep_ul,
                        "cumul_rep_dl":     s._cumul_rep_dl,
                        "cumul_real_ul":    s._cumul_real_ul,
                        "ann_count":        s._ann_count,
                        "stalled":          s._is_net_stalled,
                        "prev_rep_ul":      s._prev_rep_ul,
                        "target_reached":   s._target_reached,
                        "seed_fake_dl":     s._seed_fake_dl,
                        "history":          _compress_history(s._history, now),
                        "mode":             s._mode,
                        "last_announce_ts": s._last_announce_ts,
                        "peer_id":          s._peer_id,
                        "port":             s._port,
                        "key":              s._key,
                        "tracker_intervals": s._tracker_intervals,
                        "trackers":         s._trackers,
                    }
                for ih in purged:
                    del self._stats[ih]
                    logger.info("[AUTO-PURGE] %s - no announce for 12h+", ih)
                _atomic_write_json(STATS_FILE, data)
                self._last_flush_ts = now
            except Exception as e:
                logger.warning("Stats save failed: %s", e)

    def _clean_memory(self):
        now = time.time()
        if (now - self._last_mem_clean) < MEM_CLEAN_EVERY:
            return
        self._last_mem_clean = now
        _ensure_file_handler()
        active = set(self._stats.keys())
        cutoff = now - LAST_SEEN_TTL
        removed = 0
        for k in list(self._last_seen.keys()):
            if self._last_seen[k] < cutoff:
                del self._last_seen[k]
                removed += 1
        for sk in list(self._swarm_leechers.keys()):
            ih = sk.split(KEY_SEP)[-1]
            if ih not in active:
                del self._swarm_leechers[sk]
        if removed:
            logger.debug("[MEM_CLEAN] removed %d stale last_seen entries", removed)

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

    def _tracker_in_backoff(self, domain) -> bool:
        err = self._tracker_errors.get(domain)
        if not err:
            return False
        count, last_ts = err
        if count < TRACKER_BACKOFF_THRESHOLD:
            return False
        backoff = min(300 * (2 ** (count - TRACKER_BACKOFF_THRESHOLD)), TRACKER_BACKOFF_MAX_S)
        return (time.time() - last_ts) < backoff

    def _record_tracker_error(self, domain):
        err = self._tracker_errors.get(domain, (0, 0))
        count = err[0] + 1
        self._tracker_errors[domain] = (count, time.time())
        if count == TRACKER_BACKOFF_THRESHOLD:
            logger.warning("[TRACKER_DOWN] %s - %d consecutive errors, entering backoff", domain, count)

    def _record_tracker_success(self, domain):
        if domain in self._tracker_errors:
            prev_count = self._tracker_errors[domain][0]
            del self._tracker_errors[domain]
            if prev_count >= TRACKER_BACKOFF_THRESHOLD:
                logger.info("[TRACKER_UP] %s - recovered", domain)

    def _load_registry(self):
        try:
            with open(REGISTRY_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _get_stats(self, ih, ih_full=""):
        if ih not in self._stats:
            st = self._new_stats()
            meta = self._registry.get(ih)
            if isinstance(meta, dict):
                try:
                    st._seed_fake_dl = float(meta.get("size_bytes", 0))
                except Exception:
                    pass
            self._stats[ih] = st
        return self._stats[ih]

    def _parse_int(self, qs, key, default=0):
        try:
            return int(qs.get(key, [default])[0])
        except Exception:
            return default

    def _effective_interval(self, st, domain, elapsed):
        base = self._min_interval
        if self._respect_iv:
            tiv = st._tracker_intervals.get(domain, 0)
            if tiv > 0:
                base = tiv
        interval = max(elapsed, base) if elapsed > 0 else base
        return interval + interval * self._jitter_pct * random.uniform(-1, 1)

    def request(self, flow: http.HTTPFlow):
        url = flow.request.pretty_url
        if not self._tracker_allowed(url):
            return
        if self._intercept_scrape and "scrape" in urlparse(url).path:
            return
        if not self._is_announce(url):
            return
        domain    = _tracker_domain(url)
        if self._tracker_in_backoff(domain):
            return
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
        seen_key  = ih_key + KEY_SEP + domain
        last      = self._last_seen.get(seen_key, 0)
        event     = qs.get("event", [""])[0]

        if event == "stopped" and self._auto_purge:
            if ih_key in self._stats:
                del self._stats[ih_key]
                for k in list(self._last_seen.keys()):
                    if k.split(KEY_SEP)[0] == ih_key:
                        del self._last_seen[k]
                if self._persist:
                    self._save_stats(force=True)
                logger.info("[PURGED] %s - removed after event=stopped", ih_key)
            return

        real_ul  = self._parse_int(qs, "uploaded")
        real_dl  = self._parse_int(qs, "downloaded")
        left     = self._parse_int(qs, "left")
        st       = self._get_stats(ih_key, ih_hex)
        st.ensure_identity(self._port_lo, self._port_hi)
        st._mode = "seed" if left == 0 else "down"
        st._last_announce_ts = now

        elapsed  = (now - last) if last > 0 else 0
        interval = self._effective_interval(st, domain, elapsed)
        self._last_seen[seen_key] = now

        swarm_key = domain + KEY_SEP + ih_key
        if swarm_key in self._swarm_leechers:
            st._leecher_count = self._swarm_leechers[swarm_key]

        new_ul, new_dl, delta_ul, is_stag, corrupt_val = st.compute(real_ul, real_dl, interval, event)

        tc = self._tracker_cumul.setdefault(domain, {"ul": 0.0, "dl": 0.0, "ts": now})
        tc["ts"] = now
        tc["dl"] += real_dl
        if tc["dl"] > 0 and (tc["ul"] + delta_ul) / tc["dl"] > self._max_global_r:
            allowed_delta = max(0.0, self._max_global_r * tc["dl"] - tc["ul"])
            new_ul = st._prev_rep_ul + allowed_delta
            new_ul = max(st._prev_rep_ul, new_ul)
            delta_ul = new_ul - st._cumul_rep_ul
            st._cumul_rep_ul = st._prev_rep_ul = new_ul
            logger.info("[GLOBAL_CAP] %s | tracker_ratio capped -> UL adjusted to %.1fM",
                        domain, new_ul / 1e6)
        tc["ul"] += delta_ul
        st.record_tracker(domain, delta_ul, real_dl)

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
            patches["port"] = st._port

        if self._spoof_peers:
            nw = self._parse_int(qs, "numwant", 50)
            patches["numwant"] = int(nw * random.uniform(0.85, 1.15))

        if corrupt_val is not None:
            patches["corrupt"] = corrupt_val

        if random.random() < self._event_anom_p and event not in ("started", "stopped"):
            patches["event"] = "started"

        if self._spoof_pid:
            patches["key"] = format(st._key, "08x")
            _unreserved = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
            pid_pct   = b"peer_id=" + b"".join(
                bytes([b]) if b in _unreserved else (b"%" + format(b, "02X").encode())
                for b in st.peer_id_bytes()
            )
            parts     = raw_query.split(b"&")
            parts     = [p for p in parts if not p.startswith(b"peer_id=")]
            parts.append(pid_pct)
            raw_query = b"&".join(parts)

        new_raw_query          = _patch_query_bytes(raw_query, patches)
        path_only              = raw_path[:q_start] if q_start != -1 else raw_path
        flow.request.data.path = path_only + b"?" + new_raw_query

        if self._spoof_hdr:
            ua = (st.user_agent() if self._spoof_ua
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

        if self._persist:
            self._save_stats()
        self._clean_memory()

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
            status = flow.response.status_code
            if status >= 500:
                self._record_tracker_error(domain)
            else:
                self._record_tracker_success(domain)
            body       = flow.response.content
            st         = self._get_stats(ih_key)
            interval   = _bencode_get_int(body, b"interval")
            if interval > 0:
                prev = st._tracker_intervals.get(domain)
                st._tracker_intervals[domain] = interval
                if prev != interval:
                    logger.debug("[INTERVAL] %s@%s - tracker interval %ds", ih_key, domain, interval)
            incomplete = _bencode_get_int(body, b"incomplete")
            if incomplete >= 0:
                self._swarm_leechers[domain + KEY_SEP + ih_key] = incomplete
                st._leecher_count = incomplete
                if incomplete == 0:
                    logger.debug("[SWARM] %s@%s - 0 leechers, stagnation forced", ih_key, domain)
        except Exception:
            pass

    def done(self):
        if self._persist:
            self._save_stats(force=True)
        logger.info("NewGreedy %s stopping - stats saved.", VERSION)


addons = [NewGreedyAddon()]
