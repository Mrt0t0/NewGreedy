#!/usr/bin/env python3
"""
NewGreedy v1.3
Launcher -- starts mitmproxy on a single port (HTTP + HTTPS).
All logic lives in newgreedy_addon.py.
"""

import configparser, logging, os, signal, subprocess, sys
from pathlib import Path

VERSION     = "1.3"
ADDON       = Path(__file__).parent / "newgreedy_addon.py"
CONFIG      = Path(__file__).parent / "config.ini"
LOG_FILE    = Path(__file__).parent / "newgreedy.log"
GITHUB_REPO = "Mrt0t0/NewGreedy"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("newgreedy")


def load_port():
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(CONFIG, encoding="utf-8")
    return cfg.getint("proxy", "listen_port", fallback=3456)


def check_update():
    try:
        import requests
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            timeout=5,
        )
        latest = r.json().get("tag_name", "").lstrip("v")
        if latest and latest != VERSION:
            logger.info("New version available: v%s  (run update.sh to upgrade)", latest)
        else:
            logger.info("NewGreedy v%s is up to date.", VERSION)
    except Exception:
        pass


def main():
    logger.info("NewGreedy v%s starting...", VERSION)

    if not ADDON.exists():
        logger.error("newgreedy_addon.py not found at %s", ADDON)
        sys.exit(1)

    port = load_port()
    logger.info("Launching mitmproxy on 0.0.0.0:%d  (HTTP + HTTPS)", port)

    import threading
    threading.Thread(target=check_update, daemon=True).start()

    cmd = [
        sys.executable, "-m", "mitmproxy.tools.main",
        "--mode",         "regular",
        "--listen-host",  "0.0.0.0",
        "--listen-port",  str(port),
        "--scripts",      str(ADDON),
        "--set",          "block_global=false",
        "--set",          "ssl_insecure=false",
        "--quiet",
    ]

    # Prefer mitmdump (no UI) when available
    try:
        import shutil
        mitmdump = shutil.which("mitmdump")
        if mitmdump:
            cmd = [
                mitmdump,
                "--mode",         "regular",
                "--listen-host",  "0.0.0.0",
                "--listen-port",  str(port),
                "--scripts",      str(ADDON),
                "--set",          "block_global=false",
                "--set",          "ssl_insecure=false",
                "--quiet",
            ]
    except Exception:
        pass

    try:
        proc = subprocess.run(cmd)
        sys.exit(proc.returncode)
    except KeyboardInterrupt:
        logger.info("Shutdown requested.")
    except FileNotFoundError:
        logger.error(
            "mitmproxy not found. Install it with:  pip install mitmproxy"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
