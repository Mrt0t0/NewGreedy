#!/usr/bin/env python3
"""NewGreedy — Web UI & API"""
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
import hmac, io, json, os, pathlib, logging, asyncio, re, time, threading, zipfile
import urllib.parse
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Query, Request, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import (JSONResponse, HTMLResponse, StreamingResponse, PlainTextResponse,
                               RedirectResponse, Response)
from _version import (VERSION, parse_version, notify_configure, notify_test,
                      torrent_info, torrent_name, tracker_urls, atomic_write_bytes)
import backups
import client_profiles
import config_schema as cs
import datastore
import diagnostics
import updater


@asynccontextmanager
async def _lifespan(_app):
    task = asyncio.create_task(_watch_loop())
    try:
        yield
    finally:
        task.cancel()


app      = FastAPI(title="NewGreedy Web UI", lifespan=_lifespan)
_cfg     = None
logging.getLogger("passlib").setLevel(logging.ERROR)
logger   = logging.getLogger("NewGreedy.Web")
BASE_DIR = datastore.BASE_DIR
STATS_FILE    = datastore.STATS_FILE
REGISTRY_FILE = datastore.REGISTRY_FILE
LOG_FILE      = datastore.LOG_FILE
STATIC_DIR    = datastore.STATIC_DIR
CONFIG_FILE   = datastore.CONFIG_FILE
CONTROL_FILE  = datastore.CONTROL_FILE

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

VALID_HASH_RE   = re.compile(r"^[0-9a-f]{8,40}$")
MAX_UPLOAD_SIZE = 10 * 1024 * 1024
TOKEN_COOKIE    = "ng_token"
WATCH_INTERVAL  = 15
LOGIN_RATE      = 10
PAGES           = {"torrents", "charts", "config", "logs", "help", "status"}
PAGE_PATHS      = {"/"} | {"/" + p for p in PAGES}
PUBLIC_PATHS    = {"/api/health", "/api/login", "/api/logout"}
SAFE_METHODS    = {"GET", "HEAD", "OPTIONS"}
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
       "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self' ws: wss:; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

_SECRET_TOKEN    = ""
_PATH_PREFIX     = ""
_RATE_LIMIT      = 0
_WATCH_FOLDER    = ""
_ALLOWED_ORIGINS = set()
_cfg_mtime       = 0.0

_rate_buckets = defaultdict(deque)
_rate_lock    = threading.Lock()

_raw_cache      = None
_raw_cache_ts   = 0.0
_raw_cache_ttl  = 2.0

EDITABLE_KEYS = cs.EDITABLE_KEYS
# URLs that can carry a secret: True = the path too (webhook tokens), False = the password only
MASKED_URLS = {("notifications", "webhook_url"): True, ("upstream", "proxy_url"): False}
MASK = "***"


def set_config(c):
    global _cfg, _SECRET_TOKEN, _PATH_PREFIX, _RATE_LIMIT, _WATCH_FOLDER, _ALLOWED_ORIGINS, _cfg_mtime
    first = _cfg is None
    _cfg = c
    _cfg_mtime = _mtime(CONFIG_FILE)
    _SECRET_TOKEN = cs.get(c, "web", "web_secret_token")
    raw_token = c.get("web", "web_secret_token", fallback="").strip() if c is not None else ""
    if raw_token and not _SECRET_TOKEN:
        # a token was meant but cannot be used: lock the UI rather than open it
        _SECRET_TOKEN = cs.new_token()
        logger.error("web_secret_token in config.ini is not valid (8 to 256 printable characters, "
                     "no spaces) - the web UI refuses every sign-in until it is fixed")
    _PATH_PREFIX  = cs.get(c, "web", "web_path_prefix")
    _RATE_LIMIT   = cs.get(c, "web", "web_rate_limit")
    _WATCH_FOLDER = (cs.get(c, "advanced", "watch_folder") or "").strip()
    _ALLOWED_ORIGINS = {o.rstrip("/") for o in cs.get(c, "web", "web_allowed_origins")}
    try:
        notify_configure(cs.get(c, "notifications", "webhook_url"),
                         cs.get(c, "notifications", "webhook_format"),
                         cs.get(c, "notifications", "webhook_events"),
                         cs.get(c, "notifications", "webhook_chat_id"))
    except Exception as e:
        logger.debug("notifier config error: %s", e)
    if not first:
        return
    host = cs.get(c, "web", "web_host")
    if _SECRET_TOKEN:
        logger.info("Web UI authentication enabled")
    elif not cs.is_loopback(host):
        logger.warning("Web UI bound to %s with no web_secret_token - anyone who can reach "
                       "port %s may read stats, purge data, restore backups and trigger updates",
                       host, cs.get(c, "web", "web_port"))
    if _PATH_PREFIX:
        logger.info("Web UI served under /%s/", _PATH_PREFIX)
    if _WATCH_FOLDER:
        logger.info("Watch folder enabled: %s", datastore.resolve_data_path(_WATCH_FOLDER))


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _config():
    """The current config.ini, re-read when the file changed (UI edit, restore, hand edit)."""
    m = _mtime(CONFIG_FILE)
    if _cfg is None or (m and m != _cfg_mtime):
        set_config(cs.load(CONFIG_FILE))
    return _cfg


# ── access control ────────────────────────────────────────────────────────────

def _rate_ok(ip: str, bucket_name: str = "api", limit: int = None) -> bool:
    limit = _RATE_LIMIT if limit is None else limit
    if limit <= 0:
        return True
    now = time.monotonic()
    with _rate_lock:
        bucket = _rate_buckets[(bucket_name, ip)]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(_rate_buckets) > 512:
            for k in [k for k, v in list(_rate_buckets.items()) if not v]:
                del _rate_buckets[k]
        if len(bucket) >= limit:
            return False
        bucket.append(now)
    return True


def _same_token(supplied: str) -> bool:
    return bool(_SECRET_TOKEN) and hmac.compare_digest(
        (supplied or "").encode("utf-8"), _SECRET_TOKEN.encode("utf-8"))


def _token_ok(conn) -> bool:
    if not _SECRET_TOKEN:
        return True
    supplied = (conn.headers.get("X-Auth-Token")
                or conn.cookies.get(TOKEN_COOKIE)
                or conn.query_params.get("token")
                or "")
    return _same_token(supplied)


