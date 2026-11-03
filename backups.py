#!/usr/bin/env python3
"""NewGreedy — rotating backup archives of the data files"""
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
import io
import logging
import os
import re
import time
import zipfile

import datastore
from _version import atomic_write_bytes

logger = logging.getLogger("NewGreedy.Backup")

ARCHIVE_FILES = ("config.ini", "stats.json", "torrent_registry.json", "control.json", "trash.json")
REASONS       = ("auto", "manual", "pre-update", "pre-restore", "pre-merge")
NAME_RE       = re.compile(r"^newgreedy-(\d{8}-\d{6})-(auto|manual|pre-update|pre-restore|pre-merge)\.zip$")
KEEP_OTHERS   = 5


def build_archive() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ARCHIVE_FILES:
            path = datastore.DATA_DIR / name
            if path.is_file():
                z.write(path, name)
    return buf.getvalue()


def create(reason="manual", keep=7):
    if reason not in REASONS:
        reason = "manual"
    datastore.BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        name = f"newgreedy-{time.strftime('%Y%m%d-%H%M%S')}-{reason}.zip"
        path = datastore.BACKUPS_DIR / name
        if not path.exists():
            break
        time.sleep(1.0)
    atomic_write_bytes(path, build_archive())
    rotate(reason, keep if reason == "auto" else KEEP_OTHERS)
    logger.info("[BACKUP] %s written (%s)", name, reason)
    return name


def listing():
    out = []
    try:
        entries = list(os.scandir(datastore.BACKUPS_DIR))
    except FileNotFoundError:
        return out
    for e in entries:
        m = NAME_RE.match(e.name)
        if not m or not e.is_file():
            continue
        st = e.stat()
        out.append({"name": e.name, "reason": m.group(2), "size": st.st_size,
                    "ts": int(time.mktime(time.strptime(m.group(1), "%Y%m%d-%H%M%S")))})
    return sorted(out, key=lambda x: x["name"], reverse=True)


def rotate(reason, keep):
    items = [b for b in listing() if b["reason"] == reason]
    for b in items[max(1, keep):]:
        try:
            os.remove(datastore.BACKUPS_DIR / b["name"])
        except OSError:
            pass


def path_of(name):
    if not NAME_RE.match(name or ""):
        raise ValueError("invalid backup name")
    path = datastore.BACKUPS_DIR / name
    if not path.is_file():
        raise FileNotFoundError(name)
    return path


def delete(name):
    os.remove(path_of(name))


def last_auto_ts():
    autos = [b["ts"] for b in listing() if b["reason"] == "auto"]
    return max(autos) if autos else 0


def maybe_auto(enabled, interval_hours, keep):
    """Create the periodic backup when it is due; returns the new name or ''."""
    if not enabled:
        return ""
    if time.time() - last_auto_ts() < interval_hours * 3600:
        return ""
    try:
        return create("auto", keep)
    except Exception as e:
        logger.warning("[BACKUP] automatic backup failed: %s", e)
        return ""
