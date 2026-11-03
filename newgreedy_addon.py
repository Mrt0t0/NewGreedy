#!/usr/bin/env python3
"""NewGreedy — core addon"""
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
import os, random, time, json, hashlib, logging, threading
from logging.handlers import RotatingFileHandler
from urllib.parse import urlparse, parse_qs, unquote_to_bytes, quote_from_bytes
from mitmproxy import http
import pathlib

ADDON_VERSION = "v1.7.8"
_BASE         = pathlib.Path(__file__).parent.resolve()

# mitmproxy reloads this file as soon as it changes on disk, while the other modules
# stay loaded: after an update, mixed versions must not run - they fail closed instead
_IMPORT_ERROR = ""
try:
    import _version
    if getattr(_version, "VERSION", "") != ADDON_VERSION:
        raise ImportError(f"engine files are {ADDON_VERSION} but {getattr(_version, 'VERSION', '?')} "
                          f"is still loaded - restart NewGreedy to finish the update")
    from _version import (VERSION, bd_find_int, bd_replace_int, bd_top_str, notify,
                          notify_configure)
    import client_profiles
    import config_schema as cs
    import datastore
    import backups
    import engine_model
    from engine_model import (SessionIdentity, TorrentStats, parse_hours, tracker_domain as _tracker_domain,
                              domain_match as _domain_match)
    from engine_state import StateMixin, STATS_FILE, CONFIG_FILE, KEY_SEP, MIN_HASH_LEN, _short
    _DATA = datastore.DATA_DIR
except Exception as _e:
    _IMPORT_ERROR = str(_e) or type(_e).__name__
    VERSION = ADDON_VERSION
    _DATA = pathlib.Path(os.environ.get("NEWGREEDY_DATA_DIR") or _BASE).resolve()

    class StateMixin:  # the engine is replaced by FailClosedAddon in this case
        pass

LOG_FILE        = str(_DATA / "newgreedy.log")

LOG_MAX_BYTES   = 10 * 1024 * 1024
LOG_BACKUPS     = 3
LAST_SEEN_TTL   = 86400
MEM_CLEAN_EVERY = 3600
CONTROL_POLL    = 5.0
CONFIG_POLL     = 5.0
STATUS_EVERY    = 5.0

_LOG_FORMAT = "%(asctime)s %(message)s"

logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT, handlers=[logging.StreamHandler()])
logging.getLogger("passlib").setLevel(logging.ERROR)

logger = logging.getLogger("NewGreedy")
logger.setLevel(logging.INFO)

TRACKER_BACKOFF_THRESHOLD = 3
TRACKER_BACKOFF_MAX_S     = 1800
BW_MIN_WINDOW_S           = 60
BW_KEEP_S                 = 86400

DESYNC_MAX_SPREAD  = 0.45
DESYNC_EPOCH_S     = 3600
DESYNC_SLOTS_MAX   = 256
BURST_RANK_MAX     = 24
IDENTITY_MODES     = ("stable", "drift", "rotate")
TRACKERID_MAX      = 128


def _file_handler():
    for h in logger.handlers:
        if isinstance(h, RotatingFileHandler):
            try:
                if os.path.abspath(h.baseFilename) == os.path.abspath(LOG_FILE):
                    return h
            except Exception:
                continue
    return None


def _ensure_file_handler(level=None):
    fh = _file_handler()
    if fh is None:
        try:
            fh = RotatingFileHandler(LOG_FILE, maxBytes=LOG_MAX_BYTES,
                                     backupCount=LOG_BACKUPS, encoding="utf-8")
        except OSError:
            return
        fh.setFormatter(logging.Formatter(_LOG_FORMAT))
        fh.setLevel(logging.DEBUG)
        logger.addHandler(fh)
    if level is not None:
        logger.setLevel(level)


_ensure_file_handler()


def _load_cfg():
    return cs.load(CONFIG_FILE)


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


