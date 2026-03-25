#!/usr/bin/env python3
"""
NewGreedy v1.3
"""

import configparser, subprocess, sys
import logging
from pathlib import Path

VERSION     = "1.3"
ADDON       = Path(__file__).parent / "newgreedy_addon.py"
CONFIG      = Path(__file__).parent / "config.ini"
GITHUB_REPO = "Mrt0t0/NewGreedy"

# stdout only -- systemd writes stdout to the log file via
# StandardOutput=append: in the unit file.
# A FileHandler here would write the same line twice.
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
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            timeout=5,
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
            logger.info(
                "New version available: v%s  (run: sudo ./install.sh --update)", latest
            )
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

    import shutil
    mitmdump = shutil.which("mitmdump")

    # --mode regular : standard HTTP proxy, no transparent interception.
    # UDP tracker announces are NOT routed through an HTTP proxy by
    # qBittorrent -- they go directly to the tracker over UDP.
    # This proxy only sees HTTP/HTTPS announces.
    if mitmdump:
        cmd = [
            mitmdump,
            "--mode",        "regular",
            "--listen-host", "0.0.0.0",
            "--listen-port", str(port),
            "--scripts",     str(ADDON),
            "--set",         "block_global=false",
            "--set",         "ssl_insecure=false",
            "--quiet",
        ]
    else:
        cmd = [
            sys.executable, "-m", "mitmproxy.tools.main",
            "--mode",        "regular",
            "--listen-host", "0.0.0.0",
            "--listen-port", str(port),
            "--scripts",     str(ADDON),
            "--set",         "block_global=false",
            "--set",         "ssl_insecure=false",
            "--quiet",
        ]

    try:
        proc = subprocess.run(cmd)
        sys.exit(proc.returncode)
    except KeyboardInterrupt:
        logger.info("Shutdown requested.")
    except FileNotFoundError:
        logger.error("mitmproxy not found. Install it with:  pip install mitmproxy")
        sys.exit(1)


if __name__ == "__main__":
    main()
