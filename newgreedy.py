#!/usr/bin/env python3
import configparser, logging, os, signal, sys, threading
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("NewGreedy")

cfg = configparser.ConfigParser()
cfg.read("config.ini")

def _sighup(signum, frame):
    cfg.read("config.ini")
    logger.info("Config reloaded (SIGHUP)")

try: signal.signal(signal.SIGHUP, _sighup)
except AttributeError: pass

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

def main():
    logger.info("NewGreedy v1.6.0 starting...")
    listen_port = cfg.getint("proxy", "listen_port", fallback=3456)
    logger.info("Launching mitmproxy on 0.0.0.0:%d (HTTP + HTTPS)", listen_port)

    if cfg.getboolean("web", "web_enabled", fallback=True):
        t = threading.Thread(target=_start_web, daemon=True)
        t.start()

    from mitmproxy.tools.main import mitmdump
    sys.argv = [
        "mitmdump",
        "--listen-host", "0.0.0.0",
        "--listen-port", str(listen_port),
        "-s", "newgreedy_addon.py",
    ]
    mitmdump()

if __name__ == "__main__":
    main()
