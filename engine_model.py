#!/usr/bin/env python3
"""NewGreedy — per-torrent ratio model and session identity"""
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
import logging
import math
import os
import random
import time
from datetime import datetime
from urllib.parse import urlparse

import client_profiles

logger = logging.getLogger("NewGreedy")

HISTORY_MAX    = 200
HISTORY_FULL_S = 86400
# history size and age, set from config.ini by the engine
HIST = {"max": HISTORY_MAX, "retention": 0}


def tracker_domain(url):
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def domain_match(domain, patterns):
    return any(domain == p or domain.endswith("." + p) for p in patterns)


def _pareto_noise(inc: float, noise_pct: float) -> float:
    u = random.random()
    if u < 0.85:
        return inc * random.gauss(1.0, noise_pct)
    burst = random.uniform(1.5, 3.0)
    return inc * burst if random.random() < 0.5 else inc / burst


def parse_hours(spec: str):
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


def compress_history(hist, now):
    if not hist:
        return []
    if HIST["retention"] > 0:
        keep_from = now - HIST["retention"]
        hist = [p for p in hist if p.get("t", 0) >= keep_from]
    cutoff = now - HISTORY_FULL_S
    recent = [p for p in hist if p.get("t", 0) >= cutoff]
    old    = [p for p in hist if p.get("t", 0) < cutoff]
    bucket = {}
    for p in old:
        bucket[p.get("t", 0) // 3600] = p
    merged = sorted(bucket.values(), key=lambda x: x.get("t", 0)) + recent
    return merged[-HIST["max"]:]


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
        "_base_target", "_domain", "_just_reached", "_seen_dl",
    )

    def __init__(self, params):
        self.set_params(params)
        self._domain         = ""
        self._just_reached   = False
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
        self._seen_dl        = 0

    def set_params(self, params):
        """Engine settings only - counters are left alone, so config.ini can be reloaded live."""
        (self._stagnation_p, self._catch_up, self._max_speed, self._noise_pct,
         self._max_ratio_t, self._seed_credit, self._stall_thr,
         self._min_ann_stag, self._target_buf, self._target_ratio,
         self._auto_stop, self._active_hours, self._night_factor, self._smoothing,
         self._speed_cap) = params
        self._base_target = self._target_ratio

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
        if interval <= 0:
            return self._cumul_rep_ul, False
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
                self._just_reached = True
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
        """(downloaded, left) to report: downloaded + left = size, and progress the client
        did not download in this session is paced by dl_cap."""
        self._dl_capped = False
        real_dl = max(0, int(real_dl))
        # bytes the client really fetched since its previous announce (it restarts at 0
        # with each client session)
        gained = real_dl - self._seen_dl if real_dl >= self._seen_dl else real_dl
        self._seen_dl = real_dl
        if self._size_bytes > 0:
            # what the client holds: a torrent resumed at 70 % is not reported at 0 %,
            # and one that completes is reported complete
            held = self._size_bytes - max(0, int(client_left))
            want = min(max(int(rep_dl), held), self._size_bytes)
        else:
            want = int(self._seed_fake_dl) if (client_left == 0 and real_dl == 0) else int(rep_dl)
        prev = self._reported_dl
        free = max(prev + gained, real_dl)
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
        if len(self._history) > HIST["max"]:
            self._history = compress_history(self._history, now)

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

