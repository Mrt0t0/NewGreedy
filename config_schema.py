#!/usr/bin/env python3
"""NewGreedy — config.ini schema: tolerant reading, validation, safe edits"""
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
import configparser
import logging
import math
import os
import re
import secrets
import urllib.parse

from _version import atomic_write_bytes, NOTIFY_EVENTS, NOTIFY_FORMATS

logger = logging.getLogger("NewGreedy.Config")

_BOOLS = configparser.ConfigParser.BOOLEAN_STATES

# (section, key) -> (kind, default, *args)
RULES = {
    ("meta", "config_version"):                     ("str", ""),
    ("proxy", "listen_host"):                       ("host", "127.0.0.1"),
    ("proxy", "listen_port"):                       ("int", 3456, 1, 65535),
    ("spoofing", "target_ratio"):                   ("float", 1.5, 0.01, 100.0),
    ("spoofing", "target_ratio_buffer"):            ("float", 0.03, 0.0, 10.0),
    ("spoofing", "anti_clustering"):                ("bool", True),
    ("spoofing", "max_ratio_per_torrent"):          ("float", 3.0, 0.01, 100.0),
    ("spoofing", "max_global_ratio_per_tracker"):   ("float", 2.5, 0.01, 100.0),
    ("spoofing", "catch_up_factor"):                ("float", 0.22, 0.001, 1.0),
    ("spoofing", "seed_credit_mb"):                 ("float", 5.0, 0.0, 100000.0),
    ("spoofing", "seed_target_mb"):                 ("float", 500.0, 0.0, 1e7),
    ("spoofing", "max_simulated_speed_mbps"):       ("float", 10.0, 0.01, 10000.0),
    ("spoofing", "upload_noise_pct"):               ("float", 3.0, 0.0, 100.0),
    ("spoofing", "stagnation_probability"):         ("float", 0.03, 0.0, 1.0),
    ("spoofing", "auto_stop_at_target"):            ("bool", True),
    ("anti_detection", "user_agent_mode"):          ("choice", "random", ("random", "fixed")),
    ("anti_detection", "user_agent_value"):         ("str", ""),
    ("anti_detection", "spoof_user_agent"):         ("bool", True),
    ("anti_detection", "spoof_peer_id"):            ("bool", True),
    ("anti_detection", "spoof_peers"):              ("bool", True),
    ("anti_detection", "spoof_port"):               ("bool", True),
    ("anti_detection", "port_range"):               ("range", (6881, 6999)),
    ("anti_detection", "spoof_headers"):            ("bool", True),
    ("anti_detection", "echo_trackerid"):           ("bool", True),
    ("anti_detection", "tracker_whitelist"):        ("domains", ""),
    ("anti_detection", "tracker_blacklist"):        ("domains", ""),
    ("anti_detection", "desync_announces"):         ("bool", True),
    ("anti_detection", "desync_spread_pct"):        ("float", 0.18, 0.0, 0.45),
    ("anti_detection", "desync_min_gap"):           ("int", 45, 0, 3600),
    ("anti_detection", "startup_burst"):            ("bool", True),
    ("anti_detection", "startup_burst_window"):     ("int", 300, 0, 86400),
    ("anti_detection", "startup_ramp_floor"):       ("float", 0.15, 0.0, 1.0),
    ("ssl", "ssl_verify_trackers"):                 ("bool", True),
    ("stats", "persist_stats"):                     ("bool", True),
    ("stats", "auto_purge_stopped"):                ("bool", True),
    ("stats", "stopped_purge_delay"):               ("int", 120, 0, 86400),
    ("stats", "inactive_purge_hours"):              ("int", 12, 0, 8760),
    ("stats", "tracker_cumul_ttl_days"):            ("int", 30, 0, 3650),
    ("stats", "history_max_points"):                ("int", 200, 20, 5000),
    ("stats", "history_retention_days"):            ("int", 0, 0, 3650),
    ("stats", "trash_retention_days"):              ("int", 7, 0, 365),
    ("web", "web_enabled"):                         ("bool", True),
    ("web", "web_host"):                            ("host", "127.0.0.1"),
    ("web", "web_port"):                            ("int", 8080, 1, 65535),
    ("web", "web_secret_token"):                    ("token", ""),
    ("web", "web_path_prefix"):                     ("prefix", ""),
    ("web", "web_rate_limit"):                      ("int", 240, 0, 100000),
    ("web", "web_allowed_origins"):                 ("origins", ""),
    ("advanced", "min_announce_interval"):          ("int", 1800, 60, 86400),
    ("advanced", "log_level"):                      ("choice", "INFO", ("DEBUG", "INFO", "WARNING", "ERROR")),
    ("advanced", "corrupt_field_probability"):      ("float", 0.05, 0.0, 1.0),
    ("advanced", "stall_announce_threshold"):       ("int", 8, 1, 1000),
    ("advanced", "min_announces_before_stagnation"): ("int", 10, 0, 1000),
    ("advanced", "interval_jitter_pct"):            ("float", 0.08, 0.0, 1.0),
    ("advanced", "inject_hours"):                   ("hours", "0-23"),
    ("advanced", "respect_tracker_interval"):       ("bool", True),
    ("advanced", "watch_folder"):                   ("str", "torrents"),
    ("client", "client_profile"):                   ("profile", "random"),
    ("client", "rotate_profile_on_restart"):        ("bool", True),
    ("client", "session_identity"):                 ("choice", "", ("", "stable", "drift", "rotate")),
    ("client", "version_bump_probability"):         ("float", 0.10, 0.0, 1.0),
    ("bandwidth", "global_max_speed_mbps"):         ("float", 0.0, 0.0, 10000.0),
    ("bandwidth", "per_torrent_speed_cap_mbps"):    ("float", 12.0, 0.01, 10000.0),
    ("bandwidth", "max_download_speed_mbps"):       ("float", 12.0, 0.0, 10000.0),
    ("bandwidth", "circadian_smoothing"):           ("bool", True),
    ("bandwidth", "circadian_night_factor"):        ("float", 0.35, 0.0, 1.0),
    ("notifications", "webhook_url"):               ("url", ""),
    ("notifications", "webhook_format"):            ("choice", "generic", NOTIFY_FORMATS),
    ("notifications", "webhook_events"):            ("events", ""),
    ("notifications", "webhook_chat_id"):           ("str", ""),
    ("upstream", "proxy_url"):                      ("url", ""),
    ("upstream", "proxy_bypass"):                   ("domains", ""),
    ("backup", "auto_backup"):                      ("bool", True),
    ("backup", "backup_interval_hours"):            ("int", 24, 1, 8760),
    ("backup", "backup_keep"):                      ("int", 7, 1, 365),
    ("update", "update_channel"):                   ("choice", "stable", ("stable", "beta")),
    ("simulation", "dry_run"):                      ("bool", False),
}

