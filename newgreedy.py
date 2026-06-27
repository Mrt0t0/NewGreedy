#!/usr/bin/env python3
"""NewGreedy v1.7.5 — launcher"""
import configparser, logging, os, signal, sys, threading, time, urllib.request, json
from pathlib import Path

VERSION = "v1.7.5"
GITHUB_REPO = "Mrt0t0/NewGreedy"

_BASE = Path(__file__).parent.resolve()
_CONFIG_FILE = _BASE / "config.ini"

_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
_root_logger.addHandler(_handler)
logger = logging.getLogger("NewGreedy")

cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
cfg.read(str(_CONFIG_FILE))

def _sighup(signum, frame):
    cfg.read(str(_CONFIG_FILE))
    logger.info("Config reloaded (SIGHUP)")

if hasattr(signal, "SIGHUP"):
    signal.signal(signal.SIGHUP, _sighup)

def _parse_version(v: str):
    v = v.lstrip("v").strip()
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])

def _check_update():
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(url, headers={"User-Agent": "NewGreedy-updater"})
        with urllib.request.urlopen(req, timeout=4) as r:
            data = json.loads(r.read())
        latest = data.get("tag_name", "").strip()
        if latest and _parse_version(latest) > _parse_version(VERSION):
            logger.warning("*** UPDATE AVAILABLE: %s → %s — %s ***",
                           VERSION, latest, data.get("html_url", ""))
        else:
            logger.info("NewGreedy is up to date (%s)", VERSION)
    except Exception as e:
        logger.debug("Update check failed: %s", e)

def _start_web():
    try:
        import uvicorn
        from newgreedy_web import app, set_config
        set_config(cfg)
        host = cfg.get("web", "web_host", fallback="0.0.0.0")
        port = cfg.getint("web", "web_port", fallback=8080)
        uvicorn.run(app, host=host, port=port, log_level="warning")
    except Exception as e:
        logger.warning("Web UI not started: %s", e)

def _run_mitm(listen_port):
    from mitmproxy.tools.main import mitmdump
    sys.argv = [
        "mitmdump",
        "--listen-host", "0.0.0.0",
        "--listen-port", str(listen_port),
        "-s", str(_BASE / "newgreedy_addon.py"),
    ]
    mitmdump()

def _port_free(port, timeout=15.0):
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", port))
                return True
        except OSError:
            time.sleep(0.5)
    return False

def _watchdog(listen_port, max_restarts=5):
    restarts = 0
    while restarts < max_restarts:
        logger.info("NewGreedy %s started — proxy on port %d", VERSION, listen_port)
        try:
            _run_mitm(listen_port)
        except SystemExit:
            break
        except Exception as e:
            restarts += 1
            logger.error("mitmproxy crashed (%s) — restarting in 5s [%d/%d]",
                         e, restarts, max_restarts)
            time.sleep(5)
            if not _port_free(listen_port, timeout=15.0):
                logger.error("Port %d still in use after 15s — aborting.", listen_port)
                break
    msg = f"[NewGreedy] Max restarts ({max_restarts}) reached — exiting."
    try:
        for h in list(_root_logger.handlers):
            if "mitmproxy" in type(h).__module__:
                _root_logger.removeHandler(h)
        logger.error(msg)
    except Exception:
        print(msg, file=sys.stderr, flush=True)
    sys.exit(1)

def main():
    logger.info("NewGreedy %s starting...", VERSION)
    listen_port = cfg.getint("proxy", "listen_port", fallback=3456)
    logger.info("Launching mitmproxy on 0.0.0.0:%d", listen_port)
    threading.Thread(target=_check_update, daemon=True).start()
    if cfg.getboolean("web", "web_enabled", fallback=True):
        threading.Thread(target=_start_web, daemon=True).start()
    _watchdog(listen_port)

if __name__ == "__main__":
    main()
