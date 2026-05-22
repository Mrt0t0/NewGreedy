from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse
import configparser, json, os, pathlib, logging

app = FastAPI(title="NewGreedy Web UI")
_cfg = None
logger = logging.getLogger("NewGreedy.Web")

def set_config(c):
    global _cfg
    _cfg = c

STATS_FILE = "stats.json"
STATIC_DIR = pathlib.Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", response_class=HTMLResponse)
async def index():
    f = STATIC_DIR / "index.html"
    return f.read_text() if f.exists() else "<h1>NewGreedy v1.5.1</h1>"

@app.get("/api/stats")
async def api_stats():
    try:
        with open(STATS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

@app.get("/api/health")
async def api_health():
    try:
        with open(STATS_FILE) as f:
            data = json.load(f)
        stalled = [ih for ih, d in data.items() if d.get("stalled", False)]
        anomalies = []
        for ih, d in data.items():
            cum_ul = d.get("cumul_rep_ul", 0)
            prev   = d.get("prev_rep_ul", 0)
            if cum_ul < prev:
                anomalies.append(ih)
        return {"total": len(data), "stalled": stalled, "anomalies": anomalies}
    except Exception:
        return {"total": 0, "stalled": [], "anomalies": []}

@app.get("/api/config")
async def api_config():
    if _cfg is None:
        return {}
    return {s: dict(_cfg[s]) for s in _cfg.sections()}

@app.post("/api/config/reload")
async def api_config_reload():
    if _cfg:
        _cfg.read("config.ini")
    return {"status": "reloaded"}