# keys a [tracker:domain] section may override
TRACKER_KEYS = {k for (s, k) in RULES if s in ("spoofing", "advanced", "bandwidth")}

EDITABLE_KEYS = {
    ("spoofing", "target_ratio"), ("spoofing", "target_ratio_buffer"),
    ("spoofing", "catch_up_factor"), ("spoofing", "max_ratio_per_torrent"),
    ("spoofing", "max_global_ratio_per_tracker"), ("spoofing", "max_simulated_speed_mbps"),
    ("spoofing", "upload_noise_pct"), ("spoofing", "stagnation_probability"),
    ("spoofing", "seed_credit_mb"), ("spoofing", "seed_target_mb"),
    ("spoofing", "auto_stop_at_target"), ("spoofing", "anti_clustering"),
    ("advanced", "min_announce_interval"), ("advanced", "log_level"),
    ("advanced", "inject_hours"), ("advanced", "interval_jitter_pct"),
    ("advanced", "stall_announce_threshold"), ("advanced", "min_announces_before_stagnation"),
    ("advanced", "corrupt_field_probability"), ("advanced", "respect_tracker_interval"),
    ("anti_detection", "user_agent_mode"), ("anti_detection", "user_agent_value"),
    ("anti_detection", "spoof_user_agent"), ("anti_detection", "spoof_peer_id"),
    ("anti_detection", "spoof_peers"), ("anti_detection", "spoof_port"),
    ("anti_detection", "spoof_headers"), ("anti_detection", "echo_trackerid"),
    ("anti_detection", "tracker_whitelist"), ("anti_detection", "tracker_blacklist"),
    ("anti_detection", "desync_announces"), ("anti_detection", "desync_spread_pct"),
    ("anti_detection", "desync_min_gap"), ("anti_detection", "startup_burst"),
    ("anti_detection", "startup_burst_window"), ("anti_detection", "startup_ramp_floor"),
    ("ssl", "ssl_verify_trackers"),
    ("stats", "persist_stats"), ("stats", "auto_purge_stopped"),
    ("stats", "tracker_cumul_ttl_days"), ("stats", "stopped_purge_delay"),
    ("stats", "inactive_purge_hours"), ("stats", "history_max_points"),
    ("stats", "history_retention_days"), ("stats", "trash_retention_days"),
    ("client", "client_profile"), ("client", "rotate_profile_on_restart"),
    ("client", "session_identity"), ("client", "version_bump_probability"),
    ("bandwidth", "global_max_speed_mbps"), ("bandwidth", "per_torrent_speed_cap_mbps"),
    ("bandwidth", "max_download_speed_mbps"),
    ("bandwidth", "circadian_smoothing"), ("bandwidth", "circadian_night_factor"),
    ("notifications", "webhook_url"), ("notifications", "webhook_format"),
    ("notifications", "webhook_events"), ("notifications", "webhook_chat_id"),
    ("backup", "auto_backup"), ("backup", "backup_interval_hours"), ("backup", "backup_keep"),
    ("update", "update_channel"),
    ("simulation", "dry_run"),
}

