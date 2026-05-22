from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse
import configparser, json, os, pathlib, logging, asyncio, re

app = FastAPI(title="NewGreedy Web UI")
_cfg = None
logger = logging.getLogger("NewGreedy.Web")

BASE_DIR   = pathlib.Path(__file__).parent.resolve()
STATS_FILE = BASE_DIR / "stats.json"
LOG_FILE   = BASE_DIR / "newgreedy.log"
STATIC_DIR = BASE_DIR / "static"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

VALID_HASH_RE = re.compile(r"^[0-9a-f]{8,40}$")

def set_config(c):
    global _cfg
    _cfg = c

def _load_stats():
    try:
        with open(STATS_FILE) as f:
            raw = json.load(f)
        return {
            k: v for k, v in raw.items()
            if VALID_HASH_RE.match(k)
            and isinstance(v, dict)
            and "cumul_rep_ul" in v
        }
    except Exception:
        return {}

def _tail_log(n=100):
    try:
        with open(LOG_FILE, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            buf, pos, lines = b"", max(0, size - 32768), []
            f.seek(pos)
            buf = f.read()
        lines = buf.decode("utf-8", errors="replace").splitlines()
        return lines[-n:]
    except Exception:
        return []

@app.get("/", response_class=HTMLResponse)
async def index():
    f = STATIC_DIR / "index.html"
    return f.read_text() if f.exists() else "<h1>NewGreedy v1.5.1</h1>"

@app.get("/api/stats")
async def api_stats():
    return _load_stats()

@app.get("/api/health")
async def api_health():
    data = _load_stats()
    stalled   = [ih for ih, d in data.items() if d.get("stalled", False)]
    anomalies = [ih for ih, d in data.items()
                 if d.get("cumul_rep_ul", 0) < d.get("prev_rep_ul", 0)]
    return {"total": len(data), "stalled": stalled, "anomalies": anomalies}

@app.get("/api/logs")
async def api_logs(lines: int = 100):
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

_ws_clients: list = []

@app.websocket("/ws/logs")
async def ws_logs(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)
    last_pos = 0
    try:
        while True:
            try:
                with open(LOG_FILE, "rb") as f:
                    f.seek(last_pos)
                    chunk = f.read()
                    last_pos = f.tell()
                if chunk:
                    for line in chunk.decode("utf-8", errors="replace").splitlines():
                        if line.strip():
                            await ws.send_text(line)
            except FileNotFoundError:
                pass
            except Exception as e:
                logger.debug("ws_logs error: %s", e)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        if ws in _ws_clients:
            _ws_clients.remove(ws)
