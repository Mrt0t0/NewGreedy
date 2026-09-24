#!/usr/bin/env python3
"""NewGreedy v1.7.7 — Web UI & API"""
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
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Query, Request, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse, PlainTextResponse
from _version import (VERSION, fetch_latest, parse_version, notify_configure,
                      torrent_info, tracker_urls, atomic_write_json, atomic_write_bytes)
import client_profiles
import updater

@asynccontextmanager
async def _lifespan(_app):
    if _WATCH_FOLDER:
        logger.info("Watch folder enabled: %s", _WATCH_FOLDER)
        task = asyncio.create_task(_watch_loop())
        try:
            yield
        finally:
            task.cancel()
    else:
        yield


app      = FastAPI(title="NewGreedy Web UI", lifespan=_lifespan)
_cfg     = None
logging.getLogger("passlib").setLevel(logging.ERROR)
logger   = logging.getLogger("NewGreedy.Web")
BASE_DIR = pathlib.Path(__file__).parent.resolve()
STATS_FILE         = BASE_DIR / "stats.json"
PURGE_PENDING_FILE = BASE_DIR / "purge_pending.json"
REGISTRY_FILE      = BASE_DIR / "torrent_registry.json"
LOG_FILE           = BASE_DIR / "newgreedy.log"
STATIC_DIR         = BASE_DIR / "static"
CONFIG_FILE        = BASE_DIR / "config.ini"
CONTROL_FILE       = BASE_DIR / "control.json"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

VALID_HASH_RE   = re.compile(r"^[0-9a-f]{8,40}$")
MAX_UPLOAD_SIZE = 10 * 1024 * 1024
TOKEN_COOKIE    = "ng_token"
WATCH_INTERVAL  = 15

_SECRET_TOKEN = ""
_PATH_PREFIX  = ""
_RATE_LIMIT   = 0
_WATCH_FOLDER = ""

_rate_buckets = defaultdict(deque)
_rate_lock    = threading.Lock()

_raw_cache      = None
_raw_cache_ts   = 0.0
_raw_cache_ttl  = 2.0

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
    ("advanced", "corrupt_field_probability"),
    ("advanced", "respect_tracker_interval"),
    ("anti_detection", "user_agent_mode"), ("anti_detection", "user_agent_value"),
    ("anti_detection", "spoof_user_agent"), ("anti_detection", "spoof_peer_id"),
    ("anti_detection", "spoof_peers"), ("anti_detection", "spoof_port"),
    ("anti_detection", "spoof_headers"), ("anti_detection", "intercept_scrape"),
    ("anti_detection", "echo_trackerid"),
    ("anti_detection", "tracker_whitelist"), ("anti_detection", "tracker_blacklist"),
    ("anti_detection", "desync_announces"), ("anti_detection", "desync_spread_pct"),
    ("anti_detection", "desync_min_gap"), ("anti_detection", "startup_burst"),
    ("anti_detection", "startup_burst_window"), ("anti_detection", "startup_ramp_floor"),
    ("stats", "persist_stats"), ("stats", "auto_purge_stopped"),
    ("stats", "tracker_cumul_ttl_days"), ("stats", "stopped_purge_delay"),
    ("client", "client_profile"), ("client", "rotate_profile_on_restart"),
    ("client", "session_identity"), ("client", "version_bump_probability"),
    ("bandwidth", "global_max_speed_mbps"), ("bandwidth", "per_torrent_speed_cap_mbps"),
    ("bandwidth", "max_download_speed_mbps"),
    ("bandwidth", "circadian_smoothing"), ("bandwidth", "circadian_night_factor"),
    ("notifications", "webhook_format"), ("notifications", "webhook_events"),
    ("simulation", "dry_run"),
}