def _host_ok(conn) -> bool:
    """Without a token, only answer to loopback names (or a configured origin): a page
    whose DNS name is rebound to 127.0.0.1 would otherwise pass as same-origin."""
    if _SECRET_TOKEN:
        return True
    host = urllib.parse.urlsplit("//" + (conn.headers.get("host") or "")).hostname or ""
    if cs.is_loopback(host):
        return True
    return any(urllib.parse.urlsplit(o).hostname == host for o in _ALLOWED_ORIGINS)


def _origin_ok(conn) -> bool:
    """Reject requests a browser sends on behalf of another site (CSRF, cross-site WebSocket)."""
    origin = conn.headers.get("origin")
    if origin is None:
        ref = conn.headers.get("referer")
        if not ref:
            return conn.headers.get("sec-fetch-site", "same-origin") in ("same-origin", "none")
        origin = ref
    if origin == "null":
        return False
    parts = urllib.parse.urlsplit(origin)
    if parts.netloc.lower() == (conn.headers.get("host") or "").lower():
        return True
    return f"{parts.scheme}://{parts.netloc}".lower() in _ALLOWED_ORIGINS


def _set_cookie(response, request, token):
    response.set_cookie(TOKEN_COOKIE, token, httponly=True, samesite="strict", path="/",
                        secure=request.url.scheme == "https")


def _page_file(name):
    f = STATIC_DIR / f"{name}.html"
    if not f.exists():
        return f"<h1>NewGreedy {VERSION}</h1>"
    # cache-busting query strings follow the running version
    return f.read_text(encoding="utf-8").replace("__NG_VERSION__", VERSION.lstrip("v"))


@app.middleware("http")
async def _guard(request: Request, call_next):
    _config()
    path = request.url.path
    client_ip = request.client.host if request.client else "unknown"
    if path == "/api/health" or path.startswith("/static/"):
        return await call_next(request)
    if not _host_ok(request):
        return JSONResponse({"error": "unexpected Host header - set web_secret_token or "
                                      "web_allowed_origins to reach the UI by this name"},
                            status_code=421)
    rate_limited = path.startswith("/api/") or path == "/metrics"
    if rate_limited and not _rate_ok(client_ip):
        return JSONResponse({"error": "rate limit exceeded"}, status_code=429)
    if request.method not in SAFE_METHODS and not _origin_ok(request):
        return JSONResponse({"error": "cross-site request refused"}, status_code=403)
    if path in PUBLIC_PATHS:
        if path == "/api/login" and not _rate_ok(client_ip, "login", LOGIN_RATE):
            return JSONResponse({"error": "too many attempts, wait a minute"}, status_code=429)
        return await call_next(request)
    if not _token_ok(request):
        if not rate_limited and not _rate_ok(client_ip):
            return JSONResponse({"error": "rate limit exceeded"}, status_code=429)
        if request.method == "GET" and path in PAGE_PATHS:
            return HTMLResponse(_page_file("login"), status_code=200,
                                headers={"Cache-Control": "no-store", "Content-Security-Policy": CSP})
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if (_SECRET_TOKEN and request.method == "GET" and path in PAGE_PATHS
            and request.query_params.get("token")):
        query = [(k, v) for k, v in request.query_params.multi_items() if k != "token"]
        target = (("/" + _PATH_PREFIX) if _PATH_PREFIX else "") + (path if path != "/" else "/")
        if query:
            target += "?" + urllib.parse.urlencode(query)
        response = RedirectResponse(target, status_code=303)
        _set_cookie(response, request, _SECRET_TOKEN)
        return response
    response = await call_next(request)
    if path in PAGE_PATHS:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers.setdefault("Content-Security-Policy", CSP)
    return response


class PrefixMiddleware:
    """Serve everything under /<web_path_prefix>/, HTTP and WebSocket alike."""

    def __init__(self, asgi_app):
        self.app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and _PATH_PREFIX:
            path = scope.get("path", "")
            want = "/" + _PATH_PREFIX
            if path.startswith("/static/") or path == "/api/health":
                pass
            elif path == want or path.startswith(want + "/"):
                new = path[len(want):] or "/"
                scope = dict(scope, path=new, raw_path=new.encode())
            elif scope["type"] == "http":
                await PlainTextResponse("Not found", status_code=404)(scope, receive, send)
                return
            else:
                await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)


app.add_middleware(PrefixMiddleware)


@app.post("/api/login")
async def api_login(request: Request, payload: dict = Body(default={})):
    token = str(payload.get("token", ""))
    if not _SECRET_TOKEN:
        return {"status": "ok", "auth": False}
    if not _same_token(token):
        return JSONResponse({"error": "wrong token"}, status_code=401)
    response = JSONResponse({"status": "ok", "auth": True})
    _set_cookie(response, request, _SECRET_TOKEN)
    return response


@app.post("/api/logout")
async def api_logout():
    response = JSONResponse({"status": "ok"})
    response.delete_cookie(TOKEN_COOKIE, path="/")
    return response


@app.get("/api/token")
def api_token_info():
    c = _config()
    return {"enabled": bool(_SECRET_TOKEN), "web_host": cs.get(c, "web", "web_host"),
            "loopback": cs.is_loopback(cs.get(c, "web", "web_host")),
            "hint": ("…" + _SECRET_TOKEN[-4:]) if _SECRET_TOKEN else ""}


@app.post("/api/token/rotate")
def api_token_rotate(request: Request):
    token = cs.new_token()
    try:
        cs.set_value(CONFIG_FILE, "web", "web_secret_token", token)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    set_config(cs.load(CONFIG_FILE))
    logger.info("[TOKEN] web UI token rotated")
    response = JSONResponse({"status": "ok", "token": token,
                             "note": "copy it now - it is not shown again"})
    _set_cookie(response, request, token)
    return response


@app.post("/api/token/clear")
def api_token_clear():
    c = _config()
    if not cs.is_loopback(cs.get(c, "web", "web_host")):
        return JSONResponse({"error": "the web UI listens beyond this machine - a token is required"},
                            status_code=409)
    cs.set_value(CONFIG_FILE, "web", "web_secret_token", "")
    set_config(cs.load(CONFIG_FILE))
    logger.info("[TOKEN] web UI token removed")
    return {"status": "ok"}


# ── data helpers ──────────────────────────────────────────────────────────────

