#!/usr/bin/env python3
"""NewGreedy — data paths and the files shared by the engine and the web UI"""
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
import contextlib
import json
import logging
import os
import pathlib
import threading
import time

from _version import atomic_write_json

logger = logging.getLogger("NewGreedy.Data")

BASE_DIR = pathlib.Path(__file__).parent.resolve()
DATA_DIR = pathlib.Path(os.environ.get("NEWGREEDY_DATA_DIR") or BASE_DIR).resolve()
IN_DOCKER = os.environ.get("NEWGREEDY_DOCKER") == "1" or os.path.exists("/.dockerenv")

CONFIG_FILE        = DATA_DIR / "config.ini"
SHIPPED_CONFIG     = BASE_DIR / "config.ini"
STATS_FILE         = DATA_DIR / "stats.json"
REGISTRY_FILE      = DATA_DIR / "torrent_registry.json"
CONTROL_FILE       = DATA_DIR / "control.json"
TRASH_FILE         = DATA_DIR / "trash.json"
PURGE_PENDING_FILE = DATA_DIR / "purge_pending.json"
IMPORT_FILE        = DATA_DIR / "stats_import.json"
STATUS_FILE        = DATA_DIR / ".addon_status.json"
LOG_FILE           = DATA_DIR / "newgreedy.log"
SHUTDOWN_FLAG      = DATA_DIR / ".shutdown"
RESTART_FLAG       = DATA_DIR / ".restart"
BACKUPS_DIR        = DATA_DIR / "backups"
STATIC_DIR         = BASE_DIR / "static"

DATA_FILES = ("config.ini", "stats.json", "torrent_registry.json", "control.json",
              "trash.json", "purge_pending.json", "newgreedy.log")

SHORT_HASH_LEN = 8          # key length of v1.7.7 and older, still accepted as an alias

# a lock older than this is left over by a crash; a big stats.json written with fsync
# on a slow SD card can take several seconds, so keep a wide margin
LOCK_STALE_S = 60.0
_thread_locks = {}
_thread_locks_guard = threading.Lock()


def ensure_data_dir():
    """Create the data directory and, when it differs from the program folder,
    seed it from the files an older layout kept next to the code."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if DATA_DIR == BASE_DIR:
        return []
    moved = []
    for name in DATA_FILES:
        src, dst = BASE_DIR / name, DATA_DIR / name
        if name == "config.ini":
            continue
        if src.is_file() and not dst.exists():
            try:
                dst.write_bytes(src.read_bytes())
                moved.append(name)
            except OSError as e:
                logger.warning("could not copy %s into %s: %s", name, DATA_DIR, e)
    if not CONFIG_FILE.exists() and SHIPPED_CONFIG.is_file():
        CONFIG_FILE.write_bytes(SHIPPED_CONFIG.read_bytes())
        moved.append("config.ini (default)")
    return moved


def resolve_data_path(value: str) -> pathlib.Path:
    p = pathlib.Path(value).expanduser()
    return p if p.is_absolute() else DATA_DIR / p


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        logger.debug("read %s failed: %s", path, e)
        return default


@contextlib.contextmanager
def file_lock(path, timeout=5.0):
    """Lock a data file for a read-modify-write, across threads and processes."""
    path = str(path)
    with _thread_locks_guard:
        tlock = _thread_locks.setdefault(path, threading.Lock())
    with tlock:
        lock = path + ".lock"
        deadline = time.monotonic() + timeout
        fd = None
        while fd is None:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(lock) > LOCK_STALE_S:
                        os.remove(lock)
                        continue
                except OSError:
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{os.path.basename(path)} is locked")
                time.sleep(0.05)
        try:
            yield
        finally:
            os.close(fd)
            try:
                os.remove(lock)
            except OSError:
                pass


def update_json(path, fn, default):
    """Read, transform and write back a JSON file under file_lock; returns fn's result."""
    with file_lock(path):
        data = read_json(path, default)
        if not isinstance(data, type(default)):
            data = default
        result = fn(data)
        atomic_write_json(path, data)
        return result


