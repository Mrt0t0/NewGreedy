#!/usr/bin/env python3
"""NewGreedy — self-update from GitHub releases"""
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
import base64
import hashlib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile

import config_merge
import datastore
from _version import (VERSION, fetch_latest, parse_version, atomic_write_bytes,
                      UPDATE_SIGNING_KEY)

logger = logging.getLogger("NewGreedy.Update")

BASE_DIR = datastore.BASE_DIR
BACKUP_DIR = BASE_DIR / ".backup"
CREATED_LIST = BACKUP_DIR / ".created.json"
RESTART_FLAG = datastore.RESTART_FLAG

PRESERVE = {"config.ini", "stats.json", "torrent_registry.json", "control.json",
            "purge_pending.json", "newgreedy.log", "trash.json", "stats_import.json"}
SKIP_PREFIX = (".git", "__pycache__", "torrents/", ".backup", "data/", "backups/", "venv/",
               "tests/")
MAX_DOWNLOAD = 25 * 1024 * 1024
ALLOWED_HOSTS = ("github.com", "api.github.com", "codeload.github.com",
                 "objects.githubusercontent.com", "release-assets.githubusercontent.com")
DOCKER_MESSAGE = ("running in Docker: the program files live in the image - update with "
                  "'docker compose pull && docker compose up -d' (or rebuild from a new clone)")

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
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or host not in ALLOWED_HOSTS:
        raise ValueError(f"refusing to download the update from an unexpected host: {host or url}")


def _download(url: str, limit=MAX_DOWNLOAD) -> bytes:
    _check_host(url)
    req = urllib.request.Request(url, headers={"User-Agent": "NewGreedy-updater"})
    with urllib.request.urlopen(req, timeout=60) as r:
        _check_host(r.geturl())
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"download is larger than {limit // (1024 * 1024)} MB")
    return data


def _pick_assets(release: dict):
    """The release zip, its .sha256 file (both required) and its .sig file ('' when absent)."""
    assets = {(a.get("name") or ""): a.get("browser_download_url", "")
              for a in release.get("assets", []) if a.get("name")}
    zips = [n for n in assets if n.lower().endswith(".zip")]
    if not zips:
        raise ValueError("this release has no .zip asset - update from the command line instead")
    name = zips[0]
    sig_url = assets.get(name + ".sig", "")
    for cand in (name + ".sha256", os.path.splitext(name)[0] + ".sha256", "SHA256", "SHA256SUMS"):
        if cand in assets:
            return name, assets[name], cand, assets[cand], sig_url
    raise ValueError(f"this release publishes no checksum file for {name} "
                     f"({name}.sha256) - refusing to install an unverified archive")


def _load_public_key(text: str):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    raw = base64.b64decode(text.strip(), validate=True)
    if len(raw) != 32:
        raise ValueError("UPDATE_SIGNING_KEY must be a base64 Ed25519 public key (32 bytes)")
    return Ed25519PublicKey.from_public_bytes(raw)


def _verify_signature(archive: bytes, sig_text: str, key_text: str = None):
    """The .sha256 file sits next to the zip in the same release, so it only catches a
    damaged download; the signature proves the archive comes from the key holder."""
    from cryptography.exceptions import InvalidSignature
    key = _load_public_key(UPDATE_SIGNING_KEY if key_text is None else key_text)
    try:
        sig = base64.b64decode(sig_text.strip(), validate=True)
        key.verify(sig, archive)
    except (InvalidSignature, ValueError):
        raise ValueError("release signature is not valid for this archive - refusing to install")