def _read_stats_file(cached=True):
    global _raw_cache, _raw_cache_ts
    now = time.monotonic()
    if cached and _raw_cache is not None and (now - _raw_cache_ts) < _raw_cache_ttl:
        return _raw_cache
    raw = datastore.read_json(STATS_FILE, {})
    raw = raw if isinstance(raw, dict) else {}
    _raw_cache    = raw
    _raw_cache_ts = now
    return raw


def _invalidate_cache():
    global _raw_cache
    _raw_cache = None


def _sanitize_filename(name: str) -> str:
    if not name:
        return ""
    name = re.sub(r"[\x00-\x1f\x7f]", " ", name)
    name = re.sub(r"[\\/]", "_", name)
    return name[:255].strip()


def _load_control():
    data = datastore.read_json(CONTROL_FILE, {})
    data = data if isinstance(data, dict) else {}
    return {
        "paused":    list(data.get("paused", [])),
        "overrides": dict(data.get("overrides", {})),
        "tags":      dict(data.get("tags", {})),
    }


def _edit_control(fn):
    def apply(data):
        data.setdefault("paused", [])
        data.setdefault("overrides", {})
        data.setdefault("tags", {})
        return fn(data)
    try:
        return datastore.update_json(CONTROL_FILE, apply, {}), None
    except Exception as e:
        logger.warning("control write error: %s", e)
        return None, JSONResponse({"error": "could not write control file"}, status_code=500)


def _load_registry():
    data = datastore.read_json(REGISTRY_FILE, {})
    return data if isinstance(data, dict) else {}


def _load_stats():
    raw = _read_stats_file()
    return {
        k: v for k, v in raw.items()
        if not k.startswith("_")
        and VALID_HASH_RE.match(k)
        and isinstance(v, dict)
        and "cumul_rep_ul" in v
    }


def _load_tracker_cumul():
    tc = _read_stats_file().get("_tracker_cumul", {})
    return tc if isinstance(tc, dict) else {}