# settings the engine cannot change while it runs
RESTART_KEYS = {
    ("proxy", "listen_host"), ("proxy", "listen_port"), ("ssl", "ssl_verify_trackers"),
    ("upstream", "proxy_url"), ("upstream", "proxy_bypass"),
    ("client", "client_profile"), ("client", "rotate_profile_on_restart"),
    ("client", "session_identity"), ("client", "version_bump_probability"),
    ("anti_detection", "port_range"),
} | {("web", k) for (s, k) in RULES if s == "web"}

SECRET_KEYS = {("web", "web_secret_token")}

ENV_HOSTS = {("proxy", "listen_host"): "NEWGREEDY_LISTEN_HOST",
             ("web", "web_host"): "NEWGREEDY_WEB_HOST"}

LOOPBACK = ("127.0.0.1", "localhost", "::1")


def new_parser():
    return configparser.ConfigParser(inline_comment_prefixes=(";", "#"),
                                     interpolation=None, strict=False)


def load(path):
    """Parse config.ini; a syntax error keeps everything parsed before it."""
    c = new_parser()
    try:
        c.read(str(path), encoding="utf-8")
    except configparser.Error as e:
        logger.warning("config.ini has syntax errors, the rest is still used: %s", e)
    return c


def _num(raw):
    return float(str(raw).strip().replace(",", "."))


def parse_value(rule, raw, strict=False):
    """Returns (value, problem). Non-strict mode clamps out-of-range numbers."""
    kind, default = rule[0], rule[1]
    raw = "" if raw is None else str(raw).strip()
    try:
        if kind in ("float", "int"):
            try:
                num = _num(raw)
            except ValueError:
                return default, "must be a number"
            if not math.isfinite(num):
                return default, "must be a number"
            if kind == "int":
                if num != int(num):
                    return default, "must be a whole number"
                num = int(num)
            lo, hi = rule[2], rule[3]
            if not (lo <= num <= hi):
                if strict:
                    return default, f"must be between {lo} and {hi}"
                return max(lo, min(hi, num)), f"out of range {lo}-{hi}, clamped"
            return num, None
        if kind == "bool":
            if raw.lower() in _BOOLS:
                return _BOOLS[raw.lower()], None
            return default, "must be true or false"
        if kind == "choice":
            for c in rule[2]:
                if raw.lower() == c.lower():
                    return c, None
            return default, "must be one of: " + ", ".join(c for c in rule[2] if c)
        if kind == "hours":
            m = re.fullmatch(r"(\d{1,2})\s*-\s*(\d{1,2})", raw)
            if not m or not all(0 <= int(g) <= 23 for g in m.groups()):
                return default, "must look like 8-22, both values between 0 and 23"
            return f"{int(m.group(1))}-{int(m.group(2))}", None
        if kind == "range":
            m = re.fullmatch(r"(\d{1,5})\s*-\s*(\d{1,5})", raw)
            if not m:
                return default, "must look like 6881-6999"
            lo, hi = int(m.group(1)), int(m.group(2))
            if not (1 <= lo <= hi <= 65535):
                return default, "must be two ports between 1 and 65535, low one first"
            return (lo, hi), None
        if kind == "profile":
            import client_profiles
            allowed = client_profiles.profile_names() + ["random"]
            return (raw.lower(), None) if raw.lower() in allowed else \
                (default, "must be one of: " + ", ".join(allowed))
        if kind == "url":
            if not raw:
                return "", None
            if urllib.parse.urlsplit(raw).scheme not in ("http", "https"):
                return default, "must start with http:// or https://"
            return raw, None
        if kind == "host":
            if not raw or re.search(r"[\s/]", raw):
                return default, "must be an IP address or host name"
            return raw, None
        if kind == "prefix":
            if raw.strip("/") and not re.fullmatch(r"[A-Za-z0-9_-]+", raw.strip("/")):
                return default, "letters, digits, - and _ only"
            return raw.strip("/"), None
        if kind == "token":
            if raw and not re.fullmatch(r"[\x21-\x7e]{8,256}", raw):
                return default, "8 to 256 printable ASCII characters, no spaces"
            return raw, None
        if kind == "events":
            evs = [e.strip().lower() for e in raw.split(",") if e.strip()]
            bad = [e for e in evs if e not in NOTIFY_EVENTS]
            if bad:
                return ",".join(e for e in evs if e in NOTIFY_EVENTS), \
                    "unknown event(s): " + ", ".join(bad)
            return ",".join(evs), None
        if kind in ("domains", "origins"):
            items = [x.strip().lower() for x in raw.split(",") if x.strip()]
            return items, None
        return raw, None
    except (ValueError, TypeError):
        return default, "invalid value"


