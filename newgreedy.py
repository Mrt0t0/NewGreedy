#!/usr/bin/env python3
"""NewGreedy v1.7.6 — launcher"""
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
import configparser, json, logging, os, signal, sys, tempfile, threading, time, socket
from pathlib import Path
from _version import VERSION, parse_version, fetch_latest

_BASE = Path(__file__).parent.resolve()
_CONFIG_FILE = _BASE / "config.ini"
_SHUTDOWN_FLAG = _BASE / ".shutdown"

_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
_root_logger.addHandler(_handler)
logging.getLogger("passlib").setLevel(logging.ERROR)
logger = logging.getLogger("NewGreedy")

cfg = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
cfg.read(str(_CONFIG_FILE))

_shutting_down = threading.Event()


def _sighup(signum, frame):
    cfg.read(str(_CONFIG_FILE))
    logger.info("Config reloaded (SIGHUP)")


def _request_addon_flush():
    try:
        with open(_SHUTDOWN_FLAG, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time()}, f)
    except Exception as e:
        logger.debug("shutdown flag write failed: %s", e)


def _shutdown(signum, frame):
    if _shutting_down.is_set():
        return
    _shutting_down.set()
    name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
    logger.info("Received %s - flushing stats and shutting down...", name)
    _request_addon_flush()
    time.sleep(1.5)
    sys.exit(0)


if hasattr(signal, "SIGHUP"):
    signal.signal(signal.SIGHUP, _sighup)
for _sig in ("SIGTERM", "SIGINT"):
    if hasattr(signal, _sig):
        try:
            signal.signal(getattr(signal, _sig), _shutdown)
        except (ValueError, OSError):
            pass


def _check_update():
    try:
        socket.setdefaulttimeout(4)
        data = fetch_latest(timeout=4)
        latest = data.get("tag_name", "").strip()
        if latest and parse_version(latest) > parse_version(VERSION):
            logger.warning("*** UPDATE AVAILABLE: %s -> %s - %s ***",
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
    while restarts < max_restarts and not _shutting_down.is_set():
        logger.info("NewGreedy %s started - proxy on port %d", VERSION, listen_port)
        try:
            _run_mitm(listen_port)
        except SystemExit:
            break
        except Exception as e:
            if _shutting_down.is_set():
                break
            restarts += 1
            logger.error("mitmproxy crashed (%s) - restarting in 5s [%d/%d]",
                         e, restarts, max_restarts)
            time.sleep(5)
            if not _port_free(listen_port, timeout=15.0):
                logger.error("Port %d still in use after 15s - aborting.", listen_port)
                break
    if _shutting_down.is_set():
        return
    msg = f"[NewGreedy] Max restarts ({max_restarts}) reached - exiting."
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
    try:
        _SHUTDOWN_FLAG.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass
    listen_port = cfg.getint("proxy", "listen_port", fallback=3456)
    logger.info("Launching mitmproxy on 0.0.0.0:%d", listen_port)
    threading.Thread(target=_check_update, daemon=True).start()
    if cfg.getboolean("web", "web_enabled", fallback=True):
        threading.Thread(target=_start_web, daemon=True).start()
    _watchdog(listen_port)


if __name__ == "__main__":
    main()