def _tail_log(n=500):
    try:
        approx = max(32768, n * 256)
        with open(LOG_FILE, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            pos  = max(0, size - approx)
            f.seek(pos)
            buf  = f.read()
        lines = buf.decode("utf-8", errors="replace").splitlines()
        if pos > 0 and lines:
            lines = lines[1:]
        return lines[-n:]
    except Exception:
        return []


def _tracker_cap(c, domain):
    base = cs.get(c, "spoofing", "max_global_ratio_per_tracker")
    for section in c.sections():
        if section.lower() == f"tracker:{domain}".lower() and c.has_option(section, "max_global_ratio_per_tracker"):
            value, problem = cs.tracker_value("max_global_ratio_per_tracker",
                                              c.get(section, "max_global_ratio_per_tracker"))
            if not problem:
                return value
    return base


def _hash_param(value) -> str:
    return str(value or "")[:40].lower()


def _ctl_get(mapping, ih, default=None):
    """Control entry of a torrent, also under the 8-digit key of older versions."""
    k = datastore.alias_key(mapping, ih)
    return mapping[k] if k is not None else default


def _drop_alias(mapping, ih):
    """Remove the legacy 8-digit entry of a torrent now written under its full hash."""
    short = ih[:datastore.SHORT_HASH_LEN]
    if short == ih:
        return None
    if isinstance(mapping, list):
        if short in mapping:
            mapping.remove(short)
        return None
    return mapping.pop(short, None)


# ── pages ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    return _page_file("index")


# ── stats & control ───────────────────────────────────────────────────────────

@app.get("/api/stats")
def api_stats():
    data = _load_stats()
    reg  = _load_registry()
    ctl  = _load_control()
    paused_set = set(ctl["paused"])
    result = {}
    for k, v in data.items():
        entry = dict(v)
        ul    = entry.get("cumul_rep_ul", 0)
        dl    = entry.get("cumul_rep_dl", 0)
        seed_fake_dl = entry.get("seed_fake_dl", 0)
        entry["ratio"]            = round(ul / dl, 4) if dl > 0 else None
        entry["estimated_size_mb"]= round((dl if dl > 0 else seed_fake_dl) / 1e6, 2)
        entry["is_pure_seeder"]   = dl == 0
        if "mode" not in entry:
            entry["mode"] = "seed" if dl == 0 else "down"
        meta = datastore.registry_meta(reg, k)
        if isinstance(meta, dict):
            entry["name"]     = meta.get("name", "")
            entry["has_udp"]  = bool(meta.get("udp_trackers"))
        entry["paused"]   = datastore.alias_key(paused_set, k) is not None
        entry["tags"]     = _ctl_get(ctl["tags"], k, [])
        entry["override"] = _ctl_get(ctl["overrides"], k, {})
        entry.pop("peer_id", None)
        entry.pop("key", None)
        result[k] = entry
    return result


@app.get("/api/registry")
def api_registry():
    out = {}
    for k, v in _load_registry().items():
        if not isinstance(v, dict):
            continue
        out[k] = {
            "name":         v.get("name", ""),
            "size_bytes":   v.get("size_bytes", 0),
            "trackers":     v.get("trackers", []),
            "udp_trackers": v.get("udp_trackers", []),
        }
    return out


@app.get("/api/udp_status")
def api_udp_status():
    domains = {}
    affected = []
    for ih, meta in _load_registry().items():
        if not isinstance(meta, dict):
            continue
        udp = meta.get("udp_trackers", [])
        if not udp:
            continue
        affected.append({"hash": ih, "name": meta.get("name", ""), "count": len(udp)})
        for url in udp:
            d = (urllib.parse.urlsplit(url).hostname or url)[:255]
            domains[d] = domains.get(d, 0) + 1
    return {
        "udp_detected":   bool(domains),
        "domains":        domains,
        "affected_count": len(affected),
        "affected":       affected[:50],
        "note": "UDP tracker announces bypass the HTTP proxy and are not modified by NewGreedy",
    }


@app.get("/api/control")
def api_control_get():
    return _load_control()


@app.post("/api/control/pause")
def api_control_pause(payload: dict = Body(...)):
    ih = _hash_param(payload.get("hash"))
    if not VALID_HASH_RE.match(ih):
        return JSONResponse({"error": "invalid hash"}, status_code=400)
    paused = bool(payload.get("paused", True))

    def apply(ctl):
        _drop_alias(ctl["paused"], ih)
        current = set(ctl["paused"])
        current.add(ih) if paused else current.discard(ih)
        ctl["paused"] = sorted(current)
    _, err = _edit_control(apply)
    return err or {"hash": ih, "paused": paused}


@app.post("/api/control/override")
def api_control_override(payload: dict = Body(...)):
    ih = _hash_param(payload.get("hash"))
    if not VALID_HASH_RE.match(ih):
        return JSONResponse({"error": "invalid hash"}, status_code=400)
    entry = {}
    for field, lo, hi in (("target_ratio", 0.01, 100.0), ("cumul_rep_ul", 0.0, 1e15)):
        if field in payload and payload[field] is not None:
            try:
                val = float(payload[field])
            except (TypeError, ValueError):
                return JSONResponse({"error": f"{field} must be numeric"}, status_code=400)
            if not (lo <= val <= hi):
                return JSONResponse({"error": f"{field} out of range"}, status_code=400)
            entry[field] = val

    def apply(ctl):
        legacy = _drop_alias(ctl["overrides"], ih) or {}
        if payload.get("clear"):
            ctl["overrides"].pop(ih, None)
        elif entry:
            ctl["overrides"][ih] = {**legacy, **ctl["overrides"].get(ih, {}), **entry}
        elif legacy:
            ctl["overrides"][ih] = legacy
        return ctl["overrides"].get(ih, {})
    result, err = _edit_control(apply)
    return err or {"hash": ih, "override": result}


@app.post("/api/control/tags")
def api_control_tags(payload: dict = Body(...)):
    ih = _hash_param(payload.get("hash"))
    if not VALID_HASH_RE.match(ih):
        return JSONResponse({"error": "invalid hash"}, status_code=400)
    raw = payload.get("tags", [])
    if isinstance(raw, str):
        raw = raw.split(",")
    tags = sorted({re.sub(r"[^\w\- ]", "", str(t)).strip()[:32] for t in raw if str(t).strip()} - {""})

    def apply(ctl):
        _drop_alias(ctl["tags"], ih)
        if tags:
            ctl["tags"][ih] = tags
        else:
            ctl["tags"].pop(ih, None)
    _, err = _edit_control(apply)
    return err or {"hash": ih, "tags": tags}


# ── update ────────────────────────────────────────────────────────────────────

_release_cache = {}


def _channel():
    return cs.get(_config(), "update", "update_channel")


@app.get("/api/update/status")
def api_update_status():
    st = updater.status()
    st["current"] = VERSION
    st["restart_pending"] = updater.RESTART_FLAG.exists()
    st["docker"] = datastore.IN_DOCKER
    st["channel"] = _channel()
    if datastore.IN_DOCKER:
        st["docker_note"] = updater.DOCKER_MESSAGE
    return st


@app.get("/api/update/release")
def api_update_release():
    channel = _channel()
    cached = _release_cache.get(channel)
    if cached and time.monotonic() - cached[0] < 600:
        return cached[1]
    try:
        info = updater.release_info(channel)
        info["channel"] = channel
        info["update_available"] = parse_version(info["tag"]) > parse_version(VERSION)
    except Exception as e:
        return JSONResponse({"error": f"could not reach GitHub: {e}"}, status_code=502)
    _release_cache[channel] = (time.monotonic(), info)
    return info


@app.post("/api/update/start")
def api_update_start(payload: dict = Body(default={})):
    ok, msg = updater.start(force=bool(payload.get("force")), channel=_channel())
    return JSONResponse({"started": ok, "message": msg},
                        status_code=200 if ok else 409)


@app.post("/api/update/rollback")
def api_update_rollback():
    if datastore.IN_DOCKER:
        return JSONResponse({"error": updater.DOCKER_MESSAGE}, status_code=409)
    try:
        n = updater.rollback()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    if not n:
        return JSONResponse({"error": "no backup available"}, status_code=404)
    return {"restored": n, "note": "restart NewGreedy to load the previous files"}


RESTART_FLUSH_TIMEOUT = 8.0


def _exit_after_flush(flag: pathlib.Path):
    deadline = time.monotonic() + RESTART_FLUSH_TIMEOUT
    while time.monotonic() < deadline:
        if not flag.exists():
            break
        time.sleep(0.2)
    else:
        logger.warning("restart: stats flush did not confirm within %.0fs", RESTART_FLUSH_TIMEOUT)
    time.sleep(0.3)
    os._exit(42)


@app.post("/api/restart")
def api_restart():
    flag = datastore.SHUTDOWN_FLAG
    try:
        atomic_write_bytes(flag, json.dumps({"ts": int(time.time()), "reason": "restart"}).encode())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    threading.Thread(target=_exit_after_flush, args=(flag,), daemon=True).start()
    return {"status": "restarting",
            "note": "the service manager brings NewGreedy back up automatically"}


@app.get("/api/profiles")
def api_profiles():
    out = {}
    for name in client_profiles.profile_names():
        p = client_profiles.PROFILES[name]
        out[name] = {
            "name":        p["name"],
            "user_agents": client_profiles.user_agents(p),
            "numwant":     p["numwant"],
            "param_order": p["param_order"],
        }
    return out


# ── backup & restore ──────────────────────────────────────────────────────────

RESTORABLE = {"config.ini", "stats.json", "torrent_registry.json", "control.json", "trash.json"}


def _zip_response(data: bytes, filename: str):
    return StreamingResponse(iter([data]), media_type="application/zip",
                             headers={"Content-Disposition": f"attachment; filename={filename}"})


def _restore_archive(data: bytes):
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return JSONResponse({"error": "not a valid zip archive"}, status_code=400)
    files = {}
    with zf:
        for name in zf.namelist():
            base = pathlib.PurePosixPath(name).name
            if base in RESTORABLE and base == name:
                info = zf.getinfo(name)
                if info.file_size > 50 * 1024 * 1024:
                    return JSONResponse({"error": f"{name} is too large"}, status_code=400)
                files[base] = zf.read(name)
    if not files:
        return JSONResponse({"error": "archive contained no known file"}, status_code=400)
    parsed = {}
    for name, blob in files.items():
        if name.endswith(".json"):
            try:
                parsed[name] = json.loads(blob.decode("utf-8"))
            except Exception:
                return JSONResponse({"error": f"{name} is not valid JSON"}, status_code=400)
            if not isinstance(parsed[name], dict):
                return JSONResponse({"error": f"{name} has an unexpected format"}, status_code=400)
    try:
        backups.create("pre-restore")
    except Exception as e:
        logger.warning("[BACKUP] could not save the current data before restoring: %s", e)
    restored = []
    try:
        for name, blob in files.items():
            atomic_write_bytes(datastore.DATA_DIR / name, blob)
            restored.append(name)
        if "stats.json" in parsed:
            raw = parsed["stats.json"]
            datastore.queue_stats_import({k: v for k, v in raw.items() if not k.startswith("_")},
                                         "replace", {"_tracker_cumul": raw.get("_tracker_cumul", {})})
    except Exception as e:
        return JSONResponse({"error": str(e), "restored": restored}, status_code=500)
    _invalidate_cache()
    if "config.ini" in restored:
        set_config(cs.load(CONFIG_FILE))
    logger.info("[RESTORE] %s restored from a backup archive", ", ".join(sorted(restored)))
    return {"restored": sorted(restored),
            "note": "applied by the running engine within a few seconds; a restart is only "
                    "needed for ports, bind addresses and the client identity"}


@app.get("/api/backup")
def api_backup():
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return _zip_response(backups.build_archive(), f"newgreedy-backup-{stamp}.zip")


@app.post("/api/restore")
async def api_restore(file: UploadFile = File(...)):
    data = await file.read(MAX_UPLOAD_SIZE * 5 + 1)
    if len(data) > MAX_UPLOAD_SIZE * 5:
        return JSONResponse({"error": "archive too large"}, status_code=413)
    return await asyncio.to_thread(_restore_archive, data)


@app.get("/api/backups")
def api_backups_list():
    c = _config()
    return {"backups": backups.listing(), "auto": cs.get(c, "backup", "auto_backup"),
            "interval_hours": cs.get(c, "backup", "backup_interval_hours"),
            "keep": cs.get(c, "backup", "backup_keep"), "folder": str(datastore.BACKUPS_DIR)}


@app.post("/api/backups")
def api_backups_create():
    try:
        return {"created": backups.create("manual")}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/backups/{name}")
def api_backups_get(name: str):
    try:
        path = backups.path_of(name)
    except (ValueError, FileNotFoundError):
        return JSONResponse({"error": "not found"}, status_code=404)
    return _zip_response(path.read_bytes(), name)


@app.post("/api/backups/{name}/restore")
def api_backups_restore(name: str):
    try:
        data = backups.path_of(name).read_bytes()
    except (ValueError, FileNotFoundError):
        return JSONResponse({"error": "not found"}, status_code=404)
    return _restore_archive(data)


@app.delete("/api/backups/{name}")
def api_backups_delete(name: str):
    try:
        backups.delete(name)
    except (ValueError, FileNotFoundError):
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"deleted": name}