# ── torrent keys: full info-hash, 8-digit prefix written by older versions ───

def alias_key(mapping, ih):
    """Key under which mapping (dict or set) holds this torrent: the full info-hash,
    or the 8-digit prefix older versions used; None when absent."""
    if ih in mapping:
        return ih
    short = ih[:SHORT_HASH_LEN]
    return short if short != ih and short in mapping else None


def registry_meta(reg, ih):
    """Registry entry of a torrent; a legacy 8-digit entry only when its info_hash agrees."""
    meta = reg.get(ih)
    if isinstance(meta, dict):
        return meta
    meta = reg.get(ih[:SHORT_HASH_LEN])
    if not isinstance(meta, dict):
        return None
    full = str(meta.get("info_hash") or "")
    if len(ih) > SHORT_HASH_LEN and full and full != ih:
        return None
    return meta


# ── purge queue (web -> engine) ──────────────────────────────────────────────

def queue_purge(hashes):
    hashes = [h for h in hashes if h]
    if not hashes:
        return
    update_json(PURGE_PENDING_FILE, lambda cur: cur.extend(h for h in hashes if h not in cur), [])


def take_purges():
    if not PURGE_PENDING_FILE.exists():
        return []
    with file_lock(PURGE_PENDING_FILE):
        data = read_json(PURGE_PENDING_FILE, [])
        try:
            os.remove(PURGE_PENDING_FILE)
        except OSError:
            pass
    return [h for h in data if isinstance(h, str)] if isinstance(data, list) else []


# ── stats import (web -> engine): restore of a backup or of trashed entries ──

def queue_stats_import(entries: dict, mode: str = "merge", extra=None):
    payload = {"mode": mode, "entries": entries, "ts": time.time()}
    if extra:
        payload.update(extra)

    def put(cur):
        if mode == "replace":
            cur.clear()
            cur.update(payload)
        else:
            cur.setdefault("mode", "merge")
            cur.setdefault("entries", {}).update(entries)
            cur["ts"] = payload["ts"]
    update_json(IMPORT_FILE, put, {})


def take_stats_import():
    if not IMPORT_FILE.exists():
        return None
    with file_lock(IMPORT_FILE):
        data = read_json(IMPORT_FILE, None)
        try:
            os.remove(IMPORT_FILE)
        except OSError:
            pass
    return data if isinstance(data, dict) else None


# ── trash ────────────────────────────────────────────────────────────────────

def trash_add(entries: dict, reason: str):
    if not entries:
        return
    now = time.time()

    def put(cur):
        for ih, data in entries.items():
            cur[ih] = {"deleted_at": now, "reason": reason, "data": data}
    update_json(TRASH_FILE, put, {})


def trash_list():
    data = read_json(TRASH_FILE, {})
    return data if isinstance(data, dict) else {}


def trash_take(ih: str):
    return update_json(TRASH_FILE, lambda cur: cur.pop(ih, None), {})


def trash_clean(retention_days: float):
    if retention_days <= 0 or not TRASH_FILE.exists():
        return 0
    cutoff = time.time() - retention_days * 86400

    def clean(cur):
        old = [k for k, v in cur.items()
               if not isinstance(v, dict) or float(v.get("deleted_at", 0)) < cutoff]
        for k in old:
            del cur[k]
        return len(old)
    return update_json(TRASH_FILE, clean, {})


# ── engine status (engine -> web) ────────────────────────────────────────────

def write_status(status: dict):
    try:
        atomic_write_json(STATUS_FILE, dict(status, heartbeat=time.time()))
    except Exception as e:
        logger.debug("status write failed: %s", e)


def read_status():
    data = read_json(STATUS_FILE, {})
    return data if isinstance(data, dict) else {}


def file_size(path) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
