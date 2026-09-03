#!/usr/bin/env python3
"""NewGreedy v1.7.6 — Web UI & API"""
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
import hashlib, hmac, json, pathlib, logging, asyncio, re, time, threading
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Query, Request, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse, PlainTextResponse
from _version import VERSION, parse_version, fetch_latest

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
    ("advanced", "event_anomaly_probability"), ("advanced", "corrupt_field_probability"),
    ("advanced", "respect_tracker_interval"),
    ("anti_detection", "user_agent_mode"), ("anti_detection", "user_agent_value"),
    ("anti_detection", "spoof_user_agent"), ("anti_detection", "spoof_peer_id"),
    ("anti_detection", "spoof_peers"), ("anti_detection", "spoof_port"),
    ("anti_detection", "spoof_headers"), ("anti_detection", "intercept_scrape"),
    ("anti_detection", "tracker_whitelist"), ("anti_detection", "tracker_blacklist"),
    ("stats", "persist_stats"), ("stats", "auto_purge_stopped"),
    ("stats", "tracker_cumul_ttl_days"),
}


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
    if _SECRET_TOKEN:
        logger.info("Web UI authentication enabled")
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
        merged = list(set(existing) | set(hashes))
        PURGE_PENDING_FILE.write_text(json.dumps(merged), encoding="utf-8")
    except Exception as e:
        logger.warning("purge_pending write error: %s", e)


def _sanitize_filename(name: str) -> str:
    if not name:
        return ""
    name = name.replace("\x00", "").replace("\n", " ").replace("\r", " ")
    name = re.sub(r"[\\/]", "_", name)
    return name[:255].strip()


def _load_registry():
    try:
        with open(REGISTRY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_registry(reg):
    with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2)


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
    return f.read_text(encoding="utf-8") if f.exists() else "<h1>NewGreedy v1.7.6</h1>"


@app.get("/api/stats")
async def api_stats():
    data = _load_stats()
    reg  = _load_registry()
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


@app.get("/api/health")
async def api_health():
    data      = _load_stats()
    stalled   = [ih for ih, d in data.items() if d.get("stalled", False)]
    anomalies = [ih for ih, d in data.items() if d.get("cumul_rep_ul", 0) < d.get("prev_rep_ul", 0)]
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
        size  = d.get("estimated_size_mb", 0)
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
    ih, size = _torrent_info(data)
    if not ih:
        raise ValueError("Could not extract info_hash")
    trackers = _extract_trackers(data)
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
        ih, ih_key, size, udp = _register_torrent(data, file.filename or "")
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
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/stats/purge")
async def purge_stats_preview(keep_active: bool = True, inactive_hours: int = 0):
    data = _read_stats_file()
    cutoff = time.time() - inactive_hours * 3600 if inactive_hours > 0 else 0
    if keep_active:
        to_del = [k for k, v in data.items()
                  if VALID_HASH_RE.match(k) and isinstance(v, dict) and (
                      v.get("target_reached", False)
                      or (cutoff > 0 and float(v.get("last_announce_ts", 0)) < cutoff)
                  )]
    else:
        to_del = [k for k in data if VALID_HASH_RE.match(k) and isinstance(data[k], dict)]
    return {"would_purge": len(to_del), "hashes": to_del, "keep_active": keep_active, "inactive_hours": inactive_hours}


@app.delete("/api/stats/purge")
async def purge_stats(keep_active: bool = True, inactive_hours: int = 0):
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    before = len([k for k in data if VALID_HASH_RE.match(k)])
    cutoff = time.time() - inactive_hours * 3600 if inactive_hours > 0 else 0
    if keep_active:
        to_del = [k for k, v in data.items()
                  if VALID_HASH_RE.match(k) and isinstance(v, dict) and (
                      v.get("target_reached", False)
                      or (cutoff > 0 and float(v.get("last_announce_ts", 0)) < cutoff)
                  )]
    else:
        to_del = [k for k in data if VALID_HASH_RE.match(k) and isinstance(data[k], dict)]
    for k in to_del:
        del data[k]
    try:
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    _write_purge_pending(to_del)
    _invalidate_cache()
    after = len([k for k in data if VALID_HASH_RE.match(k)])
    return {"purged": before - after, "remaining": after, "keep_active": keep_active, "inactive_hours": inactive_hours}