# ── status, diagnostics, notifications ────────────────────────────────────────

@app.get("/api/status")
def api_status():
    c = _config()
    checks = diagnostics.run(c, live=True)
    engine = datastore.read_status()
    trash = datastore.trash_list()
    return {
        "version": VERSION, "summary": diagnostics.summary(checks), "checks": checks,
        "engine": engine, "docker": datastore.IN_DOCKER, "data_dir": str(datastore.DATA_DIR),
        "proxy": {"host": cs.get(c, "proxy", "listen_host"), "port": cs.get(c, "proxy", "listen_port")},
        "files": {
            "stats_json": datastore.file_size(STATS_FILE),
            "trash_json": datastore.file_size(datastore.TRASH_FILE),
            "log": datastore.file_size(LOG_FILE),
            "trash_entries": len(trash),
            "backups": len(backups.listing()),
            "history_max_points": cs.get(c, "stats", "history_max_points"),
            "history_retention_days": cs.get(c, "stats", "history_retention_days"),
        },
    }


@app.post("/api/notify/test")
def api_notify_test():
    _config()
    ok, detail = notify_test()
    return JSONResponse({"ok": ok, "detail": detail}, status_code=200 if ok else 502)


@app.get("/api/tracker_stats")
def api_tracker_stats():
    c = _config()
    result = {}
    for domain, vals in _load_tracker_cumul().items():
        if not isinstance(vals, dict):
            continue
        ul = float(vals.get("ul", 0))
        dl = float(vals.get("dl", 0))
        ratio = round(ul / dl, 4) if dl > 0 else None
        cap = _tracker_cap(c, domain)
        result[domain] = {
            "ul_mb":      round(ul / 1e6, 2),
            "dl_mb":      round(dl / 1e6, 2),
            "ratio":      ratio,
            "cap":        cap,
            "pct_of_cap": round((ratio / cap) * 100, 1) if ratio is not None and cap else None,
            "last_seen":  vals.get("ts", 0),
        }
    return result


@app.get("/api/health")
def api_health(request: Request):
    engine = datastore.read_status()
    age = time.time() - float(engine.get("heartbeat", 0) or 0)
    running = engine.get("state") == "running" and age < diagnostics.HEARTBEAT_STALE_S
    out = {"status": "ok" if running else "degraded", "version": VERSION,
           "engine": engine.get("state", "unknown")}
    if _token_ok(request):
        data = _load_stats()
        cap = cs.get(_config(), "spoofing", "max_ratio_per_torrent")
        out.update({
            "total": len(data),
            "stalled": [ih for ih, d in data.items() if d.get("stalled", False)],
            "anomalies": [ih for ih, d in data.items()
                          if d.get("cumul_rep_dl", 0) > 0
                          and d.get("cumul_rep_ul", 0) / d["cumul_rep_dl"] > cap],
            "target_reached": [ih for ih, d in data.items() if d.get("target_reached", False)],
        })
    return JSONResponse(out, status_code=200 if running or not engine else 503)


def _label(v) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