class NewGreedyAddon(StateMixin):
    def __init__(self):
        self._cfg            = _load_cfg()
        c                    = self._cfg
        self._lock           = threading.RLock()
        self._stats          = {}
        self._tracker_cumul  = {}
        self._tracker_errors = {}
        self._trk_state      = {}
        self._last_seen      = {}
        self._swarm_leechers = {}
        self._save_lock      = threading.Lock()
        self._last_flush_ts  = 0.0
        self._flush_interval = 60.0
        self._dirty          = False
        self._force_save     = False
        self._control_mtime  = 0.0
        self._registry_mtime = 0.0
        self._cfg_mtime      = self._mtime(CONFIG_FILE)
        self._cfg_loaded_ts  = time.time()
        self._registry       = self._load_registry()
        self._identity       = None
        self._overrides      = {}
        self._paused_set     = set()
        self._tag_map        = {}
        self._consume_queue  = set()
        self._udp_warned     = set()
        self._bw_window      = []
        self._bw_lock        = threading.Lock()
        self._domain_slots   = {}
        self._desync_cache   = {}
        self._session_start  = time.time()
        self._burst_order    = {}
        self._burst_closed   = False
        self._stopped_pending = {}
        self._announces      = 0
        self._last_announce  = {}
        self._tracker_cfg    = {}
        self._apply_config(c)
        if self._persist:
            self._load_stats()
        self._init_identity()
        self._load_control()
        self._warn_udp_trackers()
        self._stop_evt = threading.Event()
        self._write_status()
        threading.Thread(target=self._background_loop, daemon=True).start()
        mode = " [DRY-RUN]" if self._dry_run else ""
        logger.info("NewGreedy %s started - proxy listening on %s:%s%s", VERSION,
                    cs.get(c, "proxy", "listen_host"), cs.get(c, "proxy", "listen_port"), mode)

    @staticmethod
    def _mtime(path):
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    def _apply_config(self, c):
        g = lambda s, k: cs.get(c, s, k)  # noqa: E731
        self._tracker_cfg    = self._build_tracker_overrides(c)
        self._stats_params   = self._build_stats_params(c)
        self._max_global_r   = g("spoofing", "max_global_ratio_per_tracker")
        self._min_interval   = g("advanced", "min_announce_interval")
        self._jitter_pct     = g("advanced", "interval_jitter_pct")
        self._corrupt_p      = g("advanced", "corrupt_field_probability")
        self._respect_iv     = g("advanced", "respect_tracker_interval")
        self._cumul_ttl      = g("stats", "tracker_cumul_ttl_days") * 86400
        self._spoof_ua       = g("anti_detection", "spoof_user_agent")
        self._ua_mode        = g("anti_detection", "user_agent_mode")
        self._ua_fixed       = (g("anti_detection", "user_agent_value") or "").strip()
        self._spoof_pid      = g("anti_detection", "spoof_peer_id")
        self._spoof_peers    = g("anti_detection", "spoof_peers")
        self._spoof_port     = g("anti_detection", "spoof_port")
        self._spoof_hdr      = g("anti_detection", "spoof_headers")
        legacy_cluster       = g("spoofing", "anti_clustering")
        self._desync         = cs.get(c, "anti_detection", "desync_announces", legacy_cluster)
        self._desync_pct     = max(0.0, min(DESYNC_MAX_SPREAD, g("anti_detection", "desync_spread_pct")))
        self._desync_gap     = max(0, g("anti_detection", "desync_min_gap"))
        self._burst          = g("anti_detection", "startup_burst")
        self._burst_window   = max(0, g("anti_detection", "startup_burst_window"))
        self._burst_floor    = max(0.0, min(1.0, g("anti_detection", "startup_ramp_floor")))
        self._port_lo, self._port_hi = g("anti_detection", "port_range")
        self._wl             = g("anti_detection", "tracker_whitelist")
        self._bl             = g("anti_detection", "tracker_blacklist")
        self._echo_tid       = g("anti_detection", "echo_trackerid")
        self._persist        = g("stats", "persist_stats")
        self._auto_purge     = g("stats", "auto_purge_stopped")
        self._stopped_grace  = max(0, g("stats", "stopped_purge_delay"))
        self._inactive_hours = g("stats", "inactive_purge_hours")
        self._trash_days     = g("stats", "trash_retention_days")
        engine_model.HIST["max"] = g("stats", "history_max_points")
        engine_model.HIST["retention"] = g("stats", "history_retention_days") * 86400
        self._profile_cfg    = g("client", "client_profile")
        self._rotate_profile = g("client", "rotate_profile_on_restart")
        self._identity_mode  = self._resolve_identity_mode(c)
        self._bump_p         = max(0.0, min(1.0, g("client", "version_bump_probability")))
        self._global_speed   = g("bandwidth", "global_max_speed_mbps") * 1e6
        self._dl_cap         = max(0.0, g("bandwidth", "max_download_speed_mbps")) * 1e6
        self._dry_run        = g("simulation", "dry_run")
        self._backup_cfg     = (g("backup", "auto_backup"), g("backup", "backup_interval_hours"),
                                g("backup", "backup_keep"))
        level = getattr(logging, g("advanced", "log_level"), logging.INFO)
        _ensure_file_handler(level)
        notify_configure(
            g("notifications", "webhook_url"),
            g("notifications", "webhook_format"),
            g("notifications", "webhook_events"),
            g("notifications", "webhook_chat_id"),
        )

    def _resolve_identity_mode(self, c):
        mode = cs.get(c, "client", "session_identity")
        if mode in IDENTITY_MODES:
            return mode
        return "rotate" if self._rotate_profile else "stable"

    def _build_tracker_overrides(self, c):
        out = {}
        for section in c.sections():
            if not section.startswith("tracker:"):
                continue
            domain = section.split(":", 1)[1].strip().lower()
            if not domain:
                continue
            ov = {}
            for key, raw in c[section].items():
                if key not in cs.TRACKER_KEYS:
                    logger.warning("config.ini [%s] %s is not a per-tracker setting - ignored", section, key)
                    continue
                value, problem = cs.tracker_value(key, raw)
                if problem:
                    logger.warning("config.ini [%s] %s = '%s' %s - global value used",
                                   section, key, raw, problem)
                    continue
                ov[key] = value
            out[domain] = ov
        if out and out.keys() != self._tracker_cfg.keys():
            logger.info("Per-tracker overrides loaded for: %s", ", ".join(sorted(out)))
        return out

    def _override(self, domain, key, default):
        ov = self._tracker_cfg.get(domain or "", {})
        return ov[key] if key in ov else default

    def _build_stats_params(self, c, domain=None):
        def g(section, key):
            return self._override(domain, key, cs.get(c, section, key))

        target = g("spoofing", "target_ratio")
        buf    = g("spoofing", "target_ratio_buffer")
        return (
            g("spoofing", "stagnation_probability"),
            g("spoofing", "catch_up_factor"),
            g("spoofing", "max_simulated_speed_mbps") * 1e6,
            g("spoofing", "upload_noise_pct") / 100,
            g("spoofing", "max_ratio_per_torrent"),
            g("spoofing", "seed_credit_mb") * 1e6,
            g("advanced", "stall_announce_threshold"),
            g("advanced", "min_announces_before_stagnation"),
            buf,
            target + buf,
            g("spoofing", "auto_stop_at_target"),
            parse_hours(g("advanced", "inject_hours")),
            g("bandwidth", "circadian_night_factor"),
            g("bandwidth", "circadian_smoothing"),
            g("bandwidth", "per_torrent_speed_cap_mbps") * 1e6,
        )

    def _params_for(self, domain, cache=None):
        if not domain or domain not in self._tracker_cfg:
            return self._stats_params
        if cache is not None and domain in cache:
            return cache[domain]
        params = self._build_stats_params(self._cfg, domain)
        if cache is not None:
            cache[domain] = params
        return params

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
        st = TorrentStats(self._params_for(domain))
        st._domain = domain or ""
        return st

    # ── background work: everything that touches files runs here, off the proxy loop ──

    def _background_loop(self):
        due = {}

        def every(name, period, fn, *args):
            now = time.time()
            if now - due.get(name, 0) < period:
                return
            due[name] = now
            try:
                fn(*args)
            except Exception as e:
                logger.debug("background task %s failed: %s", name, e, exc_info=True)

        while not self._stop_evt.wait(1.0):
            every("shutdown", 0, self._check_shutdown_flag)
            every("stopped", 0, self._sweep_stopped)
            every("purges", 0, self._apply_purges)
            every("import", 0, self._apply_import)
            every("control", CONTROL_POLL, self._reload_control)
            every("config", CONFIG_POLL, self._maybe_reload_config)
            every("registry", CONFIG_POLL, self._maybe_reload_registry)
            every("inactive", 60, self._purge_inactive)
            every("memory", MEM_CLEAN_EVERY, self._clean_memory)
            every("trash", 3600, self._clean_trash)
            every("backup", 60, self._auto_backup)
            every("save", 0, self._save_if_due)
            every("status", STATUS_EVERY, self._write_status)

    def _check_shutdown_flag(self):
        flag = datastore.SHUTDOWN_FLAG
        if not flag.exists():
            return
        logger.info("Shutdown signal received - flushing stats")
        self._save_stats(force=True)
        try:
            flag.unlink()
        except OSError:
            pass

    def _save_if_due(self):
        if self._force_save:
            self._force_save = False
            self._save_stats(force=True)
        elif self._dirty:
            self._save_stats()

    def _request_save(self):
        self._dirty = True
        self._force_save = True

    def _auto_backup(self):
        enabled, hours, keep = self._backup_cfg
        backups.maybe_auto(enabled, hours, keep)

    def _clean_trash(self):
        n = datastore.trash_clean(self._trash_days)
        if n:
            logger.info("[TRASH] %d entr%s older than %d day(s) deleted for good",
                        n, "y" if n == 1 else "ies", self._trash_days)

    def _write_status(self, state="running", error=""):
        with self._lock:
            trackers = {d: dict(v, backoff=self._tracker_in_backoff(d))
                        for d, v in self._trk_state.items()}
            status = {
                "state": state, "error": error, "version": VERSION, "pid": os.getpid(),
                "started": self._session_start, "announces": self._announces,
                "torrents": len(self._stats), "last_announce": dict(self._last_announce),
                "trackers": trackers, "dry_run": self._dry_run,
                "config_loaded": self._cfg_loaded_ts,
                "client": self._identity.profile["name"] if self._identity else "",
            }
        datastore.write_status(status)

    def _maybe_reload_config(self):
        mtime = self._mtime(CONFIG_FILE)
        if not mtime or mtime == self._cfg_mtime:
            return
        self._cfg_mtime = mtime
        c = _load_cfg()
        old = self._cfg
        with self._lock:
            self._cfg = c
            self._apply_config(c)
            cache = {}
            for ih, st in self._stats.items():
                before = st._base_target
                st.set_params(self._params_for(st._domain, cache))
                if abs(before - st._base_target) > 1e-9:
                    st._target_reached = False
            self._reapply_targets()
            n = len(self._stats)
        self._cfg_loaded_ts = time.time()
        pending = sorted(f"{s}.{k}" for s, k in cs.RESTART_KEYS
                         if old.get(s, k, fallback=None) != c.get(s, k, fallback=None))
        logger.info("[CONFIG] config.ini reloaded - applied to %d torrent(s)%s", n,
                    f"; restart needed for {', '.join(pending)}" if pending else "")


    def _clean_memory(self):
        now = time.time()
        with self._lock:
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
        # an announce covers `interval` seconds of upload: the shared uplink is checked
        # over that same span, not over the last minute only
        window = max(BW_MIN_WINDOW_S, float(interval))
        now = time.time()
        with self._bw_lock:
            self._bw_window = [(t, v) for t, v in self._bw_window if now - t < BW_KEEP_S]
            spent = sum(v for t, v in self._bw_window if now - t < window)
        return max(0.0, self._global_speed * window - spent)

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
        if self._bl and _domain_match(domain, self._bl):
            return False
        if self._wl:
            return _domain_match(domain, self._wl)
        return True

    def _trk(self, domain):
        return self._trk_state.setdefault(domain, {"last_ts": 0, "last_code": "", "errors": 0})

    def _tracker_in_backoff(self, domain) -> bool:
        err = self._tracker_errors.get(domain)
        if not err:
            return False
        count, last_ts = err
        if count < TRACKER_BACKOFF_THRESHOLD:
            return False
        backoff = min(300 * (2 ** (count - TRACKER_BACKOFF_THRESHOLD)), TRACKER_BACKOFF_MAX_S)
        return (time.time() - last_ts) < backoff

    def _record_tracker_error(self, domain, reason=""):
        err = self._tracker_errors.get(domain, (0, 0))
        count = err[0] + 1
        self._tracker_errors[domain] = (count, time.time())
        self._trk(domain)["errors"] = count
        if count == TRACKER_BACKOFF_THRESHOLD:
            logger.warning("[TRACKER_DOWN] %s - %d consecutive errors, entering backoff%s",
                           domain, count, f" ({reason})" if reason else "")
            notify("tracker_down", "Tracker unreachable",
                   f"{domain} failed {count} times in a row{' (' + reason + ')' if reason else ''} "
                   f"and is now in backoff.", dedup_key="down:" + domain)

    def _record_tracker_success(self, domain):
        self._trk(domain)["errors"] = 0
        if domain in self._tracker_errors:
            prev_count = self._tracker_errors[domain][0]
            del self._tracker_errors[domain]
            if prev_count >= TRACKER_BACKOFF_THRESHOLD:
                logger.info("[TRACKER_UP] %s - recovered", domain)
                notify("tracker_up", "Tracker recovered",
                       f"{domain} is responding again.", dedup_key="up:" + domain)

    def _adopt_legacy(self, ih):
        """Stats saved by an older version under the 8-digit prefix: moved to the full
        info-hash, unless the registry says that entry belongs to another torrent."""
        legacy = ih[:MIN_HASH_LEN]
        if legacy == ih or legacy not in self._stats:
            return None
        meta = self._registry.get(legacy)
        if isinstance(meta, dict) and meta.get("info_hash") and meta["info_hash"] != ih:
            return None
        st = self._stats.pop(legacy)
        self._stats[ih] = st
        self._dirty = True
        logger.info("[MIGRATE] %s - stats now keyed by the full info-hash", legacy)
        return st

    def _get_stats(self, ih, domain=None):
        st = self._stats.get(ih) or self._adopt_legacy(ih)
        if st is not None:
            return st
        st = self._new_stats(domain)
        self._size_from_registry(st, datastore.registry_meta(self._registry, ih))
        if st._size_bytes > 0:
            st._seed_fake_dl = float(st._size_bytes)
        st._paused = datastore.alias_key(self._paused_set, ih) is not None
        tk = datastore.alias_key(self._tag_map, ih)
        st._tags   = self._tag_map[tk] if tk else []
        self._stats[ih] = st
        ok = datastore.alias_key(self._overrides, ih)
        ov = self._overrides.get(ok) if ok else None
        if ov:
            self._apply_override_to(st, ih, ov)
            if ov.pop("cumul_rep_ul", None) is not None:
                if not ov:
                    del self._overrides[ok]
                self._consume_queue.add(ok)
        return st

    def _parse_int(self, qs, key, default=0):
        try:
            return int(qs.get(key, [default])[0])
        except Exception:
            return default

    def _phase(self, ih_key, domain, tag=b""):
        salt = self._identity.salt_bytes() if self._identity else b""
        h = hashlib.sha1(salt + tag + domain.encode("utf-8", "replace")
                         + ih_key[:MIN_HASH_LEN].encode()).digest()
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
        with self._lock:
            self._request(flow)

    def _request(self, flow):
        url = flow.request.pretty_url
        if not self._tracker_allowed(url):
            return
        if not self._is_announce(url):
            return
        domain = _tracker_domain(url)
        # a tracker in backoff still gets a rewritten announce: passing it through would
        # hand it the real peer_id and an upload figure lower than the last one it saw

        now = time.time()
        raw_path  = flow.request.data.path
        q_start   = raw_path.find(b"?")
        raw_query = raw_path[q_start + 1:] if q_start != -1 else b""
        ih_hex    = _extract_infohash(raw_query)
        if not ih_hex or len(ih_hex) < MIN_HASH_LEN:
            return
        ih_key = ih_hex
        self._announces += 1
        self._last_announce = {"ts": now, "tracker": domain, "hash": _short(ih_key), "event": ""}
        self._trk(domain)["last_ts"] = now
        qs     = parse_qs(urlparse(url).query, keep_blank_values=True)
        event  = qs.get("event", [""])[0]
        self._last_announce["event"] = event

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
        elif left > st._size_bytes:
            st._size_bytes = left

        seen_key = ih_key + KEY_SEP + domain
        last     = self._last_seen.get(seen_key, 0)
        elapsed  = (now - last) if last > 0 else 0
        rank     = self._burst_rank(st, ih_key, now)
        pressure = self._cluster_pressure(domain, ih_key, now)
        dfactor  = self._desync_factor(ih_key, domain, pressure, rank, now)
        self._desync_cache[seen_key] = dfactor
        interval = self._effective_interval(st, domain, elapsed, dfactor)
        self._last_seen[seen_key] = now
        # upload is credited on the torrent's own clock: with several trackers, each
        # announce only covers the time since the previous one, whatever its tracker
        # and a torrent seen for the first time has no upload behind it yet (event=started)
        interval = min(interval, max(0.0, now - prev_ts)) if prev_ts > 0 else 0.0

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
        max_global = self._override(domain, "max_global_ratio_per_tracker", self._max_global_r)
        if tc["dl"] > 0 and (tc["ul"] + delta_ul) / tc["dl"] > max_global:
            # compute() already moved the counters: only the increment it added is cut
            allowed_delta = min(delta_ul, max(0.0, max_global * tc["dl"] - tc["ul"]))
            if allowed_delta < delta_ul:
                new_ul = (new_ul - delta_ul) + allowed_delta
                delta_ul = allowed_delta
                st._cumul_rep_ul = st._prev_rep_ul = new_ul
                logger.info("[GLOBAL_CAP] %s | tracker_ratio capped -> UL adjusted to %.1fM",
                            domain, new_ul / 1e6)
                notify("global_cap", "Global tracker cap reached",
                       f"{domain} hit the {max_global} aggregate ratio cap; upload was reduced.",
                       dedup_key="cap:" + domain)
        tc["ul"] += delta_ul
        st.record_tracker(domain, delta_ul, delta_dl)
        self._bandwidth_record(delta_ul)

        if st._just_reached:
            st._just_reached = False
            shown = st._target_ratio - st._target_buf
            meta = datastore.registry_meta(self._registry, ih_key)
            name = meta.get("name", "") if isinstance(meta, dict) else ""
            logger.info("[TARGET_REACHED] %s@%s - ratio target %.2f reached, injection stopped",
                        _short(ih_key), domain, shown)
            notify("target_reached", "Target ratio reached",
                   f"{name or _short(ih_key)} reached ratio {shown:.2f} on {domain}.",
                   dedup_key="target:" + _short(ih_key))

        gap = max(0.0, now - prev_ts) if prev_ts > 0 else 0.0
        reported_dl, reported_left = st.coherent_progress(left, real_dl, new_dl,
                                                          gap, self._dl_cap)
        if st._dl_capped:
            logger.debug("[DL_CAP] %s@%s - reported download held to %.1fM, %.0fM left",
                         _short(ih_key), domain, reported_dl / 1e6, reported_left / 1e6)

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
                        mode, _short(ih_key), st._cumul_rep_dl / 1e6, new_ul / 1e6,
                        delta_ul / 1e6, ratio, eta, st._ann_count, flags)
        else:
            logger.info("[%-4s] %-8s | UL:%7.1fM +%6.1fM #%d%s",
                        mode, _short(ih_key), new_ul / 1e6, delta_ul / 1e6, st._ann_count, flags)

        if event == "stopped":
            self._note_stopped(ih_key, domain, now)
        self._dirty = True

    def response(self, flow: http.HTTPFlow):
        with self._lock:
            self._response(flow)

    def error(self, flow: http.HTTPFlow):
        url = flow.request.pretty_url
        if not self._is_announce(url) or not self._tracker_allowed(url):
            return
        domain = _tracker_domain(url)
        reason = str(flow.error.msg if flow.error else "connection error")[:120]
        with self._lock:
            self._trk(domain)["last_code"] = "error: " + reason
            self._record_tracker_error(domain, reason)

    def _response(self, flow):
        url = flow.request.pretty_url
        if not self._is_announce(url):
            return
        domain    = _tracker_domain(url)
        raw_path  = flow.request.data.path
        q_start   = raw_path.find(b"?")
        raw_query = raw_path[q_start + 1:] if q_start != -1 else b""
        ih_hex    = _extract_infohash(raw_query)
        if not ih_hex or len(ih_hex) < MIN_HASH_LEN:
            return
        ih_key = ih_hex
        try:
            self._trk(domain)["last_code"] = str(flow.response.status_code)
            if flow.response.status_code >= 500:
                self._record_tracker_error(domain, f"HTTP {flow.response.status_code}")
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
                logger.debug("[INTERVAL] %s@%s - tracker interval %ds", _short(ih_key), domain, interval)
            if interval > 0 and self._desync and not self._dry_run:
                self._rewrite_interval(flow, body, st, ih_key, domain, interval)
            incomplete = bd_find_int(body, b"incomplete")
            if incomplete >= 0:
                self._swarm_leechers[domain + KEY_SEP + ih_key] = incomplete
                st._leecher_count = incomplete
                if incomplete == 0:
                    logger.debug("[SWARM] %s@%s - 0 leechers, stagnation forced", _short(ih_key), domain)
        except Exception as e:
            logger.debug("response handling failed for %s@%s: %s", _short(ih_key), domain, e, exc_info=True)

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
                     _short(ih_key), domain)

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
        logger.debug("[DESYNC] %s@%s - announce interval %ds -> %ds", _short(ih_key), domain, interval, shifted)

    def done(self):
        self._stop_evt.set()
        self._flush_consume_queue()
        self._save_stats(force=True)
        # mitmproxy may start the replacement addon before this one is done: keep its status
        if datastore.read_status().get("started") == self._session_start:
            self._write_status(state="stopped")
        logger.info("NewGreedy %s stopping - stats saved.", VERSION)


