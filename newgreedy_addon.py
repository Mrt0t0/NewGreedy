#!/usr/bin/env python3
"""NewGreedy v1.7.7 — core addon"""
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
import os, random, math, time, json, re, hashlib, logging, configparser, threading
from logging.handlers import RotatingFileHandler
from datetime import datetime
from urllib.parse import urlparse, parse_qs, unquote_to_bytes, quote_from_bytes
from mitmproxy import http
import pathlib

from _version import (VERSION, bd_find_int, bd_replace_int, bd_top_str, notify,
                      notify_configure, atomic_write_json)
import client_profiles

SCHEMA_VER    = 7
_BASE         = pathlib.Path(__file__).parent.resolve()
LOG_FILE           = str(_BASE / "newgreedy.log")
STATS_FILE         = str(_BASE / "stats.json")
PURGE_PENDING_FILE = str(_BASE / "purge_pending.json")
CONTROL_FILE       = str(_BASE / "control.json")
REGISTRY_FILE      = str(_BASE / "torrent_registry.json")

LOG_MAX_BYTES   = 10 * 1024 * 1024
LOG_BACKUPS     = 3
LAST_SEEN_TTL   = 86400
MEM_CLEAN_EVERY = 3600
HISTORY_MAX     = 200
HISTORY_FULL_S  = 86400
KEY_SEP         = "|"
CONTROL_POLL    = 5.0

_LOG_FORMAT = "%(asctime)s %(message)s"

logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT, handlers=[logging.StreamHandler()])
logging.getLogger("passlib").setLevel(logging.ERROR)

logger = logging.getLogger("NewGreedy")
logger.setLevel(logging.INFO)

TRACKER_BACKOFF_THRESHOLD = 3
TRACKER_BACKOFF_MAX_S     = 1800

DESYNC_MAX_SPREAD  = 0.45
DESYNC_EPOCH_S     = 3600
DESYNC_SLOTS_MAX   = 256
BURST_RANK_MAX     = 24
IDENTITY_MODES     = ("stable", "drift", "rotate")
TRACKERID_MAX      = 128


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
        ("advanced", "corrupt_field_probability", float),
        ("anti_detection", "desync_spread_pct", float),
        ("anti_detection", "desync_min_gap", int),
        ("anti_detection", "startup_burst_window", int),
        ("anti_detection", "startup_ramp_floor", float),
        ("stats", "tracker_cumul_ttl_days", int),
        ("stats", "stopped_purge_delay", int),
        ("client", "version_bump_probability", float),
        ("bandwidth", "global_max_speed_mbps", float),
        ("bandwidth", "max_download_speed_mbps", float),
        ("bandwidth", "per_torrent_speed_cap_mbps", float),
        ("bandwidth", "circadian_night_factor", float),
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
    c = configparser.ConfigParser(inline_comment_prefixes=(";", "#"), interpolation=None)
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


def _split_pairs(raw_query: bytes):
    pairs = []
    for part in raw_query.split(b"&"):
        if not part:
            continue
        if b"=" in part:
            k, v = part.split(b"=", 1)
        else:
            k, v = part, b""
        pairs.append((k.decode("utf-8", errors="replace"), v))
    return pairs


def _build_query(pairs) -> bytes:
    out = []
    for key, raw in pairs:
        out.append(key.encode() + b"=" + raw if raw != b"" else key.encode() + b"=")
    return b"&".join(out)


def _pct_encode(data: bytes) -> bytes:
    return quote_from_bytes(data, safe="").encode()


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


class SessionIdentity:
    __slots__ = ("profile_name", "profile", "peer_id", "user_agent", "port", "salt",
                 "mode", "bumped_from")

    def __init__(self, wanted, port_lo, port_hi, saved=None, mode="stable", bump_p=0.0):
        saved = saved or {}
        name = saved.get("profile") or ""
        if wanted and wanted.strip().lower() in client_profiles.PROFILES:
            name = wanted.strip().lower()
        elif name not in client_profiles.PROFILES:
            name = client_profiles.resolve_profile(wanted)
        self.profile_name = name
        self.profile      = client_profiles.PROFILES[name]
        self.mode         = mode
        self.bumped_from  = ""
        saved_ua  = saved.get("user_agent") or ""
        saved_pid = saved.get("peer_id") or ""
        if saved_ua not in client_profiles.user_agents(self.profile):
            saved_ua, saved_pid = "", ""
        prefix, ua = client_profiles.pick_identity(self.profile, saved_ua)
        if mode == "drift":
            if (saved_ua and bump_p > 0
                    and client_profiles.can_bump(self.profile, saved_ua)
                    and random.random() < bump_p):
                self.bumped_from = saved_ua
                prefix, ua = client_profiles.bump_identity(self.profile, saved_ua)
            saved_pid = ""
        self.user_agent = ua
        try:
            coherent = bytes.fromhex(saved_pid).startswith(prefix) if saved_pid else False
        except ValueError:
            coherent = False
        self.peer_id = saved_pid if coherent else client_profiles.make_peer_id(self.profile, ua).hex()
        try:
            self.port = int(saved.get("port") or 0) or random.randint(port_lo, port_hi)
        except (TypeError, ValueError):
            self.port = random.randint(port_lo, port_hi)
        salt = saved.get("salt") or ""
        if not isinstance(salt, str) or len(salt) != 16:
            salt = os.urandom(8).hex()
        self.salt = salt

    def salt_bytes(self):
        try:
            return bytes.fromhex(self.salt)
        except ValueError:
            self.salt = os.urandom(8).hex()
            return bytes.fromhex(self.salt)

    def peer_id_bytes(self):
        try:
            raw = bytes.fromhex(self.peer_id)
        except ValueError:
            raw = b""
        if len(raw) != 20:
            raw = client_profiles.make_peer_id(self.profile, self.user_agent)
            self.peer_id = raw.hex()
        return raw

    def as_dict(self):
        return {"profile": self.profile_name, "peer_id": self.peer_id,
                "user_agent": self.user_agent, "port": self.port, "salt": self.salt}