def _expected_digest(text: str, zip_name: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in lines:
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(\S+)$", ln)
        if m and os.path.basename(m.group(2)) == zip_name:
            return m.group(1).lower()
    if len(lines) == 1:
        m = re.fullmatch(r"([0-9a-fA-F]{64})(\s+\*?\S+)?", lines[0])
        if m:
            return m.group(1).lower()
    raise ValueError("checksum file is not in the expected '<sha256>  <file>' format")


def _verify_digest(archive: bytes, expected: str):
    got = hashlib.sha256(archive).hexdigest()
    if got != expected:
        raise ValueError(f"archive checksum mismatch (expected {expected[:12]}..., got {got[:12]}...)")


def _archive_root(names) -> str:
    if not names:
        return ""
    root = names[0].split("/")[0] + "/"
    return root if all(n.startswith(root) for n in names) else ""


def _inside_base(rel: str) -> bool:
    """rel names a file under BASE_DIR, also once symlinks and Windows drives are resolved."""
    base = BASE_DIR.resolve()
    try:
        return (base / rel).resolve().is_relative_to(base)
    except (OSError, ValueError):
        return False


def _safe_members(zf: zipfile.ZipFile):
    names = [n for n in zf.namelist() if not n.endswith("/")]
    if not names:
        raise ValueError("empty archive")
    root = _archive_root(names)
    for name in names:
        rel = name[len(root):] if root else name
        # ':' covers Windows drive letters (C:/...) and NTFS streams (file.py:stream)
        if (not rel or rel.startswith("/") or "\\" in rel or ":" in rel
                or ".." in rel.split("/") or not _inside_base(rel)):
            continue
        if rel.startswith(SKIP_PREFIX) or rel in PRESERVE:
            continue
        yield name, rel


def _migrate_config(zf: zipfile.ZipFile) -> str:
    target = datastore.CONFIG_FILE
    names = [n for n in zf.namelist() if not n.endswith("/")]
    want = _archive_root(names) + "config.ini"
    if want not in names:
        return ""
    shipped = zf.read(want).decode("utf-8", "replace")
    if datastore.SHIPPED_CONFIG.resolve() != target.resolve():
        atomic_write_bytes(datastore.SHIPPED_CONFIG, shipped.encode("utf-8"))
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
    created = []

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
            else:
                created.append(rel)
        CREATED_LIST.write_text(json.dumps(created), encoding="utf-8")
        old_req = (BASE_DIR / "requirements.txt").read_bytes() if (BASE_DIR / "requirements.txt").exists() else b""
        written = 0
        # the addon last: mitmproxy reloads it as soon as it changes on disk
        for name, rel in sorted(members, key=lambda m: m[1] == "newgreedy_addon.py"):
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
    new_req = (BASE_DIR / "requirements.txt").read_bytes() if (BASE_DIR / "requirements.txt").exists() else b""
    if new_req != old_req:
        note = "; ".join(filter(None, [note, _install_requirements()]))
    return written, note


def _install_requirements() -> str:
    if sys.prefix == getattr(sys, "base_prefix", sys.prefix):
        return ("dependencies changed - run 'pip install -r requirements.txt' "
                "(or the installer with --update) before restarting")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r",
                        str(BASE_DIR / "requirements.txt")], check=True, timeout=600,
                       capture_output=True)
        return "dependencies updated"
    except Exception as e:
        logger.warning("[UPDATE] pip install failed: %s", e)
        return "dependencies changed but pip failed - run 'pip install -r requirements.txt'"


def rollback() -> int:
    if not BACKUP_DIR.exists():
        return 0
    restored = 0
    for path in BACKUP_DIR.rglob("*"):
        if path.is_file() and path != CREATED_LIST:
            rel = path.relative_to(BACKUP_DIR)
            target = datastore.CONFIG_FILE if str(rel) == "config.ini" else BASE_DIR / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            restored += 1
    try:
        created = json.loads(CREATED_LIST.read_text(encoding="utf-8"))
    except Exception:
        created = []
    for rel in created:
        try:
            (BASE_DIR / rel).unlink()
            restored += 1
        except OSError:
            pass
    return restored