@app.get("/metrics", response_class=PlainTextResponse)
def api_metrics():
    data = _load_stats()
    tc   = _load_tracker_cumul()
    lines = [
        "# HELP newgreedy_torrents_total Number of tracked torrents",
        "# TYPE newgreedy_torrents_total gauge",
        f"newgreedy_torrents_total {len(data)}",
        "# HELP newgreedy_upload_bytes_total Cumulative injected upload per torrent",
        "# TYPE newgreedy_upload_bytes_total gauge",
    ]
    for ih, d in data.items():
        lines.append(f'newgreedy_upload_bytes_total{{hash="{_label(ih)}"}} {int(d.get("cumul_rep_ul", 0))}')
    lines += [
        "# HELP newgreedy_download_bytes_total Cumulative download per torrent",
        "# TYPE newgreedy_download_bytes_total gauge",
    ]
    for ih, d in data.items():
        lines.append(f'newgreedy_download_bytes_total{{hash="{_label(ih)}"}} {int(d.get("cumul_rep_dl", 0))}')
    lines += [
        "# HELP newgreedy_torrent_ratio Current ratio per torrent",
        "# TYPE newgreedy_torrent_ratio gauge",
    ]
    for ih, d in data.items():
        dl = d.get("cumul_rep_dl", 0)
        r  = (d.get("cumul_rep_ul", 0) / dl) if dl > 0 else 0
        lines.append(f'newgreedy_torrent_ratio{{hash="{_label(ih)}"}} {r:.4f}')
    lines += [
        "# HELP newgreedy_announces_total Announce count per torrent",
        "# TYPE newgreedy_announces_total counter",
    ]
    for ih, d in data.items():
        lines.append(f'newgreedy_announces_total{{hash="{_label(ih)}"}} {int(d.get("ann_count", 0))}')
    lines += [
        "# HELP newgreedy_tracker_ratio Aggregate ratio per tracker domain",
        "# TYPE newgreedy_tracker_ratio gauge",
    ]
    for domain, v in tc.items():
        if not isinstance(v, dict):
            continue
        dl = float(v.get("dl", 0))
        r  = (float(v.get("ul", 0)) / dl) if dl > 0 else 0
        lines.append(f'newgreedy_tracker_ratio{{domain="{_label(domain)}"}} {r:.4f}')
    engine = datastore.read_status()
    up = 1 if engine.get("state") == "running" and \
        time.time() - float(engine.get("heartbeat", 0) or 0) < diagnostics.HEARTBEAT_STALE_S else 0
    lines += [
        "# HELP newgreedy_stalled_total Torrents flagged as stalled",
        "# TYPE newgreedy_stalled_total gauge",
        f'newgreedy_stalled_total {sum(1 for d in data.values() if d.get("stalled"))}',
        "# HELP newgreedy_target_reached_total Torrents that reached their target ratio",
        "# TYPE newgreedy_target_reached_total gauge",
        f'newgreedy_target_reached_total {sum(1 for d in data.values() if d.get("target_reached"))}',
        "# HELP newgreedy_engine_up 1 when the mitmproxy engine reports a recent heartbeat",
        "# TYPE newgreedy_engine_up gauge",
        f"newgreedy_engine_up {up}",
    ]
    return "\n".join(lines) + "\n"


@app.get("/api/history")
def api_history():
    return {k: v.get("history", []) for k, v in _load_stats().items()}


@app.get("/api/history/{ih}")
def api_history_single(ih: str):
    ih = _hash_param(ih)
    data = _load_stats()
    if ih not in data:
        return JSONResponse({"error": "not found"}, status_code=404)
    return data[ih].get("history", [])