_warned = set()


def get(c, section, key, default=None):
    """Typed, never-raising read of one setting."""
    rule = RULES.get((section, key))
    env = ENV_HOSTS.get((section, key))
    if env and os.environ.get(env):
        return os.environ[env].strip()
    if c is None or not c.has_option(section, key):
        if rule is None:
            return default
        return rule[1] if default is None else default
    raw = c.get(section, key, fallback="")
    if rule is None:
        return raw
    value, problem = parse_value(rule, raw)
    if problem and (section, key, raw) not in _warned:
        _warned.add((section, key, raw))
        logger.warning("config.ini [%s] %s = '%s' %s - using %r", section, key, raw, problem, value)
    return value


def tracker_value(key, raw):
    """Parse a [tracker:domain] override with the rule of the global key it replaces."""
    for (s, k), rule in RULES.items():
        if k == key:
            value, problem = parse_value(rule, raw)
            return value, problem
    return raw, None


def validate_value(section, key, value):
    """Strict check used before writing a value from the web UI; returns an error or None."""
    rule = RULES.get((section, key))
    if not rule:
        return None
    _, problem = parse_value(rule, value, strict=True)
    return f"{key} {problem}" if problem else None


def normalize(section, key, value):
    """Canonical text for a value about to be written (1,6 -> 1.6, TRUE -> true)."""
    rule = RULES.get((section, key))
    if not rule:
        return value
    parsed, problem = parse_value(rule, value, strict=True)
    if problem:
        return value
    if isinstance(parsed, bool):
        return "true" if parsed else "false"
    if isinstance(parsed, (int, float)):
        return repr(parsed) if isinstance(parsed, float) else str(parsed)
    if isinstance(parsed, tuple):
        return "-".join(str(x) for x in parsed)
    if isinstance(parsed, list):
        return ",".join(parsed)
    return str(parsed)


def check(c):
    """Every problem found in a parsed config, as (level, section, key, message)."""
    issues = []
    for section in c.sections():
        if section.startswith("tracker:"):
            for key in c[section]:
                if key not in TRACKER_KEYS:
                    issues.append(("warn", section, key, "not a setting a tracker section can override"))
                    continue
                _, problem = tracker_value(key, c[section][key])
                if problem:
                    issues.append(("error", section, key, problem))
            continue
        known = {k for (s, k) in RULES if s == section}
        if not known:
            issues.append(("info", section, "", "unknown section, ignored"))
            continue
        for key in c[section]:
            rule = RULES.get((section, key))
            if not rule:
                issues.append(("info", section, key, "unknown key, ignored"))
                continue
            _, problem = parse_value(rule, c[section][key], strict=True)
            if problem:
                issues.append(("error", section, key, problem))
    return issues


def is_loopback(host: str) -> bool:
    host = (host or "").strip().lower().strip("[]")
    return host in LOOPBACK or host.startswith("127.")


def new_token() -> str:
    return secrets.token_urlsafe(24)


def set_value(path, section, key, value):
    """Write one key into config.ini, keeping comments and layout; atomic."""
    import datastore
    with datastore.file_lock(path):
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            text = ""
        lines = text.splitlines()
        cur, sec_start, sec_end, done = None, None, None, False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                if cur == section:
                    sec_end = i
                cur = stripped[1:-1].strip()
                if cur == section:
                    sec_start = i
                continue
            if cur != section or "=" not in line or stripped.startswith((";", "#")):
                continue
            lhs = line.split("=", 1)[0]
            if lhs.strip() != key:
                continue
            lines[i] = f"{lhs}= {value}" if lhs.endswith(" ") else f"{lhs} = {value}"
            done = True
            break
        if not done:
            if sec_start is None:
                lines += ["", f"[{section}]", f"{key} = {value}"]
            else:
                end = sec_end if sec_end is not None and sec_end > sec_start else len(lines)
                while end > sec_start + 1 and not lines[end - 1].strip():
                    end -= 1
                lines.insert(end, f"{key} = {value}")
        atomic_write_bytes(path, ("\n".join(lines).rstrip("\n") + "\n").encode("utf-8"))
