#!/usr/bin/env python3
"""NewGreedy — engine state: purges, control file, registry and stats persistence"""
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
import json
import logging
import os
import re
import time

import datastore
from _version import notify, atomic_write_json
from engine_model import TorrentStats, compress_history, tracker_domain

logger = logging.getLogger("NewGreedy")

SCHEMA_VER    = 7
STATS_FILE    = str(datastore.STATS_FILE)
CONTROL_FILE  = str(datastore.CONTROL_FILE)
REGISTRY_FILE = str(datastore.REGISTRY_FILE)
CONFIG_FILE   = str(datastore.CONFIG_FILE)
KEY_SEP       = "|"
MIN_HASH_LEN  = datastore.SHORT_HASH_LEN
STARTUP_PURGE_GRACE = 3600


def _short(ih: str) -> str:
    """Info-hash as shown in the log: the first 8 hex digits."""
    return ih[:MIN_HASH_LEN]


class StateMixin:
    """Purges, control file, registry and stats persistence of the engine."""

    def _note_stopped(self, ih_key, domain, now):
        if not self._auto_purge:
            return
        self._stopped_pending[ih_key] = now
        logger.debug("[STOPPED] %s@%s - retiring in %ds unless it announces again",
                     _short(ih_key), domain, self._stopped_grace)

    def _cancel_stopped(self, ih_key):
        if self._stopped_pending.pop(ih_key, None) is None:
            return
        logger.info("[RESUMED] %s - announced again before the purge delay, counters kept",
                    _short(ih_key))

    def _sweep_stopped(self):
        if not self._stopped_pending:
            return
        now = time.time()
        with self._lock:
            due = [(ih, ts) for ih, ts in self._stopped_pending.items()
                   if (now - ts) >= self._stopped_grace]
            for ih, _ in due:
                del self._stopped_pending[ih]
        if due:
            self._retire(due, now)

    def _pop_stats(self, hashes, now):
        """Remove torrents from memory; returns their serialised entries. Caller holds the lock."""
        out = {}
        for ih in hashes:
            st = self._stats.pop(ih, None)
            if st is None:
                continue
            out[ih] = self._serialize(st, now)
            for k in [k for k in self._last_seen if k.split(KEY_SEP)[0] == ih]:
                del self._last_seen[k]
        return out

    def _to_trash(self, entries, reason):
        if not entries or self._trash_days <= 0:
            return
        try:
            datastore.trash_add(entries, reason)
        except Exception as e:
            logger.warning("[TRASH] could not keep %d purged torrent(s): %s", len(entries), e)

    def _retire(self, marked, now):
        with self._lock:
            keep = [ih for ih, ts in marked
                    if ih in self._stats and self._stats[ih]._last_announce_ts <= ts]
            removed = self._pop_stats(keep, now)
        if not removed:
            return
        self._to_trash(removed, "stopped")
        self._request_save()
        names = sorted(_short(ih) for ih in removed)
        if len(names) == 1:
            logger.info("[PURGED] %s - removed after event=stopped", names[0])
        else:
            logger.info("[PURGED] %d torrents removed after an event=stopped burst: %s",
                        len(names), ", ".join(names[:8]) + (" ..." if len(names) > 8 else ""))
        notify("purged", "Torrents removed",
               f"{len(names)} torrent(s) sent event=stopped and were purged from stats.",
               dedup_key="purged:" + ",".join(names)[:96])

    def _purge_inactive(self):
        if self._inactive_hours <= 0:
            return
        now = time.time()
        if now - self._session_start < max(STARTUP_PURGE_GRACE, 2 * self._min_interval):
            return
        cutoff = now - self._inactive_hours * 3600
        with self._lock:
            old = [ih for ih, s in self._stats.items()
                   if 0 < s._last_announce_ts < cutoff and s._ann_count >= 5]
            removed = self._pop_stats(old, now)
        if not removed:
            return
        self._to_trash(removed, "inactive")
        self._request_save()
        for ih in sorted(removed):
            logger.info("[AUTO-PURGE] %s - no announce for %dh+", _short(ih), self._inactive_hours)

    def _apply_purges(self):
        hashes = datastore.take_purges()
        if not hashes:
            return
        with self._lock:
            removed = self._pop_stats(hashes, time.time())
        for ih in sorted(removed):
            logger.info("[PURGE_SYNC] %s - removed from memory (web purge)", _short(ih))
        if removed:
            self._request_save()

    def _apply_import(self):
        imp = datastore.take_stats_import()
        if not imp:
            return
        entries = imp.get("entries") if isinstance(imp.get("entries"), dict) else {}
        loaded = self._stats_from_raw(entries)
        busy = []
        with self._lock:
            if imp.get("mode") == "replace":
                self._stats = loaded
                self._tracker_cumul = self._cumul_from_raw(imp.get("_tracker_cumul", {}), SCHEMA_VER)
                self._last_seen.clear()
                self._stopped_pending.clear()
                msg = f"[RESTORE] stats replaced from a backup - {len(loaded)} torrent(s)"
            else:
                added = [ih for ih in loaded if ih not in self._stats]
                for ih in added:
                    self._stats[ih] = loaded[ih]
                busy = [ih for ih in loaded if ih not in added]
                msg = f"[RESTORE] {len(added)} torrent(s) brought back from the trash"
            self._apply_control_to_stats()
        if imp.get("mode") != "replace" and busy:
            # the torrent announced again since it was purged: its new entry is kept and the
            # trashed one goes back to the trash instead of being dropped
            self._to_trash({ih: entries[ih] for ih in busy if ih in entries}, "restore-conflict")
            logger.warning("[RESTORE] %s - announced again since it was purged, the trashed "
                           "entry stays in the trash", ", ".join(_short(ih) for ih in busy))
        logger.info(msg)
        self._request_save()

    def _warn_udp_trackers(self):
        udp_domains = set()
        for meta in self._registry.values():
            if not isinstance(meta, dict):
                continue
            for url in meta.get("udp_trackers", []):
                d = tracker_domain(url)
                if d and d not in self._udp_warned:
                    udp_domains.add(d)
        self._udp_warned |= udp_domains
        for d in sorted(udp_domains):
            logger.warning("[UDP_BYPASS] %s - UDP tracker cannot be intercepted, announces bypass NewGreedy", d)
        if udp_domains:
            logger.warning("[UDP_BYPASS] %d UDP tracker domain(s) detected - real stats are reported to them",
                           len(udp_domains))

    # ── control file: pause, overrides, tags ──────────────────────────────────

    def _reload_control(self):
        with self._lock:
            self._load_control()
        self._flush_consume_queue()

    def _load_control(self):
        try:
            mtime = os.path.getmtime(CONTROL_FILE)
        except OSError:
            if self._paused_set or self._tag_map or self._overrides:
                removed = self._overrides
                self._paused_set = set()
                self._overrides = {}
                self._tag_map = {}
                self._reset_removed_targets(removed)
                self._apply_control_to_stats()
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
        previous = self._overrides
        self._paused_set = {str(h).lower() for h in data.get("paused", []) if h}
        self._overrides  = {k: v for k, v in data.get("overrides", {}).items() if isinstance(v, dict)}
        self._tag_map    = {k: list(v) for k, v in data.get("tags", {}).items() if isinstance(v, list)}
        self._reset_removed_targets(previous)
        self._apply_control_to_stats()
        applied = self._apply_overrides()
        if applied:
            logger.info("[CONTROL] reloaded - %d paused, %d override(s)",
                        len(self._paused_set), applied)

    def _stats_for(self, key):
        """Stats of the torrent a control entry names: the full info-hash, or an 8-digit
        key written by an older version when exactly one torrent starts with it."""
        st = self._stats.get(key)
        if st is not None or len(key) != MIN_HASH_LEN:
            return st
        found = [s for ih, s in self._stats.items() if ih.startswith(key)]
        return found[0] if len(found) == 1 else None

    def _reset_removed_targets(self, previous):
        for ih, ov in previous.items():
            if "target_ratio" not in ov or "target_ratio" in self._overrides.get(ih, {}):
                continue
            st = self._stats_for(ih)
            if st is None:
                continue
            st._target_ratio = st._base_target
            st._target_reached = False
            logger.info("[CONTROL] %s - ratio override removed, back to %.2f",
                        _short(ih), st._base_target - st._target_buf)

    def _apply_control_to_stats(self):
        for ih, st in self._stats.items():
            st._paused = datastore.alias_key(self._paused_set, ih) is not None
            tk = datastore.alias_key(self._tag_map, ih)
            st._tags   = self._tag_map[tk] if tk else []
        self._reapply_targets()

    def _reapply_targets(self):
        for ih, ov in self._overrides.items():
            st = self._stats_for(ih)
            if st is not None and "target_ratio" in ov:
                self._apply_override_to(st, ih, {"target_ratio": ov["target_ratio"]}, quiet=True)

    def _apply_overrides(self):
        count = 0
        for ih, ov in list(self._overrides.items()):
            st = self._stats_for(ih)
            if not st:
                continue
            count += self._apply_override_to(st, ih, ov)
            if ov.pop("cumul_rep_ul", None) is not None:
                self._consume_queue.add(ih)
                if not ov:
                    del self._overrides[ih]
        return count

    def _apply_override_to(self, st, ih, ov, quiet=False):
        n = 0
        if "target_ratio" in ov:
            try:
                new_target = float(ov["target_ratio"]) + st._target_buf
                if abs(new_target - st._target_ratio) > 1e-9:
                    st._target_reached = False
                st._target_ratio = new_target
                n += 1
            except (ValueError, TypeError):
                pass
        if "cumul_rep_ul" in ov:
            try:
                val = max(0.0, float(ov["cumul_rep_ul"]))
                st._cumul_rep_ul = st._prev_rep_ul = val
                n += 1
                if not quiet:
                    logger.info("[CONTROL] %s - upload counter set to %.1fM", _short(ih), val / 1e6)
            except (ValueError, TypeError):
                pass
        return n

    def _flush_consume_queue(self):
        with self._lock:
            hashes, self._consume_queue = self._consume_queue, set()
        if hashes:
            self._consume_control(hashes)

    def _consume_control(self, hashes):
        try:
            def drop(data):
                overrides = data.get("overrides")
                if not isinstance(overrides, dict):
                    return False
                changed = False
                for ih in hashes:
                    ov = overrides.get(ih)
                    if not isinstance(ov, dict) or "cumul_rep_ul" not in ov:
                        continue
                    del ov["cumul_rep_ul"]
                    if not ov:
                        del overrides[ih]
                    changed = True
                return changed
            datastore.update_json(CONTROL_FILE, drop, {})
            self._control_mtime = self._mtime(CONTROL_FILE)
        except Exception as e:
            logger.debug("control consume error: %s", e)

    # ── registry ──────────────────────────────────────────────────────────────

    def _load_registry(self):
        self._registry_mtime = self._mtime(REGISTRY_FILE)
        try:
            with open(REGISTRY_FILE, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _maybe_reload_registry(self):
        mtime = self._mtime(REGISTRY_FILE)
        if not mtime or mtime == self._registry_mtime:
            return
        reg = self._load_registry()
        fixed = 0
        with self._lock:
            self._registry = reg
            for ih, st in self._stats.items():
                if self._size_from_registry(st, datastore.registry_meta(reg, ih)):
                    fixed += 1
        logger.info("[REGISTRY] reloaded - %d torrent(s)%s", len(reg),
                    f", size corrected for {fixed}" if fixed else "")
        self._warn_udp_trackers()

    @staticmethod
    def _size_from_registry(st, meta):
        if not isinstance(meta, dict):
            return False
        try:
            size = int(meta.get("size_bytes", 0) or 0)
        except (TypeError, ValueError):
            return False
        if size <= 0 or size == st._size_bytes:
            return False
        if st._seed_fake_dl == float(st._size_bytes):
            st._seed_fake_dl = float(size)
        st._size_bytes = size
        return True

    # ── persistence ───────────────────────────────────────────────────────────

    @staticmethod
    def _cumul_from_raw(tc_raw, schema):
        out = {}
        if schema < 7 or not isinstance(tc_raw, dict):
            return out
        for domain, vals in tc_raw.items():
            if isinstance(vals, dict):
                try:
                    out[domain] = {"ul": float(vals.get("ul", 0)), "dl": float(vals.get("dl", 0)),
                                   "ts": float(vals.get("ts", time.time()))}
                except (TypeError, ValueError):
                    continue
        return out

    def _stats_from_raw(self, raw):
        valid = re.compile(r"^[0-9a-f]{%d,40}$" % MIN_HASH_LEN)
        out, cache = {}, {}
        for k, d in raw.items():
            if k.startswith("_"):
                continue
            k = k.lower().strip()
            if not valid.match(k) or not isinstance(d, dict):
                continue
            ul_key = "cumul_rep_ul" if "cumul_rep_ul" in d else "rep_ul"
            dl_key = "cumul_rep_dl" if "cumul_rep_dl" in d else "rep_dl"
            if ul_key not in d:
                continue
            try:
                tr = d.get("trackers", {})
                domains = list(tr.keys()) if isinstance(tr, dict) else []
                domain = next((dm for dm in domains if dm in self._tracker_cfg),
                              d.get("domain") or (domains[0] if domains else ""))
                s = TorrentStats(self._params_for(domain, cache))
                s._domain        = domain
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
                if isinstance(tr, dict):
                    s._trackers = {
                        k2: {"ul": float(v2.get("ul", 0)), "dl": float(v2.get("dl", 0)),
                             "seen_dl": float(v2.get("seen_dl", 0))}
                        for k2, v2 in tr.items() if isinstance(v2, dict)
                    }
            except (TypeError, ValueError) as e:
                logger.warning("stats entry %s skipped: %s", k, e)
                continue
            self._size_from_registry(s, datastore.registry_meta(self._registry, k))
            out[k] = s
        return out

    def _load_stats(self):
        try:
            with open(STATS_FILE, encoding="utf-8") as f:
                raw = json.load(f)
            schema = raw.get("_schema_version", 1)
            if schema < 7 and raw.get("_tracker_cumul"):
                logger.warning("[CUMUL_RESET] per-tracker totals from schema v%d counted each "
                               "announce's cumulative download instead of its delta - dropping "
                               "them, the aggregate guard restarts from real figures", schema)
            self._tracker_cumul = self._cumul_from_raw(raw.get("_tracker_cumul", {}), schema)
            self._stats = self._stats_from_raw(raw)
            logger.info("Stats loaded: %d torrents (schema v%d)", len(self._stats), schema)
        except FileNotFoundError:
            logger.info("No stats file - starting fresh.")
        except Exception as e:
            logger.warning("Stats load error: %s", e)

    @staticmethod
    def _serialize(s, now):
        return {
            "cumul_rep_ul":     s._cumul_rep_ul,
            "cumul_rep_dl":     s._cumul_rep_dl,
            "cumul_real_ul":    s._cumul_real_ul,
            "seen_ul":          s._seen_ul,
            "ann_count":        s._ann_count,
            "stalled":          s._is_net_stalled,
            "prev_rep_ul":      s._prev_rep_ul,
            "target_reached":   s._target_reached,
            "seed_fake_dl":     s._seed_fake_dl,
            "history":          compress_history(s._history, now),
            "mode":             s._mode,
            "last_announce_ts": s._last_announce_ts,
            "key":              s._key,
            "size_bytes":       s._size_bytes,
            "reported_dl":      s._reported_dl,
            "domain":           s._domain,
            "trackerids":       dict(s._trackerids),
            "tracker_intervals": dict(s._tracker_intervals),
            "trackers":         {k: dict(v) for k, v in s._trackers.items()},
        }

    def _snapshot_data(self, now):
        data = {"_schema_version": SCHEMA_VER}
        if self._identity:
            data["_identity"] = self._identity.as_dict()
        tc_cutoff = now - self._cumul_ttl if self._cumul_ttl > 0 else 0
        tc_out = {}
        for domain, tc in list(self._tracker_cumul.items()):
            ts = float(tc.get("ts", now))
            if tc_cutoff > 0 and ts < tc_cutoff:
                self._tracker_cumul.pop(domain, None)
                logger.info("[CUMUL_PURGE] %s - inactive tracker dropped from cumul", domain)
                continue
            tc_out[domain] = {"ul": tc["ul"], "dl": tc["dl"], "ts": ts}
        data["_tracker_cumul"] = tc_out
        for ih, s in self._stats.items():
            data[ih] = self._serialize(s, now)
        return data

    def _save_stats(self, force=False):
        if not self._persist:
            self._dirty = False
            return
        now = time.time()
        if not force and (now - self._last_flush_ts) < self._flush_interval:
            return
        with self._save_lock:
            try:
                # the web UI removes entries under the same lock and queues the purge
                # before releasing it: applying the queue here keeps them from coming back
                with datastore.file_lock(STATS_FILE):
                    self._apply_purges()
                    with self._lock:
                        data = self._snapshot_data(now)
                        self._dirty = False
                    atomic_write_json(STATS_FILE, data)
                self._last_flush_ts = now
            except Exception as e:
                self._dirty = True
                logger.warning("Stats save failed: %s", e)