@app.delete("/api/logs")
def api_logs_clear():
    try:
        ng = logging.getLogger("NewGreedy")
        for h in list(ng.handlers):
            base = getattr(h, "baseFilename", "")
            if base and os.path.abspath(base) == os.path.abspath(str(LOG_FILE)):
                h.acquire()
                try:
                    h.close()
                    with open(LOG_FILE, "w", encoding="utf-8"):
                        pass
                    h.stream = h._open()
                finally:
                    h.release()
                break
        else:
            with open(LOG_FILE, "w", encoding="utf-8"):
                pass
        return {"status": "cleared"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/logs")
def api_logs(lines: int = Query(default=100, ge=10, le=2000)):
    return {"lines": _tail_log(lines)}


# ── config ────────────────────────────────────────────────────────────────────

def _mask_url(url: str, hide_path: bool) -> str:
    """URL without its secrets: password, and for webhooks the path and query."""
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return MASK
    if not p.scheme or not p.netloc:
        return MASK if url else url
    userinfo, sep, host = p.netloc.rpartition("@")
    if sep:
        user, colon, _ = userinfo.partition(":")
        host = f"{user}{':' + MASK if colon else ''}@{host}"
    path, query = p.path, p.query
    if hide_path and (path.strip("/") or query):
        path, query = "/" + MASK, ""
    return urllib.parse.urlunsplit((p.scheme, host, path, query, ""))


@app.get("/api/config")
def api_config():
    c = _config()
    if c is None:
        return {}
    out = {}
    for s in c.sections():
        out[s] = dict(c[s])
    for section, key in cs.SECRET_KEYS:
        if out.get(section, {}).get(key):
            out[section] = dict(out[section], **{key: MASK})
    for (section, key), hide_path in MASKED_URLS.items():
        if out.get(section, {}).get(key):
            out[section] = dict(out[section], **{key: _mask_url(out[section][key], hide_path)})
    return out


@app.get("/api/config/editable")
def api_config_editable():
    return {"keys": sorted([f"{s}.{k}" for s, k in EDITABLE_KEYS]),
            "restart": sorted([f"{s}.{k}" for s, k in cs.RESTART_KEYS if (s, k) in EDITABLE_KEYS])}


@app.post("/api/config/set")
def api_config_set(payload: dict = Body(...)):
    section = str(payload.get("section", "")).strip()
    key     = str(payload.get("key", "")).strip()
    value   = payload.get("value", "")
    if (section, key) not in EDITABLE_KEYS:
        return JSONResponse({"error": f"key {section}.{key} is not editable"}, status_code=400)
    value = str(value).strip()
    if "\n" in value or "\r" in value:
        return JSONResponse({"error": "invalid value"}, status_code=400)
    if (section, key) in MASKED_URLS and MASK in value:
        current = cs.get(_config(), section, key) or ""
        if value == _mask_url(current, MASKED_URLS[(section, key)]):
            return {"status": "ok", "section": section, "key": key, "value": value,
                    "restart_required": False, "note": "unchanged"}
        return JSONResponse({"error": f"{key} is shown masked - type the full URL to change it"},
                            status_code=400)
    problem = cs.validate_value(section, key, value)
    if problem:
        return JSONResponse({"error": problem}, status_code=400)
    value = cs.normalize(section, key, value)
    try:
        cs.set_value(CONFIG_FILE, section, key, value)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    set_config(cs.load(CONFIG_FILE))
    restart = (section, key) in cs.RESTART_KEYS
    return {"status": "ok", "section": section, "key": key, "value": value,
            "restart_required": restart,
            "note": "restart NewGreedy to apply this one" if restart
                    else "applied by the running engine within 5 seconds"}


@app.post("/api/config/reload")
def api_config_reload():
    set_config(cs.load(CONFIG_FILE))
    try:
        os.utime(CONFIG_FILE, None)
    except OSError:
        pass
    return {"status": "reloaded", "note": "the engine applies it within 5 seconds"}


_version_cache    = {}
VERSION_TTL_OK    = 3600
VERSION_TTL_ERR   = 300


@app.get("/api/version")
def api_version():
    channel = _channel()
    now = time.monotonic()
    cached = _version_cache.get(channel)
    if cached is not None:
        result, ts = cached
        ttl = VERSION_TTL_ERR if result.get("error") else VERSION_TTL_OK
        if (now - ts) < ttl:
            return dict(result, cached=True)
    try:
        info = updater.release_info(channel)
        latest = info["tag"] or "unknown"
        up_to_date = (parse_version(latest) <= parse_version(VERSION))
        result = {
            "current":          VERSION,
            "latest":           latest,
            "channel":          channel,
            "up_to_date":       up_to_date,
            "update_available": not up_to_date,
            "release_url":      info["url"],
            "url":              info["url"],
            "docker":           datastore.IN_DOCKER,
        }
    except Exception as e:
        reason = str(e)
        if "403" in reason or "rate limit" in reason.lower():
            reason = "GitHub API rate limit reached (60 requests/hour per IP) - retry later"
        elif "Name or service not known" in reason or "Temporary failure" in reason:
            reason = "No network access to api.github.com"
        result = {"current": VERSION, "latest": None, "up_to_date": None, "channel": channel,
                  "update_available": False, "error": reason}
    _version_cache[channel] = (result, now)
    return dict(result, cached=False)


@app.get("/api/stats/csv")
def api_stats_csv():
    data = _load_stats()
    reg = _load_registry()
    rows = ["hash,name,mode,dl_mb,ul_mb,real_ul_mb,ratio,size_mb,ann_count,stalled,target_reached"]
    for ih, d in data.items():
        ul    = d.get("cumul_rep_ul", 0) / 1e6
        dl    = d.get("cumul_rep_dl", 0) / 1e6
        size  = (d.get("size_bytes", 0) or (dl * 1e6) or d.get("seed_fake_dl", 0)) / 1e6
        ratio = round(ul / dl, 4) if dl > 0 else ""
        mode  = str(d.get("mode") or ("seed" if dl == 0 else "down")).upper()
        meta  = datastore.registry_meta(reg, ih)
        name  = (meta.get("name", "") if isinstance(meta, dict) else "").replace('"', '""')
        rows.append(
            f'{ih},"{name}",{mode},{dl:.2f},{ul:.2f},{d.get("cumul_real_ul", 0) / 1e6:.2f},'
            f"{ratio},{size:.2f},{d.get('ann_count', 0)},{d.get('stalled', False)},"
            f"{d.get('target_reached', False)}"
        )
    return StreamingResponse(
        iter(["\n".join(rows) + "\n"]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=newgreedy_stats.csv"},
    )


# ── torrents ──────────────────────────────────────────────────────────────────

def _register_torrent(data: bytes, filename: str):
    ih, size = torrent_info(data)
    if not ih:
        raise ValueError("Could not extract info_hash")
    trackers = tracker_urls(data)
    udp = [u for u in trackers if u.lower().startswith("udp://")]
    ih_key = ih
    name = _sanitize_filename(filename) or _sanitize_filename(torrent_name(data)) or ih[:8]

    def put(reg):
        legacy = reg.get(ih[:8])
        if isinstance(legacy, dict) and legacy.get("info_hash") in (None, "", ih):
            del reg[ih[:8]]
        reg[ih_key] = {"info_hash": ih, "size_bytes": size, "name": name,
                       "trackers": trackers, "udp_trackers": udp}
    datastore.update_json(REGISTRY_FILE, put, {})
    return ih, ih_key, size, udp


@app.post("/api/upload")
async def api_upload_torrent(file: UploadFile = File(...)):
    try:
        data = await file.read(MAX_UPLOAD_SIZE + 1)
        if len(data) > MAX_UPLOAD_SIZE:
            return JSONResponse(
                {"error": f"file too large (max {MAX_UPLOAD_SIZE // (1024*1024)} MB)"},
                status_code=413,
            )
        try:
            ih, ih_key, size, udp = await asyncio.to_thread(_register_torrent, data, file.filename or "")
        except (ValueError, IndexError, RecursionError) as e:
            return JSONResponse({"error": f"not a readable .torrent file ({e})"}, status_code=400)
        return {
            "info_hash":       ih,
            "info_hash_short": ih[:8],
            "size_bytes":      size,
            "size_mb":         round(size / 1e6, 2),
            "infohash":        ih,
            "size_estimate":   size,
            "udp_trackers":    udp,
            "udp_warning":     bool(udp),
        }
    except Exception as e:
        logger.warning("upload failed: %s", e)
        return JSONResponse({"error": "could not process the uploaded file"}, status_code=500)


def _select_for_purge(data, keep_active, inactive_hours):
    cutoff = time.time() - inactive_hours * 3600 if inactive_hours > 0 else 0
    out = []
    for k, v in data.items():
        if not VALID_HASH_RE.match(k) or not isinstance(v, dict):
            continue
        if not keep_active:
            out.append(k)
        elif v.get("target_reached", False) or (
                cutoff > 0 and float(v.get("last_announce_ts", 0)) < cutoff):
            out.append(k)
    return out


def _remove_stats(hashes, reason):
    """Drop entries from stats.json, keep them in the trash, and tell the engine."""
    with datastore.file_lock(STATS_FILE):
        # a private copy: other requests may be reading the cached one
        data = datastore.read_json(STATS_FILE, {})
        data = data if isinstance(data, dict) else {}
        removed = {k: data.pop(k) for k in hashes if k in data}
        if removed:
            # queued before the lock is released: the engine applies it before its next save
            datastore.queue_purge(list(removed))
            datastore.atomic_write_json(STATS_FILE, data)
    if removed and cs.get(_config(), "stats", "trash_retention_days") > 0:
        datastore.trash_add(removed, reason)
    _invalidate_cache()
    return removed, len([k for k in data if VALID_HASH_RE.match(k)])


@app.get("/api/stats/purge")
def purge_stats_preview(keep_active: bool = True, inactive_hours: int = 0):
    to_del = _select_for_purge(_read_stats_file(), keep_active, inactive_hours)
    return {"would_purge": len(to_del), "hashes": to_del,
            "keep_active": keep_active, "inactive_hours": inactive_hours}


@app.delete("/api/stats/purge")
def purge_stats(keep_active: bool = True, inactive_hours: int = 0):
    to_del = _select_for_purge(_read_stats_file(cached=False), keep_active, inactive_hours)
    try:
        removed, remaining = _remove_stats(to_del, "manual")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    return {"purged": len(removed), "remaining": remaining,
            "keep_active": keep_active, "inactive_hours": inactive_hours}


@app.delete("/api/stats/{ih}")
def delete_torrent_stat(ih: str):
    ih = _hash_param(ih)
    if not VALID_HASH_RE.match(ih):
        return JSONResponse({"error": "invalid hash"}, status_code=400)
    try:
        removed, _ = _remove_stats([ih], "manual")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    if not removed:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"deleted": ih}


@app.get("/api/trash")
def api_trash_list():
    reg = _load_registry()
    out = []
    for ih, item in datastore.trash_list().items():
        if not isinstance(item, dict):
            continue
        d = item.get("data") or {}
        meta = datastore.registry_meta(reg, ih)
        out.append({"hash": ih, "deleted_at": item.get("deleted_at", 0), "reason": item.get("reason", ""),
                    "name": meta.get("name", "") if isinstance(meta, dict) else "",
                    "cumul_rep_ul": d.get("cumul_rep_ul", 0), "cumul_rep_dl": d.get("cumul_rep_dl", 0),
                    "ann_count": d.get("ann_count", 0)})
    return {"items": sorted(out, key=lambda x: x["deleted_at"], reverse=True),
            "retention_days": cs.get(_config(), "stats", "trash_retention_days")}


@app.post("/api/trash/{ih}/restore")
def api_trash_restore(ih: str):
    ih = _hash_param(ih)
    if not VALID_HASH_RE.match(ih):
        return JSONResponse({"error": "invalid hash"}, status_code=400)
    if ih in _read_stats_file(cached=False):
        return JSONResponse({"error": "this torrent announced again since it was purged and "
                                      "has a new entry - delete that entry first to bring the "
                                      "old figures back"}, status_code=409)
    item = datastore.trash_take(ih)
    if not isinstance(item, dict) or not isinstance(item.get("data"), dict):
        return JSONResponse({"error": "not in the trash"}, status_code=404)
    datastore.queue_stats_import({ih: item["data"]}, "merge")
    return {"restored": ih, "note": "back in the stats within a few seconds"}


@app.delete("/api/trash/{ih}")
def api_trash_delete(ih: str):
    item = datastore.trash_take(_hash_param(ih))
    if item is None:
        return JSONResponse({"error": "not in the trash"}, status_code=404)
    return {"deleted": _hash_param(ih)}


@app.delete("/api/trash")
def api_trash_empty():
    n = datastore.update_json(datastore.TRASH_FILE, lambda cur: (len(cur), cur.clear())[0], {})
    return {"deleted": n}


# ── live logs ─────────────────────────────────────────────────────────────────

def _log_size():
    try:
        return os.path.getsize(LOG_FILE)
    except OSError:
        return 0


def _read_log_from(pos: int):
    """Bytes appended to the log since pos: (rotated, data, new_pos)."""
    try:
        with open(LOG_FILE, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            rotated = size < pos
            f.seek(0 if rotated else pos)
            data = f.read()
            return rotated, data, f.tell()
    except FileNotFoundError:
        return pos > 0, b"", 0


async def _ws_send_lines(ws: WebSocket, lines):
    for line in lines:
        if line.strip():
            await ws.send_text(line)


@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket):
    if not _host_ok(ws) or not _origin_ok(ws) or not _token_ok(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
    # file reads run in a thread: the event loop also serves the API
    last_pos = await asyncio.to_thread(_log_size)
    await _ws_send_lines(ws, await asyncio.to_thread(_tail_log, 500))
    pending = b""
    try:
        while True:
            try:
                rotated, data, last_pos = await asyncio.to_thread(_read_log_from, last_pos)
                if rotated:
                    pending = b""
                    await ws.send_text("--- log file truncated or rotated, resuming ---")
                data = pending + data
                complete, sep, pending = data.rpartition(b"\n")
                if sep:
                    await _ws_send_lines(ws, complete.decode("utf-8", errors="replace").splitlines())
                else:
                    pending = data
            except (WebSocketDisconnect, RuntimeError):
                break
            except Exception as e:
                logger.debug("ws_logs error: %s", e)
                break
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass


# ── watch folder ──────────────────────────────────────────────────────────────

_watch_seen = {}


def _scan_watch_folder():
    if not _WATCH_FOLDER:
        return
    folder = datastore.resolve_data_path(_WATCH_FOLDER)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    present = set()
    for path in folder.iterdir():
        if not path.is_file() or path.suffix.lower() != ".torrent":
            continue
        present.add(str(path))
        try:
            stat = path.stat()
        except OSError:
            continue
        sig = (stat.st_mtime, stat.st_size)
        if _watch_seen.get(str(path)) == sig:
            continue
        _watch_seen[str(path)] = sig
        try:
            if stat.st_size > MAX_UPLOAD_SIZE:
                logger.warning("[WATCH] %s skipped - larger than %d MB",
                               path.name, MAX_UPLOAD_SIZE // (1024 * 1024))
                continue
            data = path.read_bytes()
            ih, ih_key, size, udp = _register_torrent(data, path.name)
            logger.info("[WATCH] imported %s -> %s (%.1f MB)", path.name, ih_key[:8], size / 1e6)
            if udp:
                logger.warning("[UDP_BYPASS] %s uses %d UDP tracker(s) - those announces bypass NewGreedy",
                               path.name, len(udp))
        except Exception as e:
            logger.warning("[WATCH] %s not imported: %s", path.name, e)
    for gone in [k for k in _watch_seen if k not in present]:
        del _watch_seen[gone]


async def _watch_loop():
    while True:
        try:
            _config()
            await asyncio.to_thread(_scan_watch_folder)
        except Exception as e:
            logger.debug("watch loop error: %s", e)
        await asyncio.sleep(WATCH_INTERVAL)


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.get("/{page}", response_class=HTMLResponse)
def serve_page(page: str):
    if page not in PAGES:
        return HTMLResponse(status_code=404, content="Not found")
    if not (STATIC_DIR / f"{page}.html").exists():
        return HTMLResponse(status_code=404, content=f"{page}.html not found in static/")
    return _page_file(page)

