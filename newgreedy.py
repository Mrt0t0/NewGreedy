#!/usr/bin/env python3
"""
NewGreedy v1.4
Launcher -- starts mitmproxy on a single port (HTTP + HTTPS).
All logic lives in newgreedy_addon.py.
UDP tracker announces bypass this proxy entirely by design.
"""

import configparser, subprocess, sys, logging, threading, shutil
from pathlib import Path

VERSION     = "1.4"
ADDON       = Path(__file__).parent / "newgreedy_addon.py"
CONFIG      = Path(__file__).parent / "config.ini"
GITHUB_REPO = "Mrt0t0/NewGreedy"

logging.getLogger().setLevel(logging.WARNING)
logger = logging.getLogger("newgreedy")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    logger.propagate = False
    _fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(_fmt)
    logger.addHandler(_h)


def load_port():
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(CONFIG, encoding="utf-8")
    return cfg.getint("proxy", "listen_port", fallback=3456)


def check_update():
    try:
        import requests
        r = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest", timeout=5
        )
        latest = r.json().get("tag_name", "").lstrip("v")
        if not latest:
            return
        def ver(s):
            try:
                return tuple(int(x) for x in s.split("."))
            except Exception:
                return (0,)
        if ver(latest) > ver(VERSION):
            logger.info("New version available: v%s  (run: sudo ./install.sh --update)", latest)
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

    threading.Thread(target=check_update, daemon=True).start()

    mitmdump = shutil.which("mitmdump")
    runner   = [mitmdump] if mitmdump else [sys.executable, "-m", "mitmproxy.tools.main"]

    cmd = runner + [
        "--mode",        "regular",
        "--listen-host", "0.0.0.0",
        "--listen-port", str(port),
        "--scripts",     str(ADDON),
        "--set",         "block_global=false",
        "--set",         "ssl_insecure=true",
        "--quiet",
    ]

    try:
        proc = subprocess.run(cmd)
        sys.exit(proc.returncode)
    except KeyboardInterrupt:
        logger.info("Shutdown requested.")
    except FileNotFoundError:
        logger.error("mitmproxy not found. Install: pip install mitmproxy")
        sys.exit(1)


if __name__ == "__main__":
    main()