class FailClosedAddon:
    """Loaded instead of the engine when it cannot start: announces are answered with a
    tracker failure that names the problem, instead of reaching the tracker unmodified."""

    def __init__(self, reason):
        self.reason = reason
        # a warning, not an error: mitmdump quits when an error is logged while it starts,
        # and announces would then fail with a refused connection instead of this message
        logger.warning("[ENGINE_FAILED] %s - announces are blocked until NewGreedy is fixed "
                       "and restarted", reason)
        # written by hand: when the imports failed, datastore and notify are not loaded
        status = {"state": "failed", "error": reason, "version": ADDON_VERSION,
                  "pid": os.getpid(), "started": time.time(), "heartbeat": time.time()}
        try:
            tmp = _DATA / ".addon_status.json.tmp"
            tmp.write_text(json.dumps(status), encoding="utf-8")
            os.replace(tmp, _DATA / ".addon_status.json")
        except Exception:
            pass
        try:
            from _version import notify as _notify
            _notify("engine_failed", "NewGreedy engine failed", reason, dedup_key="engine_failed")
        except Exception:
            pass

    def request(self, flow: http.HTTPFlow):
        path = urlparse(flow.request.pretty_url).path
        if "announce" not in path or "scrape" in path:
            return
        msg = ("NewGreedy engine not running: " + self.reason)[:300].encode("utf-8", "replace")
        body = b"d14:failure reason" + str(len(msg)).encode() + b":" + msg + b"e"
        flow.response = http.Response.make(200, body, {"Content-Type": "text/plain"})


def _make_addon():
    if _IMPORT_ERROR:
        return FailClosedAddon(_IMPORT_ERROR)
    try:
        return NewGreedyAddon()
    except Exception as e:
        logger.warning("engine start failed", exc_info=True)
        return FailClosedAddon(f"{type(e).__name__}: {e}")


addons = [_make_addon()]