KEY_RULES = {
    "target_ratio": ("float", 0.01, 100.0), "target_ratio_buffer": ("float", 0.0, 10.0),
    "catch_up_factor": ("float", 0.001, 1.0), "max_ratio_per_torrent": ("float", 0.01, 100.0),
    "max_global_ratio_per_tracker": ("float", 0.01, 100.0),
    "max_simulated_speed_mbps": ("float", 0.01, 10000.0),
    "upload_noise_pct": ("float", 0.0, 100.0), "stagnation_probability": ("float", 0.0, 1.0),
    "seed_credit_mb": ("float", 0.0, 100000.0), "seed_target_mb": ("float", 0.0, 1e7),
    "min_announce_interval": ("int", 60, 86400),
    "stall_announce_threshold": ("int", 1, 1000),
    "min_announces_before_stagnation": ("int", 0, 1000),
    "interval_jitter_pct": ("float", 0.0, 1.0),
    "corrupt_field_probability": ("float", 0.0, 1.0),
    "desync_spread_pct": ("float", 0.0, 0.45), "desync_min_gap": ("int", 0, 3600),
    "startup_burst_window": ("int", 0, 86400), "startup_ramp_floor": ("float", 0.0, 1.0),
    "tracker_cumul_ttl_days": ("int", 0, 3650),
    "stopped_purge_delay": ("int", 0, 86400),
    "version_bump_probability": ("float", 0.0, 1.0),
    "global_max_speed_mbps": ("float", 0.0, 10000.0),
    "per_torrent_speed_cap_mbps": ("float", 0.01, 10000.0),
    "max_download_speed_mbps": ("float", 0.0, 10000.0),
    "circadian_night_factor": ("float", 0.0, 1.0),
    "auto_stop_at_target": ("bool",), "anti_clustering": ("bool",),
    "respect_tracker_interval": ("bool",), "spoof_user_agent": ("bool",),
    "spoof_peer_id": ("bool",), "spoof_peers": ("bool",), "spoof_port": ("bool",),
    "spoof_headers": ("bool",), "intercept_scrape": ("bool",),
    "desync_announces": ("bool",), "startup_burst": ("bool",),
    "echo_trackerid": ("bool",),
    "persist_stats": ("bool",), "auto_purge_stopped": ("bool",),
    "rotate_profile_on_restart": ("bool",), "circadian_smoothing": ("bool",),
    "dry_run": ("bool",),
    "log_level": ("choice", ("DEBUG", "INFO", "WARNING", "ERROR")),
    "user_agent_mode": ("choice", ("random", "fixed")),
    "session_identity": ("choice", ("stable", "drift", "rotate")),
    "webhook_format": ("choice", ("generic", "discord", "slack")),
    "inject_hours": ("hours",), "client_profile": ("profile",),
}

_BOOLS = {"true", "false", "yes", "no", "on", "off", "1", "0"}


def _validate_value(key: str, value: str):
    rule = KEY_RULES.get(key)
    if not rule:
        return None
    kind = rule[0]
    if kind in ("float", "int"):
        try:
            num = float(value) if kind == "float" else int(value)
        except ValueError:
            return f"{key} must be a{'' if kind == 'int' else ' decimal'} number"
        if not (rule[1] <= num <= rule[2]):
            return f"{key} must be between {rule[1]} and {rule[2]}"
        return None
    if kind == "bool":
        return None if value.lower() in _BOOLS else f"{key} must be true or false"
    if kind == "choice":
        return None if value.lower() in [c.lower() for c in rule[1]] else \
            f"{key} must be one of: {', '.join(rule[1])}"
    if kind == "hours":
        m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", value)
        if not m or not all(0 <= int(g) <= 23 for g in m.groups()):
            return "inject_hours must look like 8-22, with both values between 0 and 23"
        return None
    if kind == "profile":
        allowed = client_profiles.profile_names() + ["random"]
        return None if value.lower() in allowed else f"client_profile must be one of: {', '.join(allowed)}"
    return None


def set_config(c):
    global _cfg, _SECRET_TOKEN, _PATH_PREFIX, _RATE_LIMIT, _WATCH_FOLDER
    _cfg = c
    _SECRET_TOKEN = (c.get("web", "web_secret_token", fallback="") or "").strip()
    _PATH_PREFIX  = (c.get("web", "web_path_prefix", fallback="") or "").strip().strip("/")
    try:
        _RATE_LIMIT = int(c.get("web", "web_rate_limit", fallback="240"))
    except Exception:
        _RATE_LIMIT = 240
    _WATCH_FOLDER = (c.get("advanced", "watch_folder", fallback="") or "").strip()
    try:
        notify_configure(
            c.get("notifications", "webhook_url", fallback=""),
            c.get("notifications", "webhook_format", fallback="generic"),
            c.get("notifications", "webhook_events", fallback=""),
        )
    except Exception as e:
        logger.debug("notifier config error: %s", e)
    if _SECRET_TOKEN:
        logger.info("Web UI authentication enabled")
    else:
        host = (c.get("web", "web_host", fallback="0.0.0.0") or "").strip()
        if host not in ("127.0.0.1", "localhost", "::1"):
            logger.warning(
                "Web UI bound to %s with no web_secret_token - anyone who can reach "
                "port %s may read stats, purge data, restore backups and trigger updates",
                host, c.get("web", "web_port", fallback="8080"))
    if _PATH_PREFIX:
        logger.info("Web UI served under /%s/", _PATH_PREFIX)


