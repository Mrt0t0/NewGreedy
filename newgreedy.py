#!/usr/bin/env python3
"""NewGreedy — launcher"""
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
import argparse, json, logging, re, signal, sys, threading, time
from _version import VERSION, parse_version, fetch_latest
import config_schema as cs
import datastore

_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
_root_logger.addHandler(_handler)
logging.getLogger("passlib").setLevel(logging.ERROR)
logger = logging.getLogger("NewGreedy")

RESTART_DELAY_S = 5
RESTART_WINDOW_S = 600

cfg = None
_shutting_down = threading.Event()


def _load_cfg():
    global cfg
    cfg = cs.load(datastore.CONFIG_FILE)
    return cfg


def _sighup(signum, frame):
    _load_cfg()
    try:
        from newgreedy_web import set_config
        set_config(cfg)
    except Exception:
        pass
    logger.info("Config reloaded (SIGHUP) - the engine picks up config.ini changes by itself")


def _request_addon_flush():
    try:
        with open(datastore.SHUTDOWN_FLAG, "w", encoding="utf-8") as f:
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


def _install_signals():
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
        data = fetch_latest(timeout=4, channel=cs.get(cfg, "update", "update_channel"))
        latest = data.get("tag_name", "").strip()
        if latest and parse_version(latest) > parse_version(VERSION):
            logger.warning("*** UPDATE AVAILABLE: %s -> %s - %s ***",
                           VERSION, latest, data.get("html_url", ""))
        else:
            logger.info("NewGreedy is up to date (%s)", VERSION)
    except Exception as e:
        logger.debug("Update check failed: %s", e)


def _check_bcrypt():
    import diagnostics
    v = diagnostics.bcrypt_conflict()
    if v:
        logger.error("bcrypt %s is installed and crashes the passlib version mitmproxy uses - "
                     "remove it with: pip uninstall bcrypt", v)


def _merge_shipped_config():
    """Bring the data folder's config.ini up to the shipped one (Docker image upgrades)."""
    shipped, current = datastore.SHIPPED_CONFIG, datastore.CONFIG_FILE
    if shipped.resolve() == current.resolve() or not shipped.is_file() or not current.is_file():
        return
    try:
        import config_merge, backups
        shipped_text = shipped.read_text(encoding="utf-8")
        current_text = current.read_text(encoding="utf-8")
        if not config_merge.needs_merge(shipped_text, current_text):
            return
        backups.create("pre-merge")
        merged, report = config_merge.merge(shipped_text, current_text)
        from _version import atomic_write_bytes
        atomic_write_bytes(current, merged.encode("utf-8"))
        for line in config_merge.format_report(report):
            logger.info("[CONFIG] %s", line)
    except Exception as e:
        logger.warning("[CONFIG] config.ini left untouched, merge failed: %s", e)


def _ensure_token():
    if not cs.get(cfg, "web", "web_enabled"):
        return
    host = cs.get(cfg, "web", "web_host")
    if cs.is_loopback(host) or cs.get(cfg, "web", "web_secret_token"):
        return
    if cfg.get("web", "web_secret_token", fallback="").strip():
        logger.warning("web_secret_token in config.ini is not valid (8 to 256 printable "
                       "characters, no spaces) - replaced by a generated one")
    token = cs.new_token()
    try:
        cs.set_value(datastore.CONFIG_FILE, "web", "web_secret_token", token)
    except Exception as e:
        logger.error("Web UI bound to %s without a token and none could be saved (%s) - "
                     "the web UI is disabled", host, e)
        cfg.set("web", "web_enabled", "false")
        return
    _load_cfg()
    port = cs.get(cfg, "web", "web_port")
    prefix = cs.get(cfg, "web", "web_path_prefix")
    # printed to the console only, never to newgreedy.log
    print("=" * 72, flush=True)
    print(f"  Web UI listens on {host}:{port} - an access token was generated", flush=True)
    print(f"  and saved as web_secret_token in {datastore.CONFIG_FILE}", flush=True)
    print(f"  Open: http://<this-host>:{port}/{prefix + '/' if prefix else ''}?token={token}", flush=True)
    print("=" * 72, flush=True)


def _start_web():
    try:
        import uvicorn
        from newgreedy_web import app, set_config
        set_config(cfg)
        host = cs.get(cfg, "web", "web_host")
        port = cs.get(cfg, "web", "web_port")
        uvicorn.run(app, host=host, port=port, log_level="warning")
    except Exception as e:
        logger.warning("Web UI not started: %s", e)


def _upstream_args():
    url = cs.get(cfg, "upstream", "proxy_url")
    if not url:
        return []
    args = ["--mode", "upstream:" + url]
    bypass = cs.get(cfg, "upstream", "proxy_bypass")
    for domain in bypass:
        args += ["--ignore-hosts", re.escape(domain)]
    logger.info("Upstream proxy enabled: %s%s", url,
                f" (bypass: {', '.join(bypass)})" if bypass else "")
    return args