@app.delete("/api/stats/{ih}")
async def delete_torrent_stat(ih: str):
    ih = ih[:8].lower()
    if not VALID_HASH_RE.match(ih):
        return JSONResponse({"error": "invalid hash"}, status_code=400)
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    if ih not in data:
        return JSONResponse({"error": "not found"}, status_code=404)
    del data[ih]
    try:
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
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
    for path in folder.glob("*.torrent"):
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


def _torrent_info(raw: bytes):
    marker = b"4:info"
    idx = raw.find(marker)
    if idx == -1:
        raise ValueError("No info key in torrent file")
    start = idx + len(marker)
    pos   = start
    depth = 0
    while pos < len(raw):
        c = raw[pos:pos + 1]
        if c in (b"d", b"l"):
            depth += 1
            pos   += 1
        elif c == b"i":
            end = raw.index(b"e", pos + 1)
            pos = end + 1
        elif c == b"e":
            depth -= 1
            pos   += 1
            if depth == 0:
                break
        elif c.isdigit():
            p = pos
            while raw[p:p + 1].isdigit():
                p += 1
            slen = int(raw[pos:p])
            pos  = p + 1 + slen
        else:
            raise ValueError(f"Invalid bencode byte at pos {pos}: {c!r}")
    info_bytes = raw[start:pos]
    ih         = hashlib.sha1(info_bytes).hexdigest()
    size       = _bencode_length(info_bytes)
    return ih, size


def _extract_trackers(raw: bytes):
    urls = []
    idx = raw.find(b"8:announce")
    if idx != -1 and raw[idx + 10:idx + 11] != b"-":
        try:
            s, _ = _bd_str(raw, idx + 10)
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
                        s, pos = _bd_str(raw, pos)
                        urls.append(s.decode("utf-8", "replace"))
                    except Exception:
                        break
                else:
                    break
    clean = []
    for u in urls:
        u = u.strip()
        if u and u not in clean and ("://" in u):
            clean.append(u)
    return clean


def _bencode_length(info: bytes) -> int:
    length_val = _bencode_get_int_key(info, b"length")
    if length_val >= 0:
        return length_val
    total = 0
    files_start = info.find(b"5:filesl")
    if files_start == -1:
        return 0
    pos = files_start + len(b"5:filesl")
    while pos < len(info) and info[pos:pos + 1] == b"d":
        pos += 1
        while pos < len(info) and info[pos:pos + 1] != b"e":
            key, pos = _bd_str(info, pos)
            if key == b"length":
                n, pos = _bd_int(info, pos)
                total += n
            else:
                pos = _bd_skip(info, pos)
        pos += 1
    return total


def _bd_str(data, pos):
    p = pos
    while data[p:p + 1].isdigit():
        p += 1
    slen = int(data[pos:p])
    return data[p + 1:p + 1 + slen], p + 1 + slen


def _bd_int(data, pos):
    end = data.index(b"e", pos + 1)
    return int(data[pos + 1:end]), end + 1


def _bd_skip(data, pos):
    c = data[pos:pos + 1]
    if c == b"i":
        end = data.index(b"e", pos + 1)
        return end + 1
    elif c in (b"d", b"l"):
        pos += 1
        while data[pos:pos + 1] != b"e":
            pos = _bd_skip(data, pos)
        return pos + 1
    elif c.isdigit():
        p = pos
        while data[p:p + 1].isdigit():
            p += 1
        slen = int(data[pos:p])
        return p + 1 + slen
    return pos + 1


def _bencode_get_int_key(data: bytes, key: bytes) -> int:
    try:
        marker = str(len(key)).encode() + b":" + key + b"i"
        idx = data.find(marker)
        if idx == -1:
            return -1
        idx += len(marker)
        end = data.index(b"e", idx)
        return int(data[idx:end])
    except Exception:
        return -1
