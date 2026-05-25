from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse
import configparser, json, os, pathlib, logging, asyncio, re, time

app = FastAPI(title="NewGreedy Web UI v1.6")
_cfg   = None
_addon = None
logger = logging.getLogger("NewGreedy.Web")

BASE_DIR   = pathlib.Path(__file__).parent.resolve()
STATS_FILE = BASE_DIR / "stats.json"
LOG_FILE   = BASE_DIR / "newgreedy.log"
STATIC_DIR = BASE_DIR / "static"
VALID_HASH = re.compile(r"^[0-9a-f]{8,40}$")

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

def set_config(c):
    global _cfg
    _cfg = c

def set_addon(a):
    global _addon
    _addon = a

def _load_stats():
    try:
        with open(STATS_FILE) as f:
            raw = json.load(f)
        return {k: v for k, v in raw.items()
                if VALID_HASH.match(k) and isinstance(v, dict) and "cumul_rep_ul" in v}
    except Exception:
        return {}

def _tail_log(n=500):
    try:
        with open(LOG_FILE, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 131072))
            buf = f.read()
        lines = buf.decode("utf-8", errors="replace").splitlines()
        return lines[-n:]
    except Exception:
        return []

def _html(name):
    p = STATIC_DIR / name
    return p.read_text(encoding="utf-8") if p.exists() else f"<h1>{name} not found</h1>"


@app.get("/", response_class=HTMLResponse)
async def index():
    return _html("index.html")

@app.get("/torrents", response_class=HTMLResponse)
async def torrents():
    return _html("torrents.html")

@app.get("/charts", response_class=HTMLResponse)
async def charts():
    return _html("charts.html")

@app.get("/config", response_class=HTMLResponse)
async def config():
    return _html("config.html")

@app.get("/logs", response_class=HTMLResponse)
async def logs():
    return _html("logs.html")

@app.get("/help", response_class=HTMLResponse)
async def help_page():
    return _html("help.html")


@app.get("/api/stats")
async def api_stats():
    return JSONResponse(_load_stats())


@app.get("/api/health")
async def api_health():
    stats = _load_stats()
    now   = time.time()
    health = {
        "version":           "1.6.0",
        "active":            0,
        "stall_net":         [],
        "stall_algo":        [],
        "stag_prolonged":    [],
        "ended":             [],
        "conv_done":         [],
        "dead_trackers":     [],
        "connect_errors":    0,
        "last_save_ts":      0,
        "announce_timeouts": 0,
    }
    for ih, d in stats.items():
        if not d.get("ended", False):
            health["active"] += 1
        if d.get("is_stalled_net"):
            health["stall_net"].append(ih[:8])
        if d.get("is_stalled_algo"):
            health["stall_algo"].append(ih[:8])
        if d.get("stag_count", 0) >= 3:
            health["stag_prolonged"].append(ih[:8])
        if d.get("ended"):
            health["ended"].append(ih[:8])
        if d.get("conv_done"):
            health["conv_done"].append(ih[:8])

    if _addon:
        health["connect_errors"]    = getattr(_addon, "_connect_errors", 0)
        health["last_save_ts"]      = getattr(_addon, "_last_save_ts", 0)
        health["announce_timeouts"] = getattr(_addon, "_announce_timeouts", 0)
        for domain, e in getattr(_addon, "_tracker_errors", {}).items():
            if e.get("dead"):
                health["dead_trackers"].append({
                    "domain":      domain,
                    "since":       e.get("dead_since", 0),
                    "expires_in_h": round(max(0, 12 - (now - e.get("dead_since", 0)) / 3600), 1)
                })

    return JSONResponse(health)


@app.get("/api/torrents/{hash}/history")
async def api_torrent_history(hash: str, limit: int = 500):
    if not VALID_HASH.match(hash):
        return JSONResponse({"error": "invalid hash"}, status_code=400)
    stats = _load_stats()
    if hash not in stats:
        return JSONResponse({"error": "not found"}, status_code=404)
    hist = stats[hash].get("history", [])
    return JSONResponse({"hash": hash, "history": hist[-limit:]})


@app.get("/api/logs")
async def api_logs(n: int = 500):
    return JSONResponse({"lines": _tail_log(n)})


@app.get("/api/config")
async def api_config():
    if not _cfg:
        return JSONResponse({})
    out = {}
    for sec in _cfg.sections():
        out[sec] = dict(_cfg[sec])
    return JSONResponse(out)


@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket):
    await ws.accept()
    pos = 0
    try:
        while True:
            try:
                with open(LOG_FILE, "rb") as f:
                    f.seek(0, 2)
                    size = f.tell()
                    if size > pos:
                        f.seek(pos)
                        chunk = f.read()
                        pos   = size
                        lines = chunk.decode("utf-8", errors="replace").splitlines()
                        for line in lines[-50:]:
                            await ws.send_text(line)
            except Exception:
                pass
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass


@app.websocket("/ws/stats")
async def ws_stats(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            await ws.send_text(json.dumps(_load_stats()))
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        pass