def _mitm_args(listen_host, listen_port):
    import diagnostics
    args = ["mitmdump",
            "--listen-host", listen_host,
            "--listen-port", str(listen_port),
            "--set", f"confdir={diagnostics.mitm_confdir()}",
            "-s", str(datastore.BASE_DIR / "newgreedy_addon.py")]
    if not cs.get(cfg, "ssl", "ssl_verify_trackers"):
        args.append("--ssl-insecure")
        logger.warning("ssl_verify_trackers = false - tracker certificates are not checked")
    return args + _upstream_args()


def _run_mitm(listen_host, listen_port):
    from mitmproxy.tools.main import mitmdump
    sys.argv = _mitm_args(listen_host, listen_port)
    return mitmdump()


def _port_free(host, port, timeout=15.0):
    import diagnostics
    deadline = time.time() + timeout
    while time.time() < deadline:
        if diagnostics.can_bind(host, port):
            return True
        time.sleep(0.5)
    return False


def _drop_mitm_log_handlers():
    """mitmdump leaves its log handler on the root logger, bound to its closed event loop:
    any later log line would raise 'Event loop is closed'."""
    for lg in (_root_logger, logger):
        for h in list(lg.handlers):
            if "mitmproxy" in type(h).__module__:
                lg.removeHandler(h)


def _watchdog(listen_host, listen_port, max_restarts=5):
    """mitmproxy installs its own SIGINT/SIGTERM handlers: a stop request makes mitmdump()
    return normally, so a normal return means stop. Only failures are restarted."""
    failures = []
    while not _shutting_down.is_set():
        logger.info("NewGreedy %s started - proxy on %s:%d", VERSION, listen_host, listen_port)
        try:
            _run_mitm(listen_host, listen_port)
            _drop_mitm_log_handlers()
            logger.info("NewGreedy %s stopped.", VERSION)
            return 0
        except SystemExit as e:
            _drop_mitm_log_handlers()
            if e.code in (None, 0) or _shutting_down.is_set():
                return 0
            reason = f"mitmproxy exited with code {e.code}"
        except Exception as e:
            _drop_mitm_log_handlers()
            if _shutting_down.is_set():
                return 0
            reason = f"mitmproxy crashed ({e})"
        now = time.time()
        failures = [t for t in failures if now - t < RESTART_WINDOW_S] + [now]
        if len(failures) > max_restarts:
            break
        logger.error("%s - restarting in %ds [%d/%d]", reason, RESTART_DELAY_S,
                     len(failures), max_restarts)
        time.sleep(RESTART_DELAY_S)
        if not _port_free(listen_host, listen_port, timeout=15.0):
            logger.error("Port %d still in use after 15s - aborting.", listen_port)
            break
    if _shutting_down.is_set():
        return 0
    msg = f"[NewGreedy] Max restarts ({max_restarts}) reached - exiting."
    try:
        logger.error(msg)
    except Exception:
        print(msg, file=sys.stderr, flush=True)
    return 1


def _consume_restart_flag():
    flag = datastore.RESTART_FLAG
    try:
        info = json.loads(flag.read_text(encoding="utf-8"))
        flag.unlink()
        logger.info("Updated to %s - running the new files", info.get("tag", "?"))
    except FileNotFoundError:
        pass
    except Exception:
        try:
            flag.unlink()
        except OSError:
            pass


def check_config():
    import diagnostics
    datastore.ensure_data_dir()
    _load_cfg()
    print(f"NewGreedy {VERSION} - checking {datastore.CONFIG_FILE}\n")
    checks = diagnostics.run(cfg, live=False)
    diagnostics.print_report(checks)
    return 1 if diagnostics.summary(checks) == "error" else 0


def main(argv=None):
    p = argparse.ArgumentParser(description=f"NewGreedy {VERSION}")
    p.add_argument("--check-config", action="store_true",
                   help="check config.ini, ports, data folder and CA, then exit")
    args = p.parse_args(argv)
    if args.check_config:
        return check_config()

    _install_signals()
    logger.info("NewGreedy %s starting...", VERSION)
    moved = datastore.ensure_data_dir()
    if moved:
        logger.info("Data folder %s initialised with: %s", datastore.DATA_DIR, ", ".join(moved))
    _merge_shipped_config()
    _load_cfg()
    level = cs.get(cfg, "advanced", "log_level")
    logging.getLogger("NewGreedy").setLevel(getattr(logging, level, logging.INFO))
    for level_, section, key, msg in cs.check(cfg):
        if level_ == "error":
            logger.warning("config.ini [%s] %s: %s - default used", section, key, msg)
    _check_bcrypt()
    _consume_restart_flag()
    try:
        datastore.SHUTDOWN_FLAG.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass
    _ensure_token()
    listen_host = cs.get(cfg, "proxy", "listen_host")
    listen_port = cs.get(cfg, "proxy", "listen_port")
    logger.info("Launching mitmproxy on %s:%d", listen_host, listen_port)
    threading.Thread(target=_check_update, daemon=True).start()
    if cs.get(cfg, "web", "web_enabled"):
        threading.Thread(target=_start_web, daemon=True).start()
    return _watchdog(listen_host, listen_port)


if __name__ == "__main__":
    sys.exit(main())