def _rate_ok(ip: str) -> bool:
    if _RATE_LIMIT <= 0:
        return True
    now = time.monotonic()
    with _rate_lock:
        bucket = _rate_buckets[ip]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(_rate_buckets) > 512:
            for k in [k for k, v in list(_rate_buckets.items()) if not v]:
                del _rate_buckets[k]
        if len(bucket) >= _RATE_LIMIT:
            return False
        bucket.append(now)
    return True


def _token_ok(request: Request) -> bool:
    if not _SECRET_TOKEN:
        return True
    supplied = (
        request.query_params.get("token")
        or request.headers.get("X-Auth-Token")
        or request.cookies.get(TOKEN_COOKIE)
        or ""
    )
    return hmac.compare_digest(supplied, _SECRET_TOKEN)


@app.middleware("http")
async def _guard(request: Request, call_next):
    path = request.url.path
    if _PATH_PREFIX and not path.startswith("/static/"):
        want = "/" + _PATH_PREFIX
        if path == want:
            path = "/"
        elif path.startswith(want + "/"):
            path = path[len(want):] or "/"
        else:
            return PlainTextResponse("Not found", status_code=404)
        request.scope["path"] = path
        request.scope["raw_path"] = path.encode()
    client_ip = request.client.host if request.client else "unknown"
    rate_limited = path.startswith("/api/") or path == "/metrics"
    if rate_limited and not _rate_ok(client_ip):
        return JSONResponse({"error": "rate limit exceeded"}, status_code=429)
    if not _token_ok(request):
        if not rate_limited and not _rate_ok(client_ip):
            return JSONResponse({"error": "rate limit exceeded"}, status_code=429)
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    response = await call_next(request)
    if request.url.path in ("/", "/torrents", "/charts", "/config", "/logs", "/help"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    if _SECRET_TOKEN and request.query_params.get("token") == _SECRET_TOKEN:
        response.set_cookie(TOKEN_COOKIE, _SECRET_TOKEN, httponly=True, samesite="strict")
    return response


def _read_stats_file():
    global _raw_cache, _raw_cache_ts
    now = time.monotonic()
    if _raw_cache is not None and (now - _raw_cache_ts) < _raw_cache_ttl:
        return _raw_cache
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        raw = {}
    _raw_cache    = raw
    _raw_cache_ts = now
    return raw


def _invalidate_cache():
    global _raw_cache
    _raw_cache = None


def _write_purge_pending(hashes: list):
    try:
        existing = []
        if PURGE_PENDING_FILE.exists():
            existing = json.loads(PURGE_PENDING_FILE.read_text(encoding="utf-8"))
        merged = sorted(set(existing) | set(hashes))
        atomic_write_json(PURGE_PENDING_FILE, merged)
    except Exception as e:
        logger.warning("purge_pending write error: %s", e)


def _sanitize_filename(name: str) -> str:
    if not name:
        return ""
    name = name.replace("\x00", "").replace("\n", " ").replace("\r", " ")
    name = re.sub(r"[\\/]", "_", name)
    return name[:255].strip()


def _load_control():
    try:
        with open(CONTROL_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    return {
        "paused":    list(data.get("paused", [])),
        "overrides": dict(data.get("overrides", {})),
        "tags":      dict(data.get("tags", {})),
    }


def _save_control(ctl):
    try:
        atomic_write_json(CONTROL_FILE, ctl)
        return True
    except Exception as e:
        logger.warning("control write error: %s", e)
        return False



def _load_registry():
    try:
        with open(REGISTRY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_registry(reg):
    atomic_write_json(REGISTRY_FILE, reg)


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
    raw = _read_stats_file()
    tc = raw.get("_tracker_cumul", {})
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


@app.get("/", response_class=HTMLResponse)
async def index():
    f = STATIC_DIR / "index.html"
    return f.read_text(encoding="utf-8") if f.exists() else "<h1>NewGreedy v1.7.7</h1>"


@app.get("/api/stats")
async def api_stats():
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
        meta = reg.get(k)
        if isinstance(meta, dict):
            entry["name"]     = meta.get("name", "")
            entry["has_udp"]  = bool(meta.get("udp_trackers"))
        entry["paused"]   = k in paused_set
        entry["tags"]     = ctl["tags"].get(k, [])
        entry["override"] = ctl["overrides"].get(k, {})
        entry["client"]   = entry.get("profile", "")
        entry.pop("peer_id", None)
        entry.pop("key", None)
        result[k] = entry
    return result


@app.get("/api/registry")
async def api_registry():
    reg = _load_registry()
    out = {}
    for k, v in reg.items():
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
async def api_udp_status():
    reg = _load_registry()
    domains = {}
    affected = []
    for ih, meta in reg.items():
        if not isinstance(meta, dict):
            continue
        udp = meta.get("udp_trackers", [])
        if not udp:
            continue
        affected.append({"hash": ih, "name": meta.get("name", ""), "count": len(udp)})
        for url in udp:
            try:
                d = url.split("://", 1)[-1].split("/")[0].split(":")[0]
            except Exception:
                d = url
            domains[d] = domains.get(d, 0) + 1
    return {
        "udp_detected":   bool(domains),
        "domains":        domains,
        "affected_count": len(affected),
        "affected":       affected[:50],
        "note": "UDP tracker announces bypass the HTTP proxy and are not modified by NewGreedy",
    }


@app.get("/api/control")
async def api_control_get():
    return _load_control()


@app.post("/api/control/pause")
async def api_control_pause(payload: dict = Body(...)):
    ih = str(payload.get("hash", ""))[:8].lower()
    if not VALID_HASH_RE.match(ih):
        return JSONResponse({"error": "invalid hash"}, status_code=400)
    paused = bool(payload.get("paused", True))
    ctl = _load_control()
    current = set(ctl["paused"])
    current.add(ih) if paused else current.discard(ih)
    ctl["paused"] = sorted(current)
    if not _save_control(ctl):
        return JSONResponse({"error": "could not write control file"}, status_code=500)
    return {"hash": ih, "paused": paused}


@app.post("/api/control/override")
async def api_control_override(payload: dict = Body(...)):
    ih = str(payload.get("hash", ""))[:8].lower()
    if not VALID_HASH_RE.match(ih):
        return JSONResponse({"error": "invalid hash"}, status_code=400)
    ctl = _load_control()
    entry = dict(ctl["overrides"].get(ih, {}))
    for field, lo, hi in (("target_ratio", 0.0, 100.0), ("cumul_rep_ul", 0.0, 1e15)):
        if field in payload and payload[field] is not None:
            try:
                val = float(payload[field])
            except (TypeError, ValueError):
                return JSONResponse({"error": f"{field} must be numeric"}, status_code=400)
            if not (lo <= val <= hi):
                return JSONResponse({"error": f"{field} out of range"}, status_code=400)
            entry[field] = val
    if payload.get("clear"):
        ctl["overrides"].pop(ih, None)
    elif entry:
        ctl["overrides"][ih] = entry
    if not _save_control(ctl):
        return JSONResponse({"error": "could not write control file"}, status_code=500)
    return {"hash": ih, "override": ctl["overrides"].get(ih, {})}


@app.post("/api/control/tags")
async def api_control_tags(payload: dict = Body(...)):
    ih = str(payload.get("hash", ""))[:8].lower()
    if not VALID_HASH_RE.match(ih):
        return JSONResponse({"error": "invalid hash"}, status_code=400)
    raw = payload.get("tags", [])
    if isinstance(raw, str):
        raw = [t for t in raw.split(",")]
    tags = sorted({re.sub(r"[^\w\- ]", "", str(t)).strip()[:32] for t in raw if str(t).strip()})
    ctl = _load_control()
    if tags:
        ctl["tags"][ih] = tags
    else:
        ctl["tags"].pop(ih, None)
    if not _save_control(ctl):
        return JSONResponse({"error": "could not write control file"}, status_code=500)
    return {"hash": ih, "tags": tags}


@app.get("/api/update/status")
async def api_update_status():
    st = updater.status()
    st["current"] = VERSION
    st["restart_pending"] = updater.RESTART_FLAG.exists()
    return st


@app.post("/api/update/start")
async def api_update_start(payload: dict = Body(default={})):
    ok, msg = updater.start(force=bool(payload.get("force")))
    return JSONResponse({"started": ok, "message": msg},
                        status_code=200 if ok else 409)


@app.post("/api/update/rollback")
async def api_update_rollback():
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
async def api_restart():
    flag = BASE_DIR / ".shutdown"
    try:
        atomic_write_json(flag, {"ts": int(time.time()), "reason": "restart"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    threading.Thread(target=_exit_after_flush, args=(flag,), daemon=True).start()
    return {"status": "restarting",
            "note": "the service manager brings NewGreedy back up automatically"}



@app.get("/api/profiles")
async def api_profiles():
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


@app.get("/api/backup")
async def api_backup():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in (CONFIG_FILE, STATS_FILE, REGISTRY_FILE, CONTROL_FILE):
            if path.exists():
                z.write(path, path.name)
    buf.seek(0)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=newgreedy-backup-{stamp}.zip"},
    )


@app.post("/api/restore")
async def api_restore(file: UploadFile = File(...)):
    data = await file.read(MAX_UPLOAD_SIZE + 1)
    if len(data) > MAX_UPLOAD_SIZE:
        return JSONResponse({"error": "archive too large"}, status_code=413)
    allowed = {"config.ini", "stats.json", "torrent_registry.json", "control.json"}
    restored = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for name in z.namelist():
                base = pathlib.PurePosixPath(name).name
                if base not in allowed or base != name:
                    continue
                atomic_write_bytes(BASE_DIR / base, z.read(name))
                restored.append(base)
    except zipfile.BadZipFile:
        return JSONResponse({"error": "not a valid zip archive"}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    if not restored:
        return JSONResponse({"error": "archive contained no known file"}, status_code=400)
    _invalidate_cache()
    if _cfg and "config.ini" in restored:
        _cfg.read(CONFIG_FILE)
    return {"restored": restored, "note": "restart NewGreedy to apply engine settings"}



@app.get("/api/tracker_stats")
async def api_tracker_stats():
    tc = _load_tracker_cumul()
    cfg_max = 2.5
    if _cfg:
        try:
            cfg_max = float(_cfg.get("spoofing", "max_global_ratio_per_tracker", fallback="2.5"))
        except Exception:
            pass
    result = {}
    for domain, vals in tc.items():
        if not isinstance(vals, dict):
            continue
        ul = float(vals.get("ul", 0))
        dl = float(vals.get("dl", 0))
        ratio = round(ul / dl, 4) if dl > 0 else None
        result[domain] = {
            "ul_mb":      round(ul / 1e6, 2),
            "dl_mb":      round(dl / 1e6, 2),
            "ratio":      ratio,
            "cap":        cfg_max,
            "pct_of_cap": round((ratio / cfg_max) * 100, 1) if ratio is not None else None,
            "last_seen":  vals.get("ts", 0),
        }
    return result


def _max_torrent_ratio() -> float:
    if not _cfg:
        return 3.0
    try:
        return float(_cfg.get("spoofing", "max_ratio_per_torrent", fallback="3.0"))
    except Exception:
        return 3.0


@app.get("/api/health")
async def api_health():
    data      = _load_stats()
    stalled   = [ih for ih, d in data.items() if d.get("stalled", False)]
    anomalies = [ih for ih, d in data.items()
                 if d.get("cumul_rep_dl", 0) > 0
                 and d.get("cumul_rep_ul", 0) / d["cumul_rep_dl"] > _max_torrent_ratio()]
    reached   = [ih for ih, d in data.items() if d.get("target_reached", False)]
    return {"total": len(data), "stalled": stalled, "anomalies": anomalies, "target_reached": reached}


@app.get("/metrics", response_class=PlainTextResponse)
async def api_metrics():
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
        lines.append(f'newgreedy_upload_bytes_total{{hash="{ih}"}} {int(d.get("cumul_rep_ul", 0))}')
    lines += [
        "# HELP newgreedy_download_bytes_total Cumulative download per torrent",
        "# TYPE newgreedy_download_bytes_total gauge",
    ]
    for ih, d in data.items():
        lines.append(f'newgreedy_download_bytes_total{{hash="{ih}"}} {int(d.get("cumul_rep_dl", 0))}')
    lines += [
        "# HELP newgreedy_torrent_ratio Current ratio per torrent",
        "# TYPE newgreedy_torrent_ratio gauge",
    ]
    for ih, d in data.items():
        dl = d.get("cumul_rep_dl", 0)
        r  = (d.get("cumul_rep_ul", 0) / dl) if dl > 0 else 0
        lines.append(f'newgreedy_torrent_ratio{{hash="{ih}"}} {r:.4f}')
    lines += [
        "# HELP newgreedy_announces_total Announce count per torrent",
        "# TYPE newgreedy_announces_total counter",
    ]
    for ih, d in data.items():
        lines.append(f'newgreedy_announces_total{{hash="{ih}"}} {int(d.get("ann_count", 0))}')
    lines += [
        "# HELP newgreedy_tracker_ratio Aggregate ratio per tracker domain",
        "# TYPE newgreedy_tracker_ratio gauge",
    ]
    for domain, v in tc.items():
        if not isinstance(v, dict):
            continue
        dl = float(v.get("dl", 0))
        r  = (float(v.get("ul", 0)) / dl) if dl > 0 else 0
        lines.append(f'newgreedy_tracker_ratio{{domain="{domain}"}} {r:.4f}')
    lines += [
        "# HELP newgreedy_stalled_total Torrents flagged as stalled",
        "# TYPE newgreedy_stalled_total gauge",
        f'newgreedy_stalled_total {sum(1 for d in data.values() if d.get("stalled"))}',
        "# HELP newgreedy_target_reached_total Torrents that reached their target ratio",
        "# TYPE newgreedy_target_reached_total gauge",
        f'newgreedy_target_reached_total {sum(1 for d in data.values() if d.get("target_reached"))}',
    ]
    return "\n".join(lines) + "\n"


@app.get("/api/history")
async def api_history():
    data = _load_stats()
    return {k: v.get("history", []) for k, v in data.items()}


@app.get("/api/history/{ih}")
async def api_history_single(ih: str):
    ih = ih[:8].lower()
    data = _load_stats()
    if ih not in data:
        return JSONResponse({"error": "not found"}, status_code=404)
    return data[ih].get("history", [])


@app.delete("/api/logs")
async def api_logs_clear():
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
async def api_logs(lines: int = Query(default=100, ge=10, le=2000)):
    return {"lines": _tail_log(lines)}


@app.get("/api/config")
async def api_config():
    if _cfg is None:
        return {}
    out = {}
    for s in _cfg.sections():
        out[s] = dict(_cfg[s])
    if "web" in out:
        out["web"] = dict(out["web"])
        if out["web"].get("web_secret_token"):
            out["web"]["web_secret_token"] = "***"
    return out


@app.get("/api/config/editable")
async def api_config_editable():
    return {"keys": sorted([f"{s}.{k}" for s, k in EDITABLE_KEYS])}


@app.post("/api/config/set")
async def api_config_set(payload: dict = Body(...)):
    section = str(payload.get("section", "")).strip()
    key     = str(payload.get("key", "")).strip()
    value   = payload.get("value", "")
    if (section, key) not in EDITABLE_KEYS:
        return JSONResponse({"error": f"key {section}.{key} is not editable"}, status_code=400)
    value = str(value).strip()
    if "\n" in value or "\r" in value:
        return JSONResponse({"error": "invalid value"}, status_code=400)
    problem = _validate_value(key, value)
    if problem:
        return JSONResponse({"error": problem}, status_code=400)
    try:
        lines = CONFIG_FILE.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    cur_section = None
    updated = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            cur_section = stripped[1:-1]
            continue
        if cur_section != section or "=" not in line or stripped.startswith((";", "#")):
            continue
        lhs = line.split("=", 1)[0]
        if lhs.strip() != key:
            continue
        lines[i] = f"{lhs}= {value}" if lhs.endswith(" ") else f"{lhs} = {value}"
        updated = True
        break
    if not updated:
        return JSONResponse({"error": f"{section}.{key} not found in config.ini"}, status_code=404)
    try:
        CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    if _cfg:
        _cfg.read(CONFIG_FILE)
    return {"status": "ok", "section": section, "key": key, "value": value,
            "note": "restart required for listen_port / web_port changes"}


@app.post("/api/config/reload")
async def api_config_reload():
    if _cfg:
        _cfg.read(CONFIG_FILE)
    return {"status": "reloaded"}


_version_cache    = None
_version_cache_ts = 0.0
VERSION_TTL_OK    = 3600
VERSION_TTL_ERR   = 300


@app.get("/api/version")
async def api_version():
    global _version_cache, _version_cache_ts
    now = time.monotonic()
    if _version_cache is not None:
        ttl = VERSION_TTL_ERR if _version_cache.get("error") else VERSION_TTL_OK
        if (now - _version_cache_ts) < ttl:
            return dict(_version_cache, cached=True)
    try:
        data = fetch_latest()
        latest = data.get("tag_name", "unknown")
        up_to_date = (parse_version(latest) <= parse_version(VERSION))
        result = {
            "current":          VERSION,
            "latest":           latest,
            "up_to_date":       up_to_date,
            "update_available": not up_to_date,
            "release_url":      data.get("html_url", ""),
            "url":              data.get("html_url", ""),
        }
    except Exception as e:
        reason = str(e)
        if "403" in reason or "rate limit" in reason.lower():
            reason = "GitHub API rate limit reached (60 requests/hour per IP) - retry later"
        elif "Name or service not known" in reason or "Temporary failure" in reason:
            reason = "No network access to api.github.com"
        result = {"current": VERSION, "latest": None, "up_to_date": None,
                  "update_available": False, "error": reason}
    _version_cache    = result
    _version_cache_ts = now
    return dict(result, cached=False)


@app.get("/api/stats/csv")
async def api_stats_csv():
    data = _load_stats()
    rows = ["hash,mode,dl_mb,ul_mb,delta_ul_mb,ratio,size_mb,ann_count,stalled,target_reached"]
    for ih, d in data.items():
        ul    = d.get("cumul_rep_ul", 0) / 1e6
        dl    = d.get("cumul_rep_dl", 0) / 1e6
        size  = (d.get("size_bytes", 0) or (dl * 1e6) or d.get("seed_fake_dl", 0)) / 1e6
        ratio = round(ul / dl, 4) if dl > 0 else ""
        mode  = "SEED" if dl == 0 else "DOWN"
        rows.append(
            f"{ih},{mode},{dl:.2f},{ul:.2f},,{ratio},{size:.2f},"
            f"{d.get('ann_count',0)},{d.get('stalled',False)},{d.get('target_reached',False)}"
        )
    content = "\n".join(rows)
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=newgreedy_stats.csv"},
    )


def _register_torrent(data: bytes, filename: str):
    ih, size = torrent_info(data)
    if not ih:
        raise ValueError("Could not extract info_hash")
    trackers = tracker_urls(data)
    udp = [u for u in trackers if u.lower().startswith("udp://")]
    ih_key = ih[:8]
    reg = _load_registry()
    reg[ih_key] = {
        "info_hash":    ih,
        "size_bytes":   size,
        "name":         _sanitize_filename(filename),
        "trackers":     trackers,
        "udp_trackers": udp,
    }
    _save_registry(reg)
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
            ih, ih_key, size, udp = _register_torrent(data, file.filename or "")
        except (ValueError, IndexError) as e:
            return JSONResponse({"error": f"not a readable .torrent file ({e})"}, status_code=400)
        return {
            "info_hash":       ih,
            "info_hash_short": ih_key,
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


def _read_stats_raw():
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


@app.get("/api/stats/purge")
async def purge_stats_preview(keep_active: bool = True, inactive_hours: int = 0):
    to_del = _select_for_purge(_read_stats_file(), keep_active, inactive_hours)
    return {"would_purge": len(to_del), "hashes": to_del,
            "keep_active": keep_active, "inactive_hours": inactive_hours}


@app.delete("/api/stats/purge")
async def purge_stats(keep_active: bool = True, inactive_hours: int = 0):
    data = _read_stats_raw()
    before = len([k for k in data if VALID_HASH_RE.match(k)])
    to_del = _select_for_purge(data, keep_active, inactive_hours)
    for k in to_del:
        del data[k]
    try:
        atomic_write_json(STATS_FILE, data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    _write_purge_pending(to_del)
    _invalidate_cache()
    after = len([k for k in data if VALID_HASH_RE.match(k)])
    return {"purged": before - after, "remaining": after,
            "keep_active": keep_active, "inactive_hours": inactive_hours}


@app.delete("/api/stats/{ih}")
async def delete_torrent_stat(ih: str):
    ih = ih[:8].lower()
    if not VALID_HASH_RE.match(ih):
        return JSONResponse({"error": "invalid hash"}, status_code=400)
    data = _read_stats_raw()
    if ih not in data:
        return JSONResponse({"error": "not found"}, status_code=404)
    del data[ih]
    try:
        atomic_write_json(STATS_FILE, data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    _write_purge_pending([ih])
    _invalidate_cache()
    return {"deleted": ih}


@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket):
    if _SECRET_TOKEN:
        supplied = ws.query_params.get("token") or ws.cookies.get(TOKEN_COOKIE) or ""
        if not hmac.compare_digest(supplied, _SECRET_TOKEN):
            await ws.close(code=1008)
            return
    await ws.accept()
    for line in _tail_log(500):
        if line.strip():
            await ws.send_text(line)
    try:
        with open(LOG_FILE, "rb") as f:
            f.seek(0, 2)
            last_pos = f.tell()
    except FileNotFoundError:
        last_pos = 0
    try:
        while True:
            try:
                with open(LOG_FILE, "rb") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    if size < last_pos:
                        last_pos = 0
                        await ws.send_text("--- log file truncated or rotated, resuming ---")
                    f.seek(last_pos)
                    chunk    = f.read()
                    last_pos = f.tell()
                if chunk:
                    for line in chunk.decode("utf-8", errors="replace").splitlines():
                        if line.strip():
                            await ws.send_text(line)
            except (WebSocketDisconnect, RuntimeError):
                break
            except FileNotFoundError:
                last_pos = 0
            except Exception as e:
                logger.debug("ws_logs error: %s", e)
                break
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass


_watch_seen = {}


def _scan_watch_folder():
    if not _WATCH_FOLDER:
        return
    folder = pathlib.Path(_WATCH_FOLDER)
    if not folder.is_absolute():
        folder = BASE_DIR / folder
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    present = set()
    for path in folder.glob("*.torrent"):
        present.add(str(path))
        try:
            stat = path.stat()
            sig = (stat.st_mtime, stat.st_size)
            if _watch_seen.get(str(path)) == sig:
                continue
            if stat.st_size > MAX_UPLOAD_SIZE:
                logger.warning("[WATCH] %s skipped - larger than %d MB",
                               path.name, MAX_UPLOAD_SIZE // (1024 * 1024))
                _watch_seen[str(path)] = sig
                continue
            data = path.read_bytes()
            ih, ih_key, size, udp = _register_torrent(data, path.name)
            _watch_seen[str(path)] = sig
            logger.info("[WATCH] imported %s -> %s (%.1f MB)", path.name, ih_key, size / 1e6)
            if udp:
                logger.warning("[UDP_BYPASS] %s uses %d UDP tracker(s) - those announces bypass NewGreedy",
                               path.name, len(udp))
        except Exception as e:
            logger.debug("[WATCH] %s error: %s", path.name, e)
    for gone in [k for k in _watch_seen if k not in present]:
        del _watch_seen[gone]


async def _watch_loop():
    while True:
        try:
            await asyncio.to_thread(_scan_watch_folder)
        except Exception as e:
            logger.debug("watch loop error: %s", e)
        await asyncio.sleep(WATCH_INTERVAL)


@app.get("/{page}", response_class=HTMLResponse)
async def serve_page(page: str):
    valid = {"torrents", "charts", "config", "logs", "help"}
    if page not in valid:
        return HTMLResponse(status_code=404, content="Not found")
    f = STATIC_DIR / f"{page}.html"
    if not f.exists():
        return HTMLResponse(status_code=404, content=f"{page}.html not found in static/")
    return f.read_text(encoding="utf-8")
