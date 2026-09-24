#!/usr/bin/env python3
"""NewGreedy v1.7.7 — version, bencode and webhook helpers"""
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
import json
import logging
import os
import tempfile
import threading
import time
import urllib.request

VERSION     = "v1.7.7"
GITHUB_REPO = "Mrt0t0/NewGreedy"

logger = logging.getLogger("NewGreedy.Common")


def parse_version(v: str):
    parts = []
    for p in v.lstrip("v").strip().split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def fetch_latest(timeout=4):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": "NewGreedy-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def atomic_write_bytes(path, data: bytes):
    path = str(path)
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path, data):
    atomic_write_bytes(path, json.dumps(data, indent=2).encode("utf-8"))


def bd_str(data, pos):
    p = pos
    while data[p:p + 1].isdigit():
        p += 1
    length = int(data[pos:p])
    return data[p + 1:p + 1 + length], p + 1 + length


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


def _total_length(info: bytes) -> int:
    single = bd_find_int(info, b"length")
    if single >= 0:
        return single
    start = info.find(b"5:filesl")
    if start == -1:
        return 0
    total = 0
    pos = start + len(b"5:filesl")
    while pos < len(info) and info[pos:pos + 1] == b"d":
        pos += 1
        while pos < len(info) and info[pos:pos + 1] != b"e":
            key, pos = bd_str(info, pos)
            if key == b"length":
                n, pos = bd_int(info, pos)
                total += n
            else:
                pos = bd_skip(info, pos)
        pos += 1
    return total


def torrent_info(raw: bytes):
    marker = b"4:info"
    idx = raw.find(marker)
    if idx == -1:
        raise ValueError("No info key in torrent file")
    start = idx + len(marker)
    pos, depth = start, 0
    while pos < len(raw):
        c = raw[pos:pos + 1]
        if c in (b"d", b"l"):
            depth += 1
            pos += 1
        elif c == b"i":
            pos = raw.index(b"e", pos + 1) + 1
        elif c == b"e":
            depth -= 1
            pos += 1
            if depth == 0:
                break
        elif c.isdigit():
            p = pos
            while raw[p:p + 1].isdigit():
                p += 1
            pos = p + 1 + int(raw[pos:p])
        else:
            raise ValueError(f"Invalid bencode byte at pos {pos}: {c!r}")
    info = raw[start:pos]
    return hashlib.sha1(info).hexdigest(), _total_length(info)


def tracker_urls(raw: bytes):
    urls = []
    idx = raw.find(b"8:announce")
    if idx != -1 and raw[idx + 10:idx + 11] != b"-":
        try:
            s, _ = bd_str(raw, idx + 10)
            urls.append(s.decode("utf-8", "replace"))
        except Exception:
            pass
    idx = raw.find(b"13:announce-list")
    if idx != -1:
        pos = idx + 16
        if raw[pos:pos + 1] == b"l":
            pos += 1
            depth = 1
            while pos < len(raw) and depth > 0:
                c = raw[pos:pos + 1]
                if c == b"l":
                    depth += 1
                    pos += 1
                elif c == b"e":
                    depth -= 1
                    pos += 1
                elif c.isdigit():
                    try:
                        s, pos = bd_str(raw, pos)
                        urls.append(s.decode("utf-8", "replace"))
                    except Exception:
                        break
                else:
                    break
    seen, clean = set(), []
    for u in urls:
        u = u.strip()
        if u and "://" in u and u not in seen:
            seen.add(u)
            clean.append(u)
    return clean


NOTIFY_EVENTS = {"target_reached", "tracker_down", "tracker_up", "global_cap", "purged"}

_notify = {"url": "", "fmt": "generic", "events": set()}
_notify_sent = {}
_notify_lock = threading.Lock()
NOTIFY_DEDUP_S = 300


def notify_configure(url: str, fmt: str, events: str):
    _notify["url"] = (url or "").strip()
    _notify["fmt"] = (fmt or "generic").strip().lower()
    _notify["events"] = {e.strip().lower() for e in (events or "").split(",") if e.strip()} & NOTIFY_EVENTS
    if _notify["url"] and _notify["events"]:
        logger.info("Webhook enabled (%s) for: %s", _notify["fmt"], ", ".join(sorted(_notify["events"])))


def notify_enabled() -> bool:
    return bool(_notify["url"] and _notify["events"])


def _notify_body(event: str, title: str, message: str) -> bytes:
    fmt = _notify["fmt"]
    if fmt == "discord":
        body = {"embeds": [{"title": title, "description": message, "color": 5814783}]}
    elif fmt == "slack":
        body = {"text": f"*{title}*\n{message}"}
    else:
        body = {"event": event, "title": title, "message": message, "ts": int(time.time())}
    return json.dumps(body).encode("utf-8")


def _notify_post(data: bytes):
    try:
        req = urllib.request.Request(
            _notify["url"], data=data,
            headers={"Content-Type": "application/json", "User-Agent": "NewGreedy"})
        urllib.request.urlopen(req, timeout=5).close()
    except Exception as e:
        logger.debug("webhook delivery failed: %s", e)


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
    threading.Thread(target=_notify_post, args=(_notify_body(event, title, message),),
                     daemon=True).start()
