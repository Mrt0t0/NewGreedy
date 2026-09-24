#!/usr/bin/env python3
"""NewGreedy v1.7.7 — self-update from GitHub releases"""
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
import hashlib
import io
import json
import logging
import os
import pathlib
import re
import shutil
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile

import config_merge
from _version import (VERSION, GITHUB_REPO, fetch_latest, parse_version,
                      atomic_write_bytes)

logger = logging.getLogger("NewGreedy.Update")

BASE_DIR = pathlib.Path(__file__).parent.resolve()
BACKUP_DIR = BASE_DIR / ".backup"
RESTART_FLAG = BASE_DIR / ".restart"

PRESERVE = {"config.ini", "stats.json", "torrent_registry.json",
            "control.json", "purge_pending.json", "newgreedy.log"}
SKIP_PREFIX = (".git", "__pycache__", "torrents/", ".backup")
MAX_DOWNLOAD = 25 * 1024 * 1024
ALLOWED_HOSTS = ("github.com", "api.github.com", "codeload.github.com",
                 "objects.githubusercontent.com", "release-assets.githubusercontent.com")

_state = {"status": "idle", "message": "", "from": VERSION, "to": "", "ts": 0}
_lock = threading.Lock()


def status():
    with _lock:
        return dict(_state)


def _set(status_name, message="", to=""):
    with _lock:
        _state.update({"status": status_name, "message": message,
                       "ts": int(time.time())})
        if to:
            _state["to"] = to
    if status_name == "error":
        logger.warning("[UPDATE] %s", message)
    elif message:
        logger.info("[UPDATE] %s", message)


def _check_host(url: str):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        raise ValueError(f"refusing to download the update from an unexpected host: {host or url}")


def _download(url: str) -> bytes:
    _check_host(url)
    req = urllib.request.Request(url, headers={"User-Agent": "NewGreedy-updater"})
    with urllib.request.urlopen(req, timeout=60) as r:
        _check_host(r.geturl())
        data = r.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise ValueError("release archive is larger than 25 MB")
    return data


def _expected_digest(release: dict) -> str:
    body = release.get("body") or ""
    m = re.search(r"\b([0-9a-fA-F]{64})\b", body)
    return m.group(1).lower() if m else ""


def _verify_digest(archive: bytes, expected: str):
    if not expected:
        return
    got = hashlib.sha256(archive).hexdigest()
    if got != expected:
        raise ValueError(f"archive checksum mismatch (expected {expected[:12]}..., got {got[:12]}...)")


def _archive_root(names) -> str:
    if not names:
        return ""
    root = names[0].split("/")[0] + "/"
    return root if all(n.startswith(root) for n in names) else ""


def _safe_members(zf: zipfile.ZipFile):
    names = [n for n in zf.namelist() if not n.endswith("/")]
    if not names:
        raise ValueError("empty archive")
    root = _archive_root(names)
    for name in names:
        rel = name[len(root):] if root else name
        if not rel or rel.startswith("/") or ".." in rel.split("/"):
            continue
        if rel.startswith(SKIP_PREFIX) or rel in PRESERVE:
            continue
        yield name, rel


def _migrate_config(zf: zipfile.ZipFile) -> str:
    target = BASE_DIR / "config.ini"
    names = [n for n in zf.namelist() if not n.endswith("/")]
    want = _archive_root(names) + "config.ini"
    if want not in names:
        return ""
    shipped = zf.read(want).decode("utf-8", "replace")
    if not target.exists():
        atomic_write_bytes(target, shipped.encode("utf-8"))
        return "config.ini installed"
    current = target.read_text(encoding="utf-8", errors="replace")
    merged, report = config_merge.merge(shipped, current)
    if merged == current:
        return ""
    backup = BACKUP_DIR / "config.ini"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup)
    atomic_write_bytes(target, merged.encode("utf-8"))
    for line in config_merge.format_report(report):
        logger.info("[UPDATE] config.ini: %s", line)
    added, changed = len(report["added"]), len(report["changed"])
    note = f"{added} new setting(s) added to config.ini"
    return note + (f", {changed} default(s) applied" if changed else "")


def _apply(archive: bytes):
    if BACKUP_DIR.exists():
        shutil.rmtree(BACKUP_DIR, ignore_errors=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        members = list(_safe_members(zf))
        if not members:
            raise ValueError("archive contains no updatable file")
        for _, rel in members:
            src = BASE_DIR / rel
            if src.exists():
                dst = BACKUP_DIR / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        written = 0
        for name, rel in members:
            target = BASE_DIR / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                mode = target.stat().st_mode & 0o777
            else:
                mode = 0o755 if rel.endswith(".sh") or rel == "newgreedy.py" else 0o644
            with tempfile.NamedTemporaryFile(dir=str(target.parent), delete=False) as tmp:
                tmp.write(zf.read(name))
                tmp_path = tmp.name
            os.chmod(tmp_path, mode)
            os.replace(tmp_path, target)
            written += 1
        try:
            note = _migrate_config(zf)
        except Exception as e:
            note = ""
            logger.warning("[UPDATE] config.ini left untouched, merge failed: %s", e)
    return written, note


def rollback() -> int:
    if not BACKUP_DIR.exists():
        return 0
    restored = 0
    for path in BACKUP_DIR.rglob("*"):
        if path.is_file():
            rel = path.relative_to(BACKUP_DIR)
            target = BASE_DIR / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            restored += 1
    return restored


def _worker(tag: str, url: str, digest: str = ""):
    try:
        _set("downloading", f"fetching {tag}", to=tag)
        archive = _download(url)
        _verify_digest(archive, digest)
        _set("applying", f"extracting {len(archive) // 1024} KB"
                         + (" (checksum verified)" if digest else ""))
        count, cfg_note = _apply(archive)
        RESTART_FLAG.write_text(json.dumps({"tag": tag, "ts": int(time.time())}),
                                encoding="utf-8")
        _set("done", f"{count} file(s) updated to {tag}"
                     + (f"; {cfg_note}" if cfg_note else "")
                     + "; restart required")
    except Exception as e:
        _set("error", str(e))
        try:
            n = rollback()
            if n:
                logger.info("[UPDATE] rolled back %d file(s)", n)
        except Exception as re:
            logger.warning("[UPDATE] rollback failed: %s", re)


def start(force: bool = False):
    with _lock:
        if _state["status"] in ("downloading", "applying"):
            return False, "an update is already running"
    try:
        release = fetch_latest()
    except Exception as e:
        return False, f"could not reach GitHub: {e}"

    tag = (release.get("tag_name") or "").strip()
    if not tag:
        return False, "no release tag found"
    if not force and parse_version(tag) <= parse_version(VERSION):
        return False, f"already up to date ({VERSION})"

    url = ""
    for asset in release.get("assets", []):
        name = (asset.get("name") or "").lower()
        if name.endswith(".zip"):
            url = asset.get("browser_download_url", "")
            break
    if not url:
        url = release.get("zipball_url") or \
              f"https://github.com/{GITHUB_REPO}/archive/refs/tags/{tag}.zip"

    try:
        _check_host(url)
    except ValueError as e:
        return False, str(e)

    _set("downloading", f"starting update to {tag}", to=tag)
    threading.Thread(target=_worker, args=(tag, url, _expected_digest(release)),
                     daemon=True).start()
    return True, f"update to {tag} started"
