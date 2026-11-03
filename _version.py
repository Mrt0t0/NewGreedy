#!/usr/bin/env python3
"""NewGreedy — version, bencode and webhook helpers"""
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
import errno
import hashlib
import json
import logging
import os
import tempfile
import threading
import time
import urllib.parse
import urllib.request

VERSION     = "v1.7.8"
GITHUB_REPO = "Mrt0t0/NewGreedy"
# base64 Ed25519 public key that release zips are signed with (python3 updater.py keygen);
# when set, the web UI updater requires <zip>.sig and refuses anything else
UPDATE_SIGNING_KEY = ""

logger = logging.getLogger("NewGreedy.Common")


def parse_version(v: str):
    parts = []
    for p in (v or "").lstrip("v").strip().split("-")[0].split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _github_get(path, timeout):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/{path}"
    req = urllib.request.Request(url, headers={"User-Agent": "NewGreedy-updater",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def fetch_latest(timeout=4, channel="stable"):
    if (channel or "stable").strip().lower() != "beta":
        return _github_get("releases/latest", timeout)
    releases = _github_get("releases?per_page=10", timeout)
    for rel in releases if isinstance(releases, list) else []:
        if not rel.get("draft"):
            return rel
    raise ValueError("no published release found")


def atomic_write_bytes(path, data: bytes):
    path = str(path)
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.replace(tmp, path)
        except OSError as e:
            if e.errno not in (errno.EBUSY, errno.EXDEV, errno.EPERM):
                raise
            # a file bind-mounted on its own cannot be renamed over: write in place
            with open(path, "r+b" if os.path.exists(path) else "wb") as f:
                f.seek(0)
                f.write(data)
                f.truncate()
                f.flush()
                os.fsync(f.fileno())
            os.remove(tmp)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path, data):
    atomic_write_bytes(path, json.dumps(data, indent=2).encode("utf-8"))


# ── bencode ──────────────────────────────────────────────────────────────────

BDECODE_MAX_DEPTH = 64


def bdecode(data: bytes, pos: int = 0, depth: int = 0):
    """Decode one bencoded value at pos; returns (value, next_pos). Strings stay bytes."""
    if depth > BDECODE_MAX_DEPTH:
        raise ValueError("bencode nesting too deep")
    c = data[pos:pos + 1]
    if c == b"i":
        end = data.index(b"e", pos + 1)
        return int(data[pos + 1:end]), end + 1
    if c == b"l":
        pos += 1
        out = []
        while data[pos:pos + 1] != b"e":
            if pos >= len(data):
                raise ValueError("unterminated list")
            v, pos = bdecode(data, pos, depth + 1)
            out.append(v)
        return out, pos + 1
    if c == b"d":
        pos += 1
        out = {}
        while data[pos:pos + 1] != b"e":
            if pos >= len(data):
                raise ValueError("unterminated dict")
            k, pos = bd_str(data, pos)
            v, pos = bdecode(data, pos, depth + 1)
            out[k] = v
        return out, pos + 1
    if c.isdigit():
        return bd_str(data, pos)
    raise ValueError(f"invalid bencode byte at pos {pos}: {c!r}")


def bd_str(data, pos):
    p = pos
    while data[p:p + 1].isdigit():
        p += 1
    if data[p:p + 1] != b":":
        raise ValueError(f"invalid bencode string at pos {pos}")
    length = int(data[pos:p])
    end = p + 1 + length
    if end > len(data):
        raise ValueError("bencode string runs past the end")
    return data[p + 1:end], end


def bd_int(data, pos):
    end = data.index(b"e", pos + 1)
    return int(data[pos + 1:end]), end + 1


def bd_skip(data, pos):
    c = data[pos:pos + 1]
    if c == b"i":
        return data.index(b"e", pos + 1) + 1
    if c in (b"d", b"l"):
        pos += 1
        while data[pos:pos + 1] != b"e":
            if pos >= len(data):
                raise ValueError("unterminated container")
            pos = bd_skip(data, pos)
        return pos + 1
    if c.isdigit():
        p = pos
        while data[p:p + 1].isdigit():
            p += 1
        return p + 1 + int(data[pos:p])
    return pos + 1


def bd_find_int(data: bytes, key: bytes) -> int:
    try:
        marker = str(len(key)).encode() + b":" + key
        idx = data.find(marker)
        if idx == -1:
            return -1
        idx += len(marker)
        if data[idx:idx + 1] == b"i":
            return int(data[idx + 1:data.index(b"e", idx + 1)])
    except Exception:
        pass
    return -1


def bd_top_str(data: bytes, key: bytes, max_len: int = 256) -> bytes:
    try:
        if not data.startswith(b"d"):
            return b""
        pos = 1
        while pos < len(data) and data[pos:pos + 1] != b"e":
            k, pos = bd_str(data, pos)
            if k != key:
                pos = bd_skip(data, pos)
                continue
            if not data[pos:pos + 1].isdigit():
                return b""
            val, _ = bd_str(data, pos)
            return val if 0 < len(val) <= max_len else b""
    except Exception:
        pass
    return b""


def bd_replace_int(data: bytes, key: bytes, value: int) -> bytes:
    try:
        marker = str(len(key)).encode() + b":" + key
        idx = data.find(marker)
        if idx == -1:
            return data
        start = idx + len(marker)
        if data[start:start + 1] != b"i":
            return data
        end = data.index(b"e", start + 1)
        return data[:start] + b"i" + str(int(value)).encode() + b"e" + data[end + 1:]
    except Exception:
        return data


def _info_span(raw: bytes):
    """Byte span of the top-level info dict, found by walking the top-level keys."""
    if not raw.startswith(b"d"):
        raise ValueError("a .torrent file starts with a dictionary")
    pos = 1
    while pos < len(raw) and raw[pos:pos + 1] != b"e":
        key, pos = bd_str(raw, pos)
        start = pos
        pos = bd_skip(raw, pos)
        if key == b"info":
            return start, pos
    raise ValueError("No info key in torrent file")


def _total_length(info: dict) -> int:
    single = info.get(b"length")
    if isinstance(single, int):
        return max(0, single)
    total = 0
    for f in info.get(b"files", []) or []:
        if isinstance(f, dict) and isinstance(f.get(b"length"), int):
            total += max(0, f[b"length"])
    return total


def torrent_info(raw: bytes):
    start, end = _info_span(raw)
    info, _ = bdecode(raw, start)
    if not isinstance(info, dict):
        raise ValueError("info is not a dictionary")
    return hashlib.sha1(raw[start:end]).hexdigest(), _total_length(info)


def torrent_name(raw: bytes) -> str:
    try:
        start, _ = _info_span(raw)
        info, _ = bdecode(raw, start)
        name = info.get(b"name.utf-8") or info.get(b"name") or b""
        return name.decode("utf-8", "replace") if isinstance(name, bytes) else ""
    except Exception:
        return ""


def tracker_urls(raw: bytes):
    urls = []
    try:
        top, _ = bdecode(raw)
    except Exception:
        top = {}
    if isinstance(top, dict):
        ann = top.get(b"announce")
        if isinstance(ann, bytes):
            urls.append(ann.decode("utf-8", "replace"))
        for tier in top.get(b"announce-list", []) or []:
            for u in tier if isinstance(tier, list) else [tier]:
                if isinstance(u, bytes):
                    urls.append(u.decode("utf-8", "replace"))
    seen, clean = set(), []
    for u in urls:
        u = u.strip()
        if u and "://" in u and u not in seen:
            seen.add(u)
            clean.append(u)
    return clean


# ── webhooks ─────────────────────────────────────────────────────────────────

NOTIFY_EVENTS  = {"target_reached", "tracker_down", "tracker_up", "global_cap", "purged",
                  "engine_failed"}
NOTIFY_FORMATS = ("generic", "discord", "slack", "ntfy", "gotify", "telegram")

_notify = {"url": "", "fmt": "generic", "events": set(), "chat_id": ""}
_notify_sent = {}
_notify_lock = threading.Lock()
NOTIFY_DEDUP_S = 300


def notify_configure(url: str, fmt: str, events: str, chat_id: str = ""):
    url = (url or "").strip()
    if url and urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        logger.warning("webhook_url must start with http:// or https:// - notifications disabled")
        url = ""
    fmt = (fmt or "generic").strip().lower()
    if fmt not in NOTIFY_FORMATS:
        logger.warning("webhook_format '%s' unknown - using generic", fmt)
        fmt = "generic"
    changed = (url, fmt) != (_notify["url"], _notify["fmt"])
    _notify["url"] = url
    _notify["fmt"] = fmt
    _notify["chat_id"] = (chat_id or "").strip()
    _notify["events"] = {e.strip().lower() for e in (events or "").split(",") if e.strip()} & NOTIFY_EVENTS
    if changed and _notify["url"] and _notify["events"]:
        logger.info("Webhook enabled (%s) for: %s", _notify["fmt"], ", ".join(sorted(_notify["events"])))


def notify_enabled() -> bool:
    return bool(_notify["url"] and _notify["events"])


def _notify_request(event: str, title: str, message: str):
    fmt, url = _notify["fmt"], _notify["url"]
    headers = {"User-Agent": "NewGreedy", "Content-Type": "application/json"}
    if fmt == "discord":
        body = {"embeds": [{"title": title, "description": message, "color": 5814783}]}
    elif fmt == "slack":
        body = {"text": f"*{title}*\n{message}"}
    elif fmt == "ntfy":
        headers = {"User-Agent": "NewGreedy", "Content-Type": "text/plain; charset=utf-8",
                   "Title": title.encode("ascii", "replace").decode(), "Tags": event}
        return url, message.encode("utf-8"), headers
    elif fmt == "gotify":
        body = {"title": title, "message": message, "priority": 5}
    elif fmt == "telegram":
        body = {"chat_id": _notify["chat_id"], "text": f"{title}\n{message}"}
    else:
        body = {"event": event, "title": title, "message": message, "ts": int(time.time())}
    return url, json.dumps(body).encode("utf-8"), headers


def _notify_post(url, data, headers):
    try:
        req = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as r:
            return True, f"HTTP {r.status}"
    except Exception as e:
        logger.debug("webhook delivery failed: %s", e)
        return False, str(e)


def notify(event: str, title: str, message: str, dedup_key: str = ""):
    if not notify_enabled() or event not in _notify["events"]:
        return
    key = dedup_key or f"{event}:{title}"
    now = time.time()
    with _notify_lock:
        if now - _notify_sent.get(key, 0) < NOTIFY_DEDUP_S:
            return
        _notify_sent[key] = now
        if len(_notify_sent) > 512:
            for k in [k for k, v in list(_notify_sent.items()) if now - v > NOTIFY_DEDUP_S]:
                del _notify_sent[k]
    threading.Thread(target=_notify_post, args=_notify_request(event, title, message),
                     daemon=True).start()


def notify_test():
    """Send a test message right away; returns (ok, detail)."""
    if not _notify["url"]:
        return False, "webhook_url is empty"
    if _notify["fmt"] == "telegram" and not _notify["chat_id"]:
        return False, "webhook_chat_id is required for telegram"
    return _notify_post(*_notify_request("test", "NewGreedy test",
                                         f"Test notification from NewGreedy {VERSION}."))


if __name__ == "__main__":
    # single source of the version for installers and image builds
    print(VERSION)
