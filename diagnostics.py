#!/usr/bin/env python3
"""NewGreedy — service self-check, shared by --check-config and the Status page"""
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
import os
import pathlib
import shutil
import socket
import tempfile
import time

import config_schema as cs
import datastore
from _version import VERSION

HEARTBEAT_STALE_S = 30


def mitm_confdir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("NEWGREEDY_MITM_CONFDIR") or "~/.mitmproxy").expanduser()


def bcrypt_conflict() -> str:
    """bcrypt version when it breaks mitmproxy, else ''. Only mitmproxy 10 and 11 hash
    with passlib, which bcrypt 5 crashes; mitmproxy 12 calls bcrypt directly."""
    from importlib import metadata

    def major(name):
        try:
            head = metadata.version(name).split(".")[0]
        except Exception:
            return None
        return int(head) if head.isdigit() else None

    mitm, bc = major("mitmproxy"), major("bcrypt")
    if mitm is None or mitm >= 12 or bc is None or bc < 5 or major("passlib") is None:
        return ""
    return metadata.version("bcrypt")


def can_bind(host, port) -> bool:
    """True when a listening socket could take host:port right now."""
    fam = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(fam, socket.SOCK_STREAM) as s:
            if os.name == "nt":
                # on Windows SO_REUSEADDR lets bind() succeed over a port in use
                s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", -5), 1)
            else:
                # skip TIME_WAIT leftovers; a listening socket still makes bind() fail
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
        return True
    except OSError:
        return False


def _port_in_use(host, port):
    return not can_bind(host, port)


def _single_file_mounts():
    """Files bind-mounted one by one into the container (the v1.7.7 compose layout)."""
    found = []
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) > 4:
                    target = parts[4]
                    if target.startswith("/app/") and os.path.isfile(target):
                        found.append(target)
    except OSError:
        pass
    return found


def run(c, live=False):
    """Returns a list of {level, id, title, detail}. live=True when called by the running service."""
    out = []

    def add(level, cid, title, detail=""):
        out.append({"level": level, "id": cid, "title": title, "detail": detail})

    for level, section, key, msg in cs.check(c):
        name = f"[{section}] {key}".strip()
        add("warn" if level == "error" else "info", "config",
            f"{name}: {msg}", "The default value is used instead." if level == "error" else "")
    if not any(o["id"] == "config" and o["level"] == "warn" for o in out):
        add("ok", "config", "config.ini is valid", str(datastore.CONFIG_FILE))

    proxy_host = cs.get(c, "proxy", "listen_host")
    proxy_port = cs.get(c, "proxy", "listen_port")
    web_on     = cs.get(c, "web", "web_enabled")
    web_host   = cs.get(c, "web", "web_host")
    web_port   = cs.get(c, "web", "web_port")
    token      = cs.get(c, "web", "web_secret_token")

    if cs.is_loopback(proxy_host):
        add("ok", "proxy_bind", f"Proxy listens on {proxy_host}:{proxy_port}", "Only this machine can use it.")
    else:
        add("warn", "proxy_bind", f"Proxy listens on {proxy_host}:{proxy_port}",
            "Any machine that reaches this port can use the proxy. Keep it on 127.0.0.1 unless "
            "your torrent client runs on another host or container.")
    if web_on:
        if cs.is_loopback(web_host):
            add("ok", "web_bind", f"Web UI on {web_host}:{web_port}",
                "Token " + ("required." if token else "not set - fine for local use."))
        elif token:
            add("ok", "web_bind", f"Web UI on {web_host}:{web_port}", "Protected by web_secret_token.")
        else:
            add("error", "web_bind", f"Web UI on {web_host}:{web_port} without a token",
                "A token is generated at the next start.")
    if web_on and proxy_port == web_port:
        add("error", "ports", "listen_port and web_port are the same", str(proxy_port))
    if not live:
        for label, host, port in (("Proxy", proxy_host, proxy_port), ("Web UI", web_host, web_port)):
            if label == "Web UI" and not web_on:
                continue
            if _port_in_use(host, port):
                add("warn", "ports", f"{label} port {port} is already in use",
                    "Another program, or NewGreedy already running.")

    try:
        datastore.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=datastore.DATA_DIR, prefix=".probe_"):
            pass
        free = shutil.disk_usage(datastore.DATA_DIR).free
        level = "ok" if free > 100 * 1024 * 1024 else "warn"
        add(level, "data_dir", f"Data folder is writable: {datastore.DATA_DIR}",
            f"{free // (1024 * 1024)} MB free")
    except OSError as e:
        add("error", "data_dir", f"Data folder is not writable: {datastore.DATA_DIR}", str(e))

    ca = mitm_confdir() / "mitmproxy-ca-cert.pem"
    if ca.is_file():
        add("ok", "ca", "mitmproxy CA certificate found", str(ca))
    else:
        add("warn", "ca", "mitmproxy CA certificate not found yet",
            f"{ca} is created at the first start; HTTPS trackers need it trusted by the torrent client.")

    bv = bcrypt_conflict()
    if bv:
        add("error", "bcrypt", f"bcrypt {bv} is installed",
            "It crashes the passlib version mitmproxy uses. Remove it: pip uninstall bcrypt")

    if datastore.IN_DOCKER:
        mounts = _single_file_mounts()
        if mounts:
            add("error", "docker", "Data files are mounted one by one",
                "Saving them fails in this layout. Mount the data/ folder instead: " + ", ".join(mounts))
        else:
            add("ok", "docker", "Docker layout", f"data folder {datastore.DATA_DIR}")

    if live:
        st = datastore.read_status()
        age = time.time() - float(st.get("heartbeat", 0) or 0)
        if not st:
            add("error", "engine", "Engine status unknown", "The mitmproxy addon has not reported yet.")
        elif st.get("state") == "failed":
            add("error", "engine", "Engine failed to start", st.get("error", ""))
        elif age > HEARTBEAT_STALE_S:
            add("error", "engine", "Engine not responding", f"last heartbeat {int(age)} s ago")
        else:
            add("ok", "engine", f"Engine running ({st.get('version', '?')})",
                f"{st.get('torrents', 0)} torrent(s), {st.get('announces', 0)} announce(s) this session")
        if st.get("version") and st.get("version") != VERSION:
            add("warn", "engine", "Engine and web UI run different versions",
                f"engine {st.get('version')}, web UI {VERSION} - restart NewGreedy")
        if datastore.RESTART_FLAG.exists():
            add("warn", "update", "An update is waiting for a restart", "")
    return out


def summary(checks):
    levels = [c["level"] for c in checks]
    return "error" if "error" in levels else "warn" if "warn" in levels else "ok"


def print_report(checks):
    marks = {"ok": "  OK ", "info": " INFO", "warn": " WARN", "error": "ERROR"}
    for c in checks:
        print(f"[{marks.get(c['level'], c['level'])}] {c['title']}")
        if c["detail"]:
            print(f"        {c['detail']}")
    print(f"\nResult: {summary(checks).upper()}")