class TorrentStats:
    __slots__ = (
        "_stagnation_p", "_catch_up", "_max_speed", "_noise_pct", "_max_ratio_t",
        "_seed_credit", "_stall_thr", "_min_ann_stag", "_target_buf",
        "_target_ratio", "_auto_stop", "_active_hours", "_cumul_rep_ul",
        "_cumul_rep_dl", "_cumul_real_ul", "_ann_count", "_zero_dl_count", "_is_net_stalled",
        "_is_algo_stalled", "_prev_rep_ul", "_target_reached", "_leecher_count",
        "_seed_fake_dl", "_history", "_mode", "_last_announce_ts",
        "_peer_id", "_port", "_key", "_tracker_intervals", "_trackers",
        "_profile_name", "_user_agent", "_size_bytes", "_paused", "_tags",
        "_night_factor", "_smoothing", "_speed_cap", "_numwant", "_burst_rank",
        "_reported_dl", "_trackerids", "_dl_capped", "_seen_ul",
    )

    def __init__(self, params):
        (self._stagnation_p, self._catch_up, self._max_speed, self._noise_pct,
         self._max_ratio_t, self._seed_credit, self._stall_thr,
         self._min_ann_stag, self._target_buf, self._target_ratio,
         self._auto_stop, self._active_hours, self._night_factor, self._smoothing,
         self._speed_cap) = params
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
        self._profile_name   = ""
        self._user_agent     = ""
        self._size_bytes     = 0
        self._paused         = False
        self._tags           = []
        self._numwant        = 0
        self._burst_rank     = -1
        self._reported_dl    = 0
        self._trackerids     = {}
        self._dl_capped      = False
        self._seen_ul        = 0.0

    def adopt_identity(self, ident):
        self._profile_name = ident.profile_name
        self._peer_id      = ident.peer_id
        self._user_agent   = ident.user_agent
        self._port         = ident.port
        if not self._key:
            self._key = random.randint(1, 0xFFFFFFFF)
        return ident.profile

    def dl_delta(self, domain, real_dl):
        t = self._trackers.setdefault(domain, {"ul": 0.0, "dl": 0.0, "seen_dl": 0.0})
        seen = float(t.get("seen_dl", 0.0))
        t["seen_dl"] = float(real_dl)
        return float(real_dl) if real_dl < seen else float(real_dl) - seen

    def record_tracker(self, domain, delta_ul, delta_dl):
        t = self._trackers.setdefault(domain, {"ul": 0.0, "dl": 0.0, "seen_dl": 0.0})
        t["ul"] += delta_ul
        t["dl"] += delta_dl

    def _hour_factor(self) -> float:
        hour = datetime.now().hour
        lo, hi = self._active_hours
        inside = (lo <= hour <= hi) if lo <= hi else (hour >= lo or hour <= hi)
        if not self._smoothing:
            return 1.0 if inside else 0.0
        span = (hi - lo) % 24 or 24
        pos = (hour - lo) % 24
        if pos > span:
            return self._night_factor
        phase = math.sin(math.pi * (pos / span)) if span else 1.0
        return self._night_factor + (1.0 - self._night_factor) * phase

    def _smart_stagnation(self, ann, rp) -> bool:
        if ann < self._min_ann_stag or rp < 0.30:
            return False
        if ann > 30 and rp < 0.65:
            return False
        p = self._stagnation_p * (1.5 if rp > 0.85 else 1.1 if rp > 0.60 else 1.0)
        if self._leecher_count == 0:
            return True
        return random.random() < p

    def _calc_upload(self, real_ul, cum_dl, interval, ann, budget, ramp=1.0):
        if self._paused:
            return self._cumul_rep_ul, True
        factor = self._hour_factor() * max(0.0, min(1.0, ramp))
        if factor <= 0.01:
            return self._cumul_rep_ul, True
        speed_ceiling = min(self._max_speed, self._speed_cap) * interval * factor
        if budget is not None:
            speed_ceiling = min(speed_ceiling, budget)
        if speed_ceiling <= 0:
            return self._cumul_rep_ul, True
        if self._auto_stop and self._target_reached:
            return self._cumul_rep_ul + (real_ul * random.uniform(0.9, 1.1) if real_ul > 0 else 0), False
        if cum_dl <= 0:
            inc = real_ul * random.uniform(1.2, 1.6) if real_ul > 0 else self._seed_credit * random.uniform(0.8, 1.2)
            return self._cumul_rep_ul + min(inc * factor, speed_ceiling), False
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
        inc   = min(remaining * self._catch_up * (1 + 0.5 * decay) * factor, speed_ceiling)
        inc   = max(0, min(_pareto_noise(inc, self._noise_pct), speed_ceiling))
        return max(self._cumul_rep_ul, min(self._cumul_rep_ul + inc, cum_dl * self._max_ratio_t)), False

    def coherent_progress(self, client_left, real_dl, rep_dl, gap, dl_cap):
        self._dl_capped = False
        want = int(self._seed_fake_dl) if (client_left == 0 and real_dl == 0) else int(rep_dl)
        if self._size_bytes > 0:
            want = min(want, self._size_bytes)
        prev = self._reported_dl
        free = max(prev, int(real_dl))
        if want > free and prev > 0 and dl_cap > 0:
            ceiling = free + int(dl_cap * gap)
            if want > ceiling:
                want = ceiling
                self._dl_capped = True
        self._reported_dl = max(0, want)
        if self._size_bytes > 0:
            return self._reported_dl, max(0, self._size_bytes - self._reported_dl)
        return self._reported_dl, client_left

    def _snapshot(self):
        now = int(time.time())
        self._history.append({
            "t":  now,
            "ul": int(self._cumul_rep_ul),
            "dl": int(self._cumul_rep_dl),
        })
        if len(self._history) > HISTORY_MAX:
            self._history = _compress_history(self._history, now)

    def compute(self, real_ul, real_dl, interval, event=None, budget=None, ramp=1.0):
        self._ann_count     += 1
        self._cumul_real_ul += real_ul if real_ul < self._seen_ul else real_ul - self._seen_ul
        self._seen_ul        = real_ul
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
        new_ul, is_stag = self._calc_upload(real_ul, self._cumul_rep_dl, interval,
                                            self._ann_count, budget, ramp)
        self._is_algo_stalled = is_stag
        if new_ul < self._prev_rep_ul:
            new_ul = self._prev_rep_ul
        delta              = new_ul - self._cumul_rep_ul
        self._cumul_rep_ul = self._prev_rep_ul = new_ul
        if self._ann_count % 5 == 0:
            self._snapshot()
        return new_ul, self._cumul_rep_dl, delta, is_stag


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
        self._last_control   = 0.0
        self._control_mtime  = 0.0
        self._registry       = self._load_registry()
        self._identity       = None
        self._overrides      = {}
        self._paused_set     = set()
        self._tag_map        = {}
        self._bw_window      = []
        self._bw_lock        = threading.Lock()
        self._domain_slots   = {}
        self._desync_cache   = {}
        self._session_start  = time.time()
        self._burst_order    = {}
        self._burst_closed   = False
        self._stopped_pending = {}
        self._purge_lock     = threading.Lock()
        self._apply_config(c)
        if self._persist:
            self._load_stats()
        self._init_identity()
        self._load_control()
        self._warn_udp_trackers()
        self._stop_evt = threading.Event()
        threading.Thread(target=self._background_loop, daemon=True).start()
        mode = " [DRY-RUN]" if self._dry_run else ""
        logger.info("NewGreedy %s started - proxy listening on port %s%s",
                    VERSION, c.get("proxy", "listen_port", fallback="3456"), mode)

    def _apply_config(self, c):
        self._stats_params   = self._build_stats_params(c)
        self._max_global_r   = c.getfloat("spoofing", "max_global_ratio_per_tracker", fallback=2.5)
        self._min_interval   = c.getint("advanced", "min_announce_interval", fallback=1800)
        self._jitter_pct     = c.getfloat("advanced", "interval_jitter_pct", fallback=0.08)
        self._corrupt_p      = c.getfloat("advanced", "corrupt_field_probability", fallback=0.05)
        self._respect_iv     = c.getboolean("advanced", "respect_tracker_interval", fallback=True)
        self._cumul_ttl      = c.getint("stats", "tracker_cumul_ttl_days", fallback=30) * 86400
        self._spoof_ua       = c.getboolean("anti_detection", "spoof_user_agent", fallback=True)
        self._ua_mode        = (c.get("anti_detection", "user_agent_mode", fallback="random") or "random").strip().lower()
        self._ua_fixed       = (c.get("anti_detection", "user_agent_value", fallback="") or "").strip()
        self._spoof_pid      = c.getboolean("anti_detection", "spoof_peer_id", fallback=True)
        self._spoof_peers    = c.getboolean("anti_detection", "spoof_peers", fallback=True)
        self._spoof_port     = c.getboolean("anti_detection", "spoof_port", fallback=True)
        self._spoof_hdr      = c.getboolean("anti_detection", "spoof_headers", fallback=True)
        self._intercept_scrape = c.getboolean("anti_detection", "intercept_scrape", fallback=False)
        legacy_cluster       = c.getboolean("spoofing", "anti_clustering", fallback=True)
        self._desync         = c.getboolean("anti_detection", "desync_announces", fallback=legacy_cluster)
        self._desync_pct     = max(0.0, min(DESYNC_MAX_SPREAD,
                                            c.getfloat("anti_detection", "desync_spread_pct", fallback=0.18)))
        self._desync_gap     = max(0, c.getint("anti_detection", "desync_min_gap", fallback=45))
        self._burst          = c.getboolean("anti_detection", "startup_burst", fallback=True)
        self._burst_window   = max(0, c.getint("anti_detection", "startup_burst_window", fallback=300))
        self._burst_floor    = max(0.0, min(1.0, c.getfloat("anti_detection", "startup_ramp_floor", fallback=0.15)))
        lo, hi               = c.get("anti_detection", "port_range", fallback="6881-6999").split("-")
        self._port_lo, self._port_hi = int(lo), int(hi)
        self._wl             = [x.strip() for x in c.get("anti_detection", "tracker_whitelist", fallback="").split(",") if x.strip()]
        self._bl             = [x.strip() for x in c.get("anti_detection", "tracker_blacklist", fallback="").split(",") if x.strip()]
        self._echo_tid       = c.getboolean("anti_detection", "echo_trackerid", fallback=True)
        self._persist        = c.getboolean("stats", "persist_stats", fallback=True)
        self._auto_purge     = c.getboolean("stats", "auto_purge_stopped", fallback=True)
        self._stopped_grace  = max(0, c.getint("stats", "stopped_purge_delay", fallback=120))
        self._profile_cfg    = c.get("client", "client_profile", fallback="random")
        self._rotate_profile = c.getboolean("client", "rotate_profile_on_restart", fallback=True)
        self._identity_mode  = self._resolve_identity_mode(c)
        self._bump_p         = max(0.0, min(1.0, c.getfloat("client", "version_bump_probability",
                                                            fallback=0.10)))
        self._global_speed   = c.getfloat("bandwidth", "global_max_speed_mbps", fallback=0.0) * 1e6
        self._dl_cap         = max(0.0, c.getfloat("bandwidth", "max_download_speed_mbps",
                                                   fallback=12.0)) * 1e6
        self._dry_run        = c.getboolean("simulation", "dry_run", fallback=False)
        self._tracker_cfg    = self._build_tracker_overrides(c)
        notify_configure(
            c.get("notifications", "webhook_url", fallback=""),
            c.get("notifications", "webhook_format", fallback="generic"),
            c.get("notifications", "webhook_events", fallback=""),
        )

    def _resolve_identity_mode(self, c):
        mode = (c.get("client", "session_identity", fallback="") or "").strip().lower()
        if mode in IDENTITY_MODES:
            return mode
        if mode:
            logger.warning("config.ini [client] session_identity='%s' unknown - "
                           "expected one of %s", mode, ", ".join(sorted(IDENTITY_MODES)))
        return "rotate" if self._rotate_profile else "stable"

    def _build_tracker_overrides(self, c):
        out = {}
        for section in c.sections():
            if not section.startswith("tracker:"):
                continue
            domain = section.split(":", 1)[1].strip().lower()
            if domain:
                out[domain] = dict(c[section])
        if out:
            logger.info("Per-tracker overrides loaded for: %s", ", ".join(sorted(out)))
        return out

    def _build_stats_params(self, c, domain=None):
        def g(section, key, kind, default):
            if domain:
                ov = self._tracker_cfg.get(domain, {}) if hasattr(self, "_tracker_cfg") else {}
                if key in ov:
                    try:
                        return kind(ov[key])
                    except (ValueError, TypeError):
                        pass
            if kind is bool:
                return c.getboolean(section, key, fallback=default)
            if kind is int:
                return c.getint(section, key, fallback=default)
            if kind is float:
                return c.getfloat(section, key, fallback=default)
            return c.get(section, key, fallback=default)

        target = g("spoofing", "target_ratio", float, 1.5)
        buf    = g("spoofing", "target_ratio_buffer", float, 0.03)
        return (
            g("spoofing", "stagnation_probability", float, 0.03),
            g("spoofing", "catch_up_factor", float, 0.22),
            g("spoofing", "max_simulated_speed_mbps", float, 10.0) * 1e6,
            g("spoofing", "upload_noise_pct", float, 3.0) / 100,
            g("spoofing", "max_ratio_per_torrent", float, 3.0),
            g("spoofing", "seed_credit_mb", float, 5.0) * 1e6,
            g("advanced", "stall_announce_threshold", int, 8),
            g("advanced", "min_announces_before_stagnation", int, 10),
            buf,
            target + buf,
            g("spoofing", "auto_stop_at_target", bool, True),
            _parse_hours(g("advanced", "inject_hours", str, "0-23")),
            g("bandwidth", "circadian_night_factor", float, 0.35),
            g("bandwidth", "circadian_smoothing", bool, True),
            g("bandwidth", "per_torrent_speed_cap_mbps", float, 12.0) * 1e6,
        )

    def _init_identity(self):
        saved = None
        if self._identity_mode != "rotate":
            try:
                with open(STATS_FILE, encoding="utf-8") as f:
                    saved = json.load(f).get("_identity")
            except Exception:
                saved = None
        self._identity = SessionIdentity(self._profile_cfg, self._port_lo,
                                         self._port_hi, saved, self._identity_mode,
                                         self._bump_p)
        logger.info("[IDENTITY] announcing as %s (%s), peer_id %s..., port %d, session=%s",
                    self._identity.profile["name"], self._identity.user_agent,
                    self._identity.peer_id[:12], self._identity.port,
                    self._identity_mode)
        if self._identity.bumped_from:
            logger.info("[IDENTITY] client updated since last session: %s -> %s",
                        self._identity.bumped_from, self._identity.user_agent)
        elif self._identity_mode == "drift" and saved:
            logger.debug("[IDENTITY] same build as last session, fresh peer_id tail")
        if self._ua_mode == "fixed" and self._ua_fixed:
            logger.warning("[IDENTITY] user_agent_mode=fixed - User-Agent '%s' does not match "
                           "the peer_id prefix, which trackers can cross-check", self._ua_fixed)
        if self._desync:
            logger.info("[DESYNC] announce de-correlation active - spread %.0f%%, min gap %ds",
                        self._desync_pct * 100, self._desync_gap)
        if self._burst and self._burst_window > 0:
            logger.info("[BURST] startup window open for %ds - upload ramps from %.0f%%",
                        self._burst_window, self._burst_floor * 100)

    def _new_stats(self, domain=None):
        params = self._build_stats_params(self._cfg, domain) if domain else self._stats_params
        return TorrentStats(params)

    def _background_loop(self):
        flag = _BASE / ".shutdown"
        while not self._stop_evt.wait(1.0):
            try:
                self._sweep_stopped(time.time())
            except Exception as e:
                logger.debug("stopped sweep error: %s", e)
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

    def _note_stopped(self, ih_key, domain, now):
        if not self._auto_purge:
            return
        if self._stopped_grace <= 0:
            self._retire([(ih_key, now)], now)
            return
        with self._purge_lock:
            self._stopped_pending[ih_key] = now
        logger.debug("[STOPPED] %s@%s - retiring in %ds unless it announces again",
                     ih_key, domain, self._stopped_grace)

    def _cancel_stopped(self, ih_key):
        with self._purge_lock:
            if self._stopped_pending.pop(ih_key, None) is None:
                return
        logger.info("[RESUMED] %s - announced again before the purge delay, counters kept",
                    ih_key)

    def _sweep_stopped(self, now):
        if not self._stopped_pending:
            return
        with self._purge_lock:
            due = [(ih, ts) for ih, ts in self._stopped_pending.items()
                   if (now - ts) >= self._stopped_grace]
            for ih, _ in due:
                del self._stopped_pending[ih]
        if due:
            self._retire(due, now)

    def _retire(self, marked, now):
        removed = []
        for ih, ts in marked:
            st = self._stats.get(ih)
            if st is None or st._last_announce_ts > ts:
                continue
            del self._stats[ih]
            removed.append(ih)
            for k in [k for k in self._last_seen if k.split(KEY_SEP)[0] == ih]:
                del self._last_seen[k]
        if not removed:
            return
        if self._persist:
            self._save_stats(force=True)
        if len(removed) == 1:
            logger.info("[PURGED] %s - removed after event=stopped", removed[0])
        else:
            logger.info("[PURGED] %d torrents removed after an event=stopped burst: %s",
                        len(removed), ", ".join(sorted(removed)[:8])
                        + (" ..." if len(removed) > 8 else ""))
        notify("purged", "Torrents removed",
               f"{len(removed)} torrent(s) sent event=stopped and were purged from stats.",
               dedup_key="purged:" + ",".join(sorted(removed))[:96])

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

    def _load_control(self):
        try:
            mtime = os.path.getmtime(CONTROL_FILE)
        except OSError:
            if self._paused_set or self._tag_map or self._overrides:
                self._paused_set = set()
                self._overrides = {}
                self._tag_map = {}
                for st in self._stats.values():
                    st._paused = False
                    st._tags = []
            self._control_mtime = 0.0
            return
        if mtime == self._control_mtime:
            return
        self._control_mtime = mtime
        try:
            with open(CONTROL_FILE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.debug("control file read error: %s", e)
            return
        self._paused_set = set(data.get("paused", []))
        self._overrides  = {k: v for k, v in data.get("overrides", {}).items() if isinstance(v, dict)}
        self._tag_map    = {k: list(v) for k, v in data.get("tags", {}).items() if isinstance(v, list)}
        for ih, st in self._stats.items():
            st._paused = ih in self._paused_set
            st._tags   = self._tag_map.get(ih, [])
        applied = self._apply_overrides()
        if applied:
            logger.info("[CONTROL] reloaded - %d paused, %d override(s)",
                        len(self._paused_set), applied)

    def _apply_overrides(self):
        count = 0
        consumed = []
        for ih, ov in list(self._overrides.items()):
            st = self._stats.get(ih)
            if not st:
                continue
            count += self._apply_override_to(st, ih, ov)
            if ov.pop("cumul_rep_ul", None) is not None:
                consumed.append(ih)
                if not ov:
                    del self._overrides[ih]
        if consumed:
            self._consume_control(consumed)
        return count

    def _apply_override_to(self, st, ih, ov):
        n = 0
        if "target_ratio" in ov:
            try:
                st._target_ratio = float(ov["target_ratio"]) + st._target_buf
                st._target_reached = False
                n += 1
            except (ValueError, TypeError):
                pass
        if "cumul_rep_ul" in ov:
            try:
                val = max(0.0, float(ov["cumul_rep_ul"]))
                st._cumul_rep_ul = st._prev_rep_ul = val
                n += 1
                logger.info("[CONTROL] %s - upload counter set to %.1fM", ih, val / 1e6)
            except (ValueError, TypeError):
                pass
        return n

    def _consume_control(self, hashes):
        try:
            with open(CONTROL_FILE, encoding="utf-8") as f:
                data = json.load(f)
            overrides = data.get("overrides") if isinstance(data, dict) else None
            if not isinstance(overrides, dict):
                return
            changed = False
            for ih in hashes:
                ov = overrides.get(ih)
                if not isinstance(ov, dict) or "cumul_rep_ul" not in ov:
                    continue
                del ov["cumul_rep_ul"]
                if not ov:
                    del overrides[ih]
                changed = True
            if not changed:
                return
            data["overrides"] = overrides
            atomic_write_json(CONTROL_FILE, data)
            self._control_mtime = os.path.getmtime(CONTROL_FILE)
        except Exception as e:
            logger.debug("control consume error: %s", e)

    def _load_registry(self):
        try:
            with open(REGISTRY_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _load_stats(self):
        VALID = re.compile(r"^[0-9a-f]{6,40}$")
        try:
            with open(STATS_FILE, encoding="utf-8") as f:
                raw = json.load(f)
            schema = raw.get("_schema_version", 1)
            tc_raw = raw.get("_tracker_cumul", {}) if schema >= 7 else {}
            if schema < 7 and raw.get("_tracker_cumul"):
                logger.warning("[CUMUL_RESET] per-tracker totals from schema v%d counted each "
                               "announce's cumulative download instead of its delta - dropping "
                               "them, the aggregate guard restarts from real figures", schema)
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
                s._seen_ul       = float(d.get("seen_ul", 0))
                s._ann_count     = int(d.get("ann_count", 0))
                s._prev_rep_ul   = s._cumul_rep_ul
                s._is_net_stalled= bool(d.get("stalled", False))
                s._target_reached= bool(d.get("target_reached", False))
                s._seed_fake_dl  = float(d.get("seed_fake_dl", 0))
                s._history       = list(d.get("history", []))
                s._mode          = d.get("mode", "down")
                s._last_announce_ts = float(d.get("last_announce_ts", 0))
                s._key           = int(d.get("key", 0) or 0)
                s._size_bytes    = int(d.get("size_bytes", 0) or 0)
                s._reported_dl   = int(d.get("reported_dl", 0) or 0)
                tids = d.get("trackerids", {})
                if isinstance(tids, dict):
                    s._trackerids = {str(k2): str(v2) for k2, v2 in tids.items()
                                     if isinstance(v2, str)}
                iv = d.get("tracker_intervals", {})
                s._tracker_intervals = {k2: int(v2) for k2, v2 in iv.items()} if isinstance(iv, dict) else {}
                tr = d.get("trackers", {})
                if isinstance(tr, dict):
                    s._trackers = {
                        k2: {"ul": float(v2.get("ul", 0)), "dl": float(v2.get("dl", 0)),
                             "seen_dl": float(v2.get("seen_dl", 0))}
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
                if self._identity:
                    data["_identity"] = self._identity.as_dict()
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
                        "seen_ul":          s._seen_ul,
                        "ann_count":        s._ann_count,
                        "stalled":          s._is_net_stalled,
                        "prev_rep_ul":      s._prev_rep_ul,
                        "target_reached":   s._target_reached,
                        "seed_fake_dl":     s._seed_fake_dl,
                        "history":          _compress_history(s._history, now),
                        "mode":             s._mode,
                        "last_announce_ts": s._last_announce_ts,
                        "key":              s._key,
                        "size_bytes":       s._size_bytes,
                        "reported_dl":      s._reported_dl,
                        "trackerids":       s._trackerids,
                        "tracker_intervals": s._tracker_intervals,
                        "trackers":         s._trackers,
                    }
                for ih in purged:
                    del self._stats[ih]
                    logger.info("[AUTO-PURGE] %s - no announce for 12h+", ih)
                atomic_write_json(STATS_FILE, data)
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
            if sk.split(KEY_SEP)[-1] not in active:
                del self._swarm_leechers[sk]
        for dk in list(self._desync_cache.keys()):
            if dk.split(KEY_SEP)[0] not in active:
                del self._desync_cache[dk]
        for domain, slots in list(self._domain_slots.items()):
            for k in [k for k in slots if k not in active]:
                del slots[k]
            if not slots:
                del self._domain_slots[domain]
        if removed:
            logger.debug("[MEM_CLEAN] removed %d stale last_seen entries", removed)

    def _bandwidth_budget(self, interval):
        if self._global_speed <= 0:
            return None
        now = time.time()
        with self._bw_lock:
            self._bw_window = [(t, v) for t, v in self._bw_window if now - t < 60]
            spent = sum(v for _, v in self._bw_window)
        allowance = self._global_speed * 60
        return max(0.0, allowance - spent)

    def _bandwidth_record(self, delta):
        if self._global_speed <= 0 or delta <= 0:
            return
        with self._bw_lock:
            self._bw_window.append((time.time(), delta))

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
            notify("tracker_down", "Tracker unreachable",
                          f"{domain} returned {count} consecutive errors and is now in backoff.",
                          dedup_key="down:" + domain)

    def _record_tracker_success(self, domain):
        if domain in self._tracker_errors:
            prev_count = self._tracker_errors[domain][0]
            del self._tracker_errors[domain]
            if prev_count >= TRACKER_BACKOFF_THRESHOLD:
                logger.info("[TRACKER_UP] %s - recovered", domain)
                notify("tracker_up", "Tracker recovered",
                              f"{domain} is responding again.", dedup_key="up:" + domain)

    def _get_stats(self, ih, domain=None):
        if ih not in self._stats:
            st = self._new_stats(domain)
            meta = self._registry.get(ih)
            if isinstance(meta, dict):
                try:
                    st._size_bytes = int(meta.get("size_bytes", 0))
                    st._seed_fake_dl = float(st._size_bytes)
                except (ValueError, TypeError):
                    pass
            st._paused = ih in self._paused_set
            st._tags   = self._tag_map.get(ih, [])
            self._stats[ih] = st
            ov = self._overrides.get(ih)
            if ov:
                self._apply_override_to(st, ih, ov)
                if ov.pop("cumul_rep_ul", None) is not None:
                    if not ov:
                        del self._overrides[ih]
                    self._consume_control([ih])
        return self._stats[ih]

    def _parse_int(self, qs, key, default=0):
        try:
            return int(qs.get(key, [default])[0])
        except Exception:
            return default

    def _phase(self, ih_key, domain, tag=b""):
        salt = self._identity.salt_bytes() if self._identity else b""
        h = hashlib.sha1(salt + tag + domain.encode("utf-8", "replace") + ih_key.encode()).digest()
        return int.from_bytes(h[:4], "big") / 4294967295.0

    def _cluster_pressure(self, domain, ih_key, now):
        slots = self._domain_slots.setdefault(domain, {})
        gap = self._desync_gap
        near = 0
        if gap > 0:
            for k, ts in slots.items():
                if k != ih_key and (now - ts) < gap:
                    near += 1
        slots[ih_key] = now
        if len(slots) > DESYNC_SLOTS_MAX:
            for k in sorted(slots, key=slots.get)[:len(slots) - DESYNC_SLOTS_MAX]:
                del slots[k]
        return near

    def _burst_rank(self, st, ih_key, now):
        if not self._burst or self._burst_window <= 0:
            return -1
        if (now - self._session_start) > self._burst_window:
            if not self._burst_closed and self._burst_order:
                self._burst_closed = True
                logger.info("[BURST] startup window closed - %d torrent(s) re-announced at launch",
                            len(self._burst_order))
            return st._burst_rank
        if ih_key not in self._burst_order:
            self._burst_order[ih_key] = len(self._burst_order)
        st._burst_rank = self._burst_order[ih_key]
        return st._burst_rank

    def _burst_ramp(self, now):
        if not self._burst or self._burst_window <= 0:
            return 1.0
        elapsed = now - self._session_start
        if elapsed >= self._burst_window:
            return 1.0
        progress = max(0.0, elapsed) / float(self._burst_window)
        return self._burst_floor + (1.0 - self._burst_floor) * (progress ** 0.6)

    def _desync_factor(self, ih_key, domain, pressure=0, rank=-1, now=None):
        if not self._desync or self._desync_pct <= 0:
            return 1.0
        epoch  = int((now or time.time()) // DESYNC_EPOCH_S)
        drift  = (self._phase(ih_key, domain, str(epoch).encode()) - 0.5) * 0.2
        offset = ((self._phase(ih_key, domain) + drift) % 1.0) * self._desync_pct
        if rank >= 0:
            offset += self._desync_pct * (min(rank, BURST_RANK_MAX) / float(BURST_RANK_MAX))
        if pressure > 0:
            offset += self._desync_pct * 0.5 * min(pressure, 4)
        return 1.0 + min(DESYNC_MAX_SPREAD, offset)

    def _effective_interval(self, st, domain, elapsed, factor=1.0):
        base = self._min_interval
        if self._respect_iv:
            tiv = st._tracker_intervals.get(domain, 0)
            if tiv > 0:
                base = tiv
        interval = (max(elapsed, base) if elapsed > 0 else base) * factor
        return interval + interval * self._jitter_pct * random.uniform(-1, 1)

    def request(self, flow: http.HTTPFlow):
        url = flow.request.pretty_url
        if not self._tracker_allowed(url):
            return
        if self._intercept_scrape and "scrape" in urlparse(url).path:
            return
        if not self._is_announce(url):
            return
        domain = _tracker_domain(url)
        if self._tracker_in_backoff(domain):
            return

        now = time.time()
        if now - self._last_control > CONTROL_POLL:
            self._last_control = now
            self._load_control()

        raw_path  = flow.request.data.path
        q_start   = raw_path.find(b"?")
        raw_query = raw_path[q_start + 1:] if q_start != -1 else b""
        ih_hex    = _extract_infohash(raw_query)
        if not ih_hex or len(ih_hex) < 6:
            return
        ih_key = ih_hex[:8]
        qs     = parse_qs(urlparse(url).query, keep_blank_values=True)
        event  = qs.get("event", [""])[0]

        if event != "stopped":
            self._cancel_stopped(ih_key)

        real_ul = self._parse_int(qs, "uploaded")
        real_dl = self._parse_int(qs, "downloaded")
        left    = self._parse_int(qs, "left")

        st   = self._get_stats(ih_key, domain)
        prof = st.adopt_identity(self._identity)
        st._mode = "seed" if left == 0 else "down"
        prev_ts  = st._last_announce_ts
        st._last_announce_ts = now

        if st._size_bytes <= 0:
            st._size_bytes = max(real_dl + left, int(st._seed_fake_dl))

        seen_key = ih_key + KEY_SEP + domain
        last     = self._last_seen.get(seen_key, 0)
        elapsed  = (now - last) if last > 0 else 0
        rank     = self._burst_rank(st, ih_key, now)
        pressure = self._cluster_pressure(domain, ih_key, now)
        dfactor  = self._desync_factor(ih_key, domain, pressure, rank, now)
        self._desync_cache[seen_key] = dfactor
        interval = self._effective_interval(st, domain, elapsed, dfactor)
        self._last_seen[seen_key] = now

        swarm_key = domain + KEY_SEP + ih_key
        if swarm_key in self._swarm_leechers:
            st._leecher_count = self._swarm_leechers[swarm_key]

        ramp   = self._burst_ramp(now)
        budget = self._bandwidth_budget(interval)
        new_ul, new_dl, delta_ul, is_stag = st.compute(real_ul, real_dl, interval, event, budget, ramp)

        delta_dl = st.dl_delta(domain, real_dl)
        tc = self._tracker_cumul.setdefault(domain, {"ul": 0.0, "dl": 0.0, "ts": now})
        tc["ts"] = now
        tc["dl"] += delta_dl
        max_global = self._max_global_r
        ov = self._tracker_cfg.get(domain, {})
        if "max_global_ratio_per_tracker" in ov:
            try:
                max_global = float(ov["max_global_ratio_per_tracker"])
            except (ValueError, TypeError):
                pass
        if tc["dl"] > 0 and (tc["ul"] + delta_ul) / tc["dl"] > max_global:
            allowed_delta = max(0.0, max_global * tc["dl"] - tc["ul"])
            new_ul = max(st._prev_rep_ul, st._prev_rep_ul + allowed_delta)
            delta_ul = new_ul - st._cumul_rep_ul
            st._cumul_rep_ul = st._prev_rep_ul = new_ul
            logger.info("[GLOBAL_CAP] %s | tracker_ratio capped -> UL adjusted to %.1fM",
                        domain, new_ul / 1e6)
            notify("global_cap", "Global tracker cap reached",
                          f"{domain} hit the {max_global} aggregate ratio cap; upload was reduced.",
                          dedup_key="cap:" + domain)
        tc["ul"] += delta_ul
        st.record_tracker(domain, delta_ul, delta_dl)
        self._bandwidth_record(delta_ul)

        if st._target_reached and st._ann_count % 20 == 1:
            notify("target_reached", "Target ratio reached",
                          f"{ih_key} reached ratio {st._target_ratio:.2f} on {domain}.",
                          dedup_key="target:" + ih_key)

        gap = max(0.0, now - prev_ts) if prev_ts > 0 else 0.0
        reported_dl, reported_left = st.coherent_progress(left, real_dl, new_dl,
                                                          gap, self._dl_cap)
        if st._dl_capped:
            logger.debug("[DL_CAP] %s@%s - reported download held to %.1fM, %.0fM left",
                         ih_key, domain, reported_dl / 1e6, reported_left / 1e6)

        patches = {
            "uploaded":   str(int(new_ul)),
            "downloaded": str(reported_dl),
            "left":       str(reported_left),
        }
        raw_patches = {}
        tid = st._trackerids.get(domain, "") if self._echo_tid else ""
        if tid:
            try:
                raw_patches["trackerid"] = _pct_encode(bytes.fromhex(tid))
            except ValueError:
                st._trackerids.pop(domain, None)
        if self._spoof_port:
            patches["port"] = str(st._port)
        if self._spoof_peers:
            if event == "stopped":
                patches["numwant"] = "0"
            elif st._numwant:
                patches["numwant"] = str(st._numwant)
            else:
                st._numwant = prof.get("numwant", 200)
                patches["numwant"] = str(st._numwant)
        if self._spoof_pid:
            patches["key"] = format(st._key, "08x")
        if prof.get("sends_corrupt") and self._corrupt_p > 0 and random.random() < self._corrupt_p:
            patches["corrupt"] = str(random.randint(0, 65535))

        if not self._dry_run:
            pairs = _split_pairs(raw_query)
            existing = {k for k, _ in pairs}
            out = []
            for key, raw in pairs:
                if key == "peer_id" and self._spoof_pid:
                    out.append((key, _pct_encode(self._identity.peer_id_bytes())))
                elif key in raw_patches:
                    out.append((key, raw_patches.pop(key)))
                elif key in patches:
                    out.append((key, patches[key].encode()))
                else:
                    out.append((key, raw))
            for key, val in patches.items():
                if key not in existing:
                    out.append((key, val.encode()))
            for key, val in raw_patches.items():
                out.append((key, val))
            for key, val in prof.get("extra_params", {}).items():
                if key not in existing:
                    out.append((key, val.encode()))
            out = client_profiles.order_params(prof, out)
            path_only = raw_path[:q_start] if q_start != -1 else raw_path
            flow.request.data.path = path_only + b"?" + _build_query(out)

            if self._spoof_ua:
                flow.request.headers["User-Agent"] = (
                    self._ua_fixed if (self._ua_mode == "fixed" and self._ua_fixed)
                    else st._user_agent)
            if self._spoof_hdr:
                for hk, hv in prof.get("headers", {}).items():
                    flow.request.headers[hk] = hv

        mode  = "SEED" if left == 0 else "DOWN"
        ratio = new_ul / st._cumul_rep_dl if st._cumul_rep_dl > 0 else 0.0
        avg_d = new_ul / st._ann_count if st._ann_count > 1 else delta_ul
        eta   = int(max(0, st._cumul_rep_dl * st._target_ratio - new_ul) / avg_d) if avg_d > 0 else 0
        flags = "".join(filter(None, [
            " [STAG]"            if is_stag             else "",
            " [PAUSED]"          if st._paused          else "",
            " [STALL_NET]"       if st._is_net_stalled  else "",
            " [STALL_ALGO]"      if st._is_algo_stalled else "",
            " [TARGET_REACHED]"  if st._target_reached  else "",
            " [DRY-RUN]"         if self._dry_run       else "",
            " [BURST]"           if ramp < 0.999        else "",
            " [DESYNC]"          if pressure > 0        else "",
            " [DL_CAP]"          if st._dl_capped       else "",
            " [STOPPED]"         if event == "stopped"  else "",
        ]))

        if st._cumul_rep_dl > 0:
            logger.info("[%-4s] %-8s | DL:%7.1fM UL:%7.1fM +%6.1fM R:%.2f ETA:~%da #%d%s",
                        mode, ih_key, st._cumul_rep_dl / 1e6, new_ul / 1e6,
                        delta_ul / 1e6, ratio, eta, st._ann_count, flags)
        else:
            logger.info("[%-4s] %-8s | UL:%7.1fM +%6.1fM #%d%s",
                        mode, ih_key, new_ul / 1e6, delta_ul / 1e6, st._ann_count, flags)

        if event == "stopped":
            self._note_stopped(ih_key, domain, now)
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
            if flow.response.status_code >= 500:
                self._record_tracker_error(domain)
            else:
                self._record_tracker_success(domain)
            body = flow.response.content
            st   = self._stats.get(ih_key)
            if st is None:
                return
            if self._echo_tid:
                self._capture_trackerid(body, st, ih_key, domain)
            interval = bd_find_int(body, b"interval")
            if interval > 0 and st._tracker_intervals.get(domain) != interval:
                st._tracker_intervals[domain] = interval
                logger.debug("[INTERVAL] %s@%s - tracker interval %ds", ih_key, domain, interval)
            if interval > 0 and self._desync and not self._dry_run:
                self._rewrite_interval(flow, body, st, ih_key, domain, interval)
            incomplete = bd_find_int(body, b"incomplete")
            if incomplete >= 0:
                self._swarm_leechers[domain + KEY_SEP + ih_key] = incomplete
                st._leecher_count = incomplete
                if incomplete == 0:
                    logger.debug("[SWARM] %s@%s - 0 leechers, stagnation forced", ih_key, domain)
        except Exception:
            pass

    def _capture_trackerid(self, body, st, ih_key, domain):
        tid = bd_top_str(body, b"trackerid", TRACKERID_MAX)
        if not tid:
            return
        hexed = tid.hex()
        if st._trackerids.get(domain) == hexed:
            return
        st._trackerids[domain] = hexed
        if len(st._trackerids) > 16:
            for k in list(st._trackerids)[:-16]:
                del st._trackerids[k]
        logger.debug("[TRACKERID] %s@%s - stored, echoed from the next announce on",
                     ih_key, domain)

    def _rewrite_interval(self, flow, body, st, ih_key, domain, interval):
        if not body.startswith(b"d") or b"14:failure reason" in body:
            return
        marker = body.find(b"8:interval")
        peers = body.find(b"5:peers")
        if marker == -1 or (peers != -1 and peers < marker):
            return
        seen_key = ih_key + KEY_SEP + domain
        factor = self._desync_cache.get(seen_key)
        if factor is None:
            factor = self._desync_factor(ih_key, domain, 0, st._burst_rank)
        floor = bd_find_int(body, b"min interval")
        if floor > interval:
            return
        shifted = int(round(interval * factor))
        shifted = max(interval, floor, min(shifted, int(interval * (1.0 + DESYNC_MAX_SPREAD))))
        if shifted == interval:
            return
        patched = bd_replace_int(body, b"interval", shifted)
        if patched == body:
            return
        flow.response.content = patched
        logger.debug("[DESYNC] %s@%s - announce interval %ds -> %ds", ih_key, domain, interval, shifted)

    def done(self):
        if self._persist:
            self._save_stats(force=True)
        logger.info("NewGreedy %s stopping - stats saved.", VERSION)


addons = [NewGreedyAddon()]
