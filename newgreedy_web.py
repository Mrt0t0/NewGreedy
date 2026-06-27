#!/usr/bin/env python3
"""NewGreedy v1.7.5 — Web UI & API"""
import configparser, hashlib, json, pathlib, logging, asyncio, re, urllib.request, time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse

VERSION      = "v1.7.5"
GITHUB_REPO  = "Mrt0t0/NewGreedy"

app      = FastAPI(title="NewGreedy Web UI")
_cfg     = None
logger   = logging.getLogger("NewGreedy.Web")
BASE_DIR = pathlib.Path(__file__).parent.resolve()
STATS_FILE    = BASE_DIR / "stats.json"
REGISTRY_FILE = BASE_DIR / "torrent_registry.json"
LOG_FILE      = BASE_DIR / "newgreedy.log"
STATIC_DIR    = BASE_DIR / "static"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

VALID_HASH_RE = re.compile(r"^[0-9a-f]{8,40}$")

_raw_cache      = None
_raw_cache_ts   = 0.0
_raw_cache_ttl  = 2.0


def set_config(c):
    global _cfg
    _cfg = c


def _read_stats_file():
    global _raw_cache, _raw_cache_ts
    now = time.monotonic()
    if _raw_cache is not None and (now - _raw_cache_ts) < _raw_cache_ttl:
        return _raw_cache
    try:
        with open(STATS_FILE) as f:
            raw = json.load(f)
    except Exception:
        raw = {}
    _raw_cache    = raw
    _raw_cache_ts = now
    return raw


def _invalidate_cache():
    global _raw_cache
    _raw_cache = None


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
    return f.read_text() if f.exists() else "<h1>NewGreedy v1.7.5</h1>"


@app.get("/{page}", response_class=HTMLResponse)
async def serve_page(page: str):
    valid = {"torrents", "charts", "config", "logs", "help"}
    if page not in valid:
        return HTMLResponse(status_code=404, content="Not found")
    f = STATIC_DIR / f"{page}.html"
    if not f.exists():
        return HTMLResponse(status_code=404, content=f"{page}.html not found in static/")
    return f.read_text()


@app.get("/api/stats")
async def api_stats():
    data = _load_stats()
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
        result[k] = entry
    return result


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
        }
    return result


@app.get("/api/health")
async def api_health():
    data      = _load_stats()
    stalled   = [ih for ih, d in data.items() if d.get("stalled", False)]
    anomalies = [ih for ih, d in data.items() if d.get("cumul_rep_ul", 0) < d.get("prev_rep_ul", 0)]
    reached   = [ih for ih, d in data.items() if d.get("target_reached", False)]
    return {"total": len(data), "stalled": stalled, "anomalies": anomalies, "target_reached": reached}


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


@app.get("/api/logs")
async def api_logs(lines: int = Query(default=100, ge=10, le=2000)):
    return {"lines": _tail_log(lines)}


@app.get("/api/config")
async def api_config():
    if _cfg is None:
        return {}
    return {s: dict(_cfg[s]) for s in _cfg.sections()}


@app.post("/api/config/reload")
async def api_config_reload():
    if _cfg:
        _cfg.read(BASE_DIR / "config.ini")
    return {"status": "reloaded"}


@app.get("/api/version")
async def api_version():
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(url, headers={"User-Agent": "NewGreedy-updater"})
        with urllib.request.urlopen(req, timeout=4) as r:
            data = json.loads(r.read())
        latest = data.get("tag_name", "unknown")

        def _pv(v):
            parts = v.lstrip("v").split(".")
            return tuple(int(x) if x.isdigit() else 0 for x in (parts + ["0", "0"])[:3])

        up_to_date = (_pv(latest) <= _pv(VERSION))
        return {
            "current":          VERSION,
            "latest":           latest,
            "up_to_date":       up_to_date,
            "update_available": not up_to_date,
            "release_url":      data.get("html_url", ""),
            "url":              data.get("html_url", ""),
        }
    except Exception as e:
        return {"current": VERSION, "latest": None, "up_to_date": None, "error": str(e)}


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


@app.post("/api/upload")
async def api_upload_torrent(file: UploadFile = File(...)):
    try:
        data = await file.read()
        ih, size = _torrent_info(data)
        if not ih:
            return JSONResponse({"error": "Could not extract info_hash"}, status_code=400)
        ih_key = ih[:8]
        try:
            with open(REGISTRY_FILE) as f:
                reg = json.load(f)
        except Exception:
            reg = {}
        reg[ih_key] = {"info_hash": ih, "size_bytes": size, "name": file.filename or ""}
        with open(REGISTRY_FILE, "w") as f:
            json.dump(reg, f, indent=2)
        return {
            "info_hash":       ih,
            "info_hash_short": ih_key,
            "size_bytes":      size,
            "size_mb":         round(size / 1e6, 2),
            "infohash":        ih,
            "size_estimate":   size,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/stats/purge")
async def purge_stats_preview(keep_active: bool = True, inactive_hours: int = 0):
    try:
        with open(STATS_FILE) as f:
            data = json.load(f)
    except Exception:
        data = {}
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
        with open(STATS_FILE) as f:
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
        with open(STATS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    _invalidate_cache()
    after = len([k for k in data if VALID_HASH_RE.match(k)])
    return {"purged": before - after, "remaining": after, "keep_active": keep_active, "inactive_hours": inactive_hours}


@app.delete("/api/stats/{ih}")
async def delete_torrent_stat(ih: str):
    ih = ih[:8].lower()
    if not VALID_HASH_RE.match(ih):
        return JSONResponse({"error": "invalid hash"}, status_code=400)
    try:
        with open(STATS_FILE) as f:
            data = json.load(f)
    except Exception:
        data = {}
    if ih not in data:
        return JSONResponse({"error": "not found"}, status_code=404)
    del data[ih]
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    _invalidate_cache()
    return {"deleted": ih}


@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket):
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
                pass
            except Exception as e:
                logger.debug("ws_logs error: %s", e)
                break
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass


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