def _worker(tag: str, zip_url: str, sum_url: str, zip_name: str, sig_url: str = ""):
    applying = False
    try:
        _set("downloading", f"fetching {tag}", to=tag)
        expected = _expected_digest(_download(sum_url, 64 * 1024).decode("utf-8", "replace"), zip_name)
        archive = _download(zip_url)
        _verify_digest(archive, expected)
        if UPDATE_SIGNING_KEY:
            _verify_signature(archive, _download(sig_url, 4096).decode("ascii", "replace"))
            verified = "checksum and signature verified"
        else:
            verified = "checksum verified, no signing key configured"
        try:
            import backups
            backups.create("pre-update")
        except Exception as e:
            logger.warning("[UPDATE] backup before update failed: %s", e)
        _set("applying", f"extracting {len(archive) // 1024} KB ({verified})")
        applying = True
        count, cfg_note = _apply(archive)
        RESTART_FLAG.write_text(json.dumps({"tag": tag, "ts": int(time.time())}),
                                encoding="utf-8")
        _set("done", f"{count} file(s) updated to {tag}"
                     + (f"; {cfg_note}" if cfg_note else "")
                     + "; restart required")
    except Exception as e:
        _set("error", str(e))
        if not applying:
            return
        try:
            n = rollback()
            if n:
                logger.info("[UPDATE] rolled back %d file(s)", n)
        except Exception as re_:
            logger.warning("[UPDATE] rollback failed: %s", re_)


def release_info(channel="stable"):
    rel = fetch_latest(timeout=6, channel=channel)
    return {"tag": (rel.get("tag_name") or "").strip(), "name": rel.get("name") or "",
            "notes": (rel.get("body") or "")[:20000], "url": rel.get("html_url", ""),
            "prerelease": bool(rel.get("prerelease")), "published": rel.get("published_at", "")}


def start(force: bool = False, channel: str = "stable"):
    if datastore.IN_DOCKER:
        return False, DOCKER_MESSAGE
    with _lock:
        if _state["status"] in ("downloading", "applying"):
            return False, "an update is already running"
    try:
        release = fetch_latest(timeout=6, channel=channel)
    except Exception as e:
        return False, f"could not reach GitHub: {e}"

    tag = (release.get("tag_name") or "").strip()
    if not tag:
        return False, "no release tag found"
    if not force and parse_version(tag) <= parse_version(VERSION):
        return False, f"already up to date ({VERSION})"
    try:
        zip_name, zip_url, _, sum_url, sig_url = _pick_assets(release)
        _check_host(zip_url)
        _check_host(sum_url)
        if UPDATE_SIGNING_KEY:
            if not sig_url:
                raise ValueError(f"this release publishes no signature ({zip_name}.sig) - "
                                 f"refusing to install an unsigned archive")
            _check_host(sig_url)
    except ValueError as e:
        return False, str(e)

    _set("downloading", f"starting update to {tag}", to=tag)
    threading.Thread(target=_worker, args=(tag, zip_url, sum_url, zip_name, sig_url),
                     daemon=True).start()
    return True, f"update to {tag} started"


# ── release signing (maintainer side) ─────────────────────────────────────────

def _cli(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Sign NewGreedy release archives (Ed25519)")
    sub = p.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen", help="create a signing key; prints the public key for _version.py")
    k.add_argument("keyfile")
    sg = sub.add_parser("sign", help="write <archive>.sig next to the archive")
    sg.add_argument("archive")
    sg.add_argument("keyfile")
    v = sub.add_parser("verify", help="check <archive>.sig against a public key")
    v.add_argument("archive")
    v.add_argument("public_key", nargs="?", default=None,
                   help="base64 public key; UPDATE_SIGNING_KEY when omitted")
    args = p.parse_args(argv)
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if args.cmd == "keygen":
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        fd = os.open(args.keyfile, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
        pub = key.public_key().public_bytes(serialization.Encoding.Raw,
                                            serialization.PublicFormat.Raw)
        print(f'UPDATE_SIGNING_KEY = "{base64.b64encode(pub).decode()}"')
        print(f"private key written to {args.keyfile} - keep it out of the repository")
        return 0
    with open(args.archive, "rb") as f:
        archive = f.read()
    if args.cmd == "sign":
        with open(args.keyfile, "rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        with open(args.archive + ".sig", "w", encoding="ascii") as f:
            f.write(base64.b64encode(key.sign(archive)).decode() + "\n")
        print(f"{args.archive}.sig written")
        return 0
    with open(args.archive + ".sig", encoding="ascii") as f:
        _verify_signature(archive, f.read(), args.public_key)
    print("signature OK")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
