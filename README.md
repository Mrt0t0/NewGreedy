<div align="center">

# NewGreedy

### BitTorrent announce proxy — Upload ratio spoofer

[![Version](https://img.shields.io/badge/version-1.7.7-blue?style=flat-square)](https://github.com/Mrt0t0/NewGreedy/releases)
[![Python](https://img.shields.io/badge/python-3.9%2B-green?style=flat-square)](https://python.org)
[![License](https://img.shields.io/badge/license-GPLv3-gray?style=flat-square)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows%20%7C%20Docker-lightgray?style=flat-square)](https://github.com/Mrt0t0/NewGreedy)

</div>

---

> **Disclaimer** — NewGreedy is provided for **educational and research purposes only**.
> Using this tool on private trackers may violate their terms of service and result in a permanent ban.
> The author assumes no responsibility for any misuse. **Use at your own risk.**

---

## Contents

- [What does NewGreedy do?](#what-does-newgreedy-do)
- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation) — [Linux](#linux) · [macOS](#macos) · [Windows](#windows) · [Docker](#docker)
- [Updating](#updating)
- [Configure your torrent client](#configure-your-torrent-client)
- [Configuration reference](#configuration-reference)
- [Web UI](#web-ui)
- [REST API](#rest-api)
- [Reading the logs](#reading-the-logs)
- [Troubleshooting](#troubleshooting)
- [Files](#files)
- [License](#license)
- [Changelog](#changelog)

---

## What does NewGreedy do?

NewGreedy is a **local HTTP/HTTPS proxy** that sits between your torrent client and the tracker.
Every time your client sends an announce, NewGreedy intercepts it and rewrites the `uploaded`, `downloaded`, `left`, `peer_id` and `port` fields before forwarding the request, as one consistent client that behaves the way the real one would.

Your torrent client and the files on disk are **never touched**. Only the statistics reported to trackers change.

---

## Features

**Ratio engine**
- Brings each torrent to `target_ratio` with a non-linear catch-up, random noise and deliberate pauses, then stops
- Caps per torrent (`max_ratio_per_torrent`) and per tracker across all torrents (`max_global_ratio_per_tracker`)
- Credits upload to pure seeders, and holds back when the tracker reports no leecher
- One upload budget shared by every torrent, with active hours and a smooth day/night curve

**Looking like a real client**
- One identity per session — peer_id, port, `key`, User-Agent, headers and parameter order of a real qBittorrent, Transmission, Deluge, libtorrent or rTorrent build
- Carried across restarts the way a real install behaves: same client, fresh peer_id, occasional version upgrade
- Announces spread per torrent, a gradual start after a restart, tracker intervals only ever lengthened
- `downloaded` and `left` always consistent with the torrent size; `trackerid` returned; scrapes forwarded

**Control**
- Pause, ratio override and tags per torrent, applied within seconds without a restart
- `[tracker:domain]` sections to set different rules for one tracker
- Watch folder: drop `.torrent` files to register their name, size and trackers
- Dry-run mode that logs every decision without changing a single announce

**Web UI and integrations**
- Dashboard, sortable torrents table, ratio charts, live logs and settings editor
- REST API and Prometheus `/metrics`
- Webhooks to Discord, Slack or any JSON endpoint
- Upstream proxy such as Gluetun; optional access token, hidden path and rate limiting

**Operations**
- Installers for Linux (systemd), macOS (launchd) and Windows (Task Scheduler), plus Docker
- Update from the command line or in one click from the web UI — settings merged, data kept
- Backup and restore archive, atomic writes, log rotation, clean flush on shutdown
- UDP trackers detected and flagged, since their announces bypass the proxy

---

## How it works

```
Torrent client  ──► NewGreedy :3456 (proxy)  ──► Tracker
                         │
                         ├─ Intercepts announces (HTTP + HTTPS via mitmproxy)
                         ├─ Rewrites: uploaded / downloaded / left / peer_id / port
                         │            numwant / key / trackerid
                         ├─ Applies ratio engine (non-linear progression, noise, stagnation)
                         ├─ Guards per-tracker global ratio cap
                         ├─ Keeps downloaded + left = size, and left falling at a real speed
                         ├─ Spreads announce timing across torrents (see below)
                         └─ Forwards patched request to tracker
```

**Announce timing:**
```
tracker interval  ──► + per-torrent offset (stable, derived from infohash + session)
                      + slow hourly drift
                      + extra spread when several torrents cluster on one tracker
                      ──► rewritten in the tracker reply, later only, never earlier
```

Torrents that start together at launch would otherwise keep announcing together forever.
Each one gets its own period, so the group fans out over the first hours and the tracker
sees a natural, uncorrelated stream instead of a repeating burst.

**Upload calculation per torrent:**
```
target_ul  = cumul_downloaded × target_ratio
remaining  = target_ul − cumul_reported_ul
increment  = remaining × catch_up_factor × e^(−decay) × Pareto_noise
             capped at max_simulated_speed_mbps × interval
             capped at max_ratio_per_torrent × cumul_downloaded
             capped by global tracker ratio guard (corrected before accumulation)
             ──► cumul_rep_ul sent to tracker as `uploaded`
```

Injection stops automatically when `target_ratio` is reached (`auto_stop_at_target = true`).

---

## Requirements

| Dependency | Version |
|---|---|
| Python | 3.9+ (3.11 recommended) |
| mitmproxy | 10.0+ |
| fastapi | 0.110+ |
| uvicorn | 0.27+ |
| python-multipart | 0.0.9+ |
| bcrypt | 4.x (**not** 5.x — see [Troubleshooting](#troubleshooting)) |

---

## Installation

### Linux

**1. Clone the repository**

```bash
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
```

**2. Run the installer**

Requires `sudo` — installs to `/opt/newgreedy` and registers a systemd service.

```bash
sudo ./install.sh
```

The installer:
- Installs Python dependencies via `pip`
- Copies all files to `/opt/newgreedy/`
- Generates the mitmproxy CA certificate and adds it to the system trust store
- Creates and enables a `systemd` service (`newgreedy.service`) that starts at boot

**3. Verify**

```bash
systemctl status newgreedy
xdg-open http://localhost:8080
```

**4. Trust the CA in your browser**

The CA is automatically added to the OS trust store. For browsers with their own store (Firefox):
- File: `/usr/local/share/ca-certificates/mitmproxy-ca.crt`
- Firefox → Settings → Privacy & Security → Certificates → View Certificates → Import

Keep the cloned folder: updates are run from it — see [Updating](#updating).

**Uninstall:**
```bash
sudo ./uninstall.sh
```

**Service management:**
```bash
systemctl start newgreedy
systemctl stop newgreedy
systemctl restart newgreedy
journalctl -u newgreedy -f
```

---

### macOS

**1. Clone and run the installer**

```bash
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
chmod +x install-macos.sh
./install-macos.sh
```

The installer:
- Checks for `python3` (install with `brew install python@3.11` if missing)
- Installs Python dependencies
- Copies files to `~/Library/Application Support/NewGreedy`
- Generates the mitmproxy CA and adds it to the login keychain
- Registers a launchd agent that starts at login

**2. Verify**

Open `http://localhost:8080`. The proxy listens on `127.0.0.1:3456`.

**Manage the service:**
```bash
launchctl unload ~/Library/LaunchAgents/com.mrt0t0.newgreedy.plist   # stop
launchctl load   ~/Library/LaunchAgents/com.mrt0t0.newgreedy.plist   # start
tail -f "$HOME/Library/Application Support/NewGreedy/newgreedy.log"
```

Keep the cloned folder: updates are run from it — see [Updating](#updating).

**Uninstall:**
```bash
./install-macos.sh --uninstall
```

**Firefox** keeps its own certificate store — import manually:
`~/.mitmproxy/mitmproxy-ca-cert.pem`

---

### Windows

**1. Open PowerShell as Administrator**

**2. Run the installer**

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The installer:
- Checks for Python and downloads it automatically if missing (Python 3.11)
- Installs Python dependencies
- Copies files to `%LOCALAPPDATA%\NewGreedy\`
- Imports the mitmproxy CA certificate into the Windows user certificate store
- Registers a Scheduled Task that starts NewGreedy at logon

**3. Verify**

Open `http://localhost:8080` in your browser. The proxy listens on `127.0.0.1:3456`.

**4. Trust the CA in Firefox** (if used)

Firefox → Settings → Privacy & Security → Certificates → Import
File: `%USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.p12`

Keep the folder you installed from: updates are run from it — see [Updating](#updating).

**Uninstall:**
```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall
```

---

### Docker

**1. Create the required data files**

```bash
touch stats.json torrent_registry.json newgreedy.log
echo '{}' > control.json
```

**2. Start the stack**

```bash
docker compose up -d
```

**3. Check startup logs**

```bash
docker compose logs -f
```

**4. Export and trust the mitmproxy CA**

```bash
docker exec newgreedy cat /root/.mitmproxy/mitmproxy-ca-cert.pem > mitmproxy-ca.crt
```

Import `mitmproxy-ca.crt` into your OS or browser trust store, then restart your torrent client.

Updates are run from this folder — see [Updating](#updating).

---

### Manual (no installer)

```bash
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
pip install -r requirements.txt
python3 newgreedy.py
```

Updates: see [Updating](#updating).

---

## Updating

Every method keeps your data — `stats.json`, `torrent_registry.json`, `control.json`, `torrents/` — and **merges** `config.ini`: your values stay, and the settings a new version adds are inserted with their comments.

| Install | Command-line update | Web button |
|---|---|---|
| Linux | `git pull`, then `sudo ./install.sh --update` | ✅ restarts on its own |
| macOS | `git pull`, then `./install-macos.sh --update` | ✅ restarts on its own |
| Windows | `git pull`, then `.\install.ps1 -Update` | ✅ start it again if it does not come back |
| Docker | save `config.ini`, `git pull`, merge, rebuild | ⚠️ lasts until the container is rebuilt |
| Manual | save `config.ini`, `git pull`, merge, restart | ✅ start NewGreedy again afterwards |

**Coming from v1.7.6:** it has no update button — use the command line once. The button is available from v1.7.7 on.

### Linux

```bash
cd ~/NewGreedy                 # the folder you installed from
git pull                       # or extract the new release zip over it
sudo ./install.sh --update
```

The installer replaces the program files in `/opt/newgreedy`, merges `config.ini`, and restarts the service. Follow it with `journalctl -u newgreedy -f`.

v1.7.6 copied its `.git/` folder into `/opt/newgreedy`. It is not used; `sudo rm -rf /opt/newgreedy/.git` removes it.

### macOS

```bash
cd ~/NewGreedy
git pull
./install-macos.sh --update
```

No `sudo`. Files in `~/Library/Application Support/NewGreedy` are updated and the launchd agent is reloaded.

### Windows

In PowerShell, from the folder you installed from:

```powershell
git pull                       # or download and extract the new release zip
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Update
```

Files in `%LOCALAPPDATA%\NewGreedy` are updated and the scheduled task is restarted.

### Docker

`config.ini` is bind-mounted from the cloned folder, so it is also the file Git tracks: `git pull` refuses to run while it holds your settings. Set it aside, pull, merge it back, rebuild:

```bash
cd ~/NewGreedy
cp config.ini config.ini.mine
git checkout -- .
git pull
python3 config_merge.py --shipped config.ini --current config.ini.mine --out config.ini
[ -f control.json ] || echo '{}' > control.json
docker compose up -d --build
```

`git checkout -- .` discards changes to tracked files only — your settings are in `config.ini.mine`, and data files are not tracked. `control.json` is mounted since v1.7.7; if it is missing, Docker creates a directory in its place.

Without `python3` on the host, run the merge in a throwaway container:
`docker run --rm -v "$PWD:/w" -w /w python:3.11-slim python3 config_merge.py --shipped config.ini --current config.ini.mine --out config.ini`

### Manual

Stop NewGreedy (`Ctrl+C`), then:

```bash
cd ~/NewGreedy
cp config.ini config.ini.mine
git checkout -- .
git pull
pip install -r requirements.txt
python3 config_merge.py --shipped config.ini --current config.ini.mine --out config.ini
python3 newgreedy.py
```

### Web interface

When a newer release exists, the Help page shows **Update now**:

1. The release is downloaded from GitHub — the host is checked, and the SHA-256 too when the release notes publish one.
2. Program files are copied to `.backup/`, then replaced.
3. `config.ini` is merged; data files are not touched.
4. **Restart to apply** stops NewGreedy so the new code loads.

Any failure puts the previous files back; `POST /api/update/rollback` does the same by hand.

What happens after **Restart to apply** depends on the install:

| Install | After the restart | Keep in mind |
|---|---|---|
| Linux | systemd starts NewGreedy again | — |
| macOS | launchd starts NewGreedy again | — |
| Windows | NewGreedy stops | If it is not back within a minute: `Start-ScheduledTask -TaskName NewGreedy`, or log off and on |
| Docker | the container restarts (`restart: unless-stopped`) | The new files live inside the container: rebuilding from an older clone brings the old version back. Update the clone as above before the next rebuild |
| Manual | NewGreedy stops | Start it again with `python3 newgreedy.py`. The next `git pull` goes through the steps above |

### Options

- `--keep-scrape` (`-KeepScrape` on Windows) — `intercept_scrape` is the only value a merge changes on its own, from `true` to `false` (the v1.7.7 default). This keeps yours.
- `python3 config_merge.py --shipped config.ini --current <your config.ini>` without `--out` shows what would change and writes nothing.

---

## Configure your torrent client

### qBittorrent

Settings → Connection → Proxy Server:

| Field | Value |
|---|---|
| Type | HTTP |
| Host | `127.0.0.1` |
| Port | `3456` |
| Use proxy for tracker communication | ✅ enabled |
| Use proxy for peer connections | ❌ disabled |

### Deluge / Transmission / Other clients

Set HTTP proxy `127.0.0.1:3456` in the network/proxy settings.

---

## Configuration reference

| Platform | Config path |
|---|---|
| Linux / macOS | `/opt/newgreedy/config.ini` |
| Windows | `%LOCALAPPDATA%\NewGreedy\config.ini` |
| Docker | `./config.ini` (bind-mounted) |
| Manual | `config.ini` next to `newgreedy.py` |

Changes are applied live via **Reload config.ini** in the Config page, or `SIGHUP`. A full restart is only needed for `listen_port` and `web_port`.

---

### [proxy]

| Key | Default | Description |
|---|---|---|
| `listen_port` | `3456` | Port NewGreedy listens on. Must match the proxy port in your torrent client. |
| `tracker_timeout` | `5` | Reserved, not read. mitmproxy manages tracker connection timeouts itself. |

---

### [spoofing]

| Key | Default | Description |
|---|---|---|
| `upload_mode` | `ratio_based` | Engine mode. `ratio_based` is the only supported value. |
| `target_ratio` | `1.60` | UL/DL ratio to reach per torrent. |
| `target_ratio_buffer` | `0.03` | Internal overshoot buffer. Effective target = `target_ratio + buffer`. |
| `auto_stop_at_target` | `true` | Stop injecting once the target ratio is reached. |
| `catch_up_factor` | `0.22` | Aggressiveness of catch-up. Range: `0.05` (slow) – `0.50` (aggressive). |
| `max_simulated_speed_mbps` | `10.0` | Hard cap on simulated upload speed per announce interval. |
| `max_ratio_per_torrent` | `3.0` | Maximum ratio per torrent regardless of target. |
| `max_global_ratio_per_tracker` | `2.5` | Maximum aggregate UL/DL across all torrents on one tracker domain. Enforced before each announce, with overflow correction. |
| `upload_noise_pct` | `3.0` | Pareto-distributed noise percentage on each upload increment. |
| `stagnation_probability` | `0.03` | Per-announce probability of deliberately skipping injection. |
| `seed_credit_mb` | `5.0` | MB credited per announce for pure seeders. |
| `seed_target_mb` | `500.0` | Total upload target for pure seeders (progress bar reference). |
| `seeding_dl_ratio` | `0.85` | Reserved, not read. |
| `anti_clustering` | `true` | Legacy master switch for announce de-correlation. Superseded by `desync_announces`, which falls back to this value. |

---

### [anti_detection]

| Key | Default | Description |
|---|---|---|
| `user_agent_mode` | `random` | `random`: send the User-Agent paired with the session `peer_id`. `fixed`: force `user_agent_value`, which breaks that pairing and is logged as a warning at startup. |
| `user_agent_value` | `qBittorrent/5.1.0` | Static UA, used only when `user_agent_mode = fixed`. |
| `spoof_user_agent` | `true` | Send the emulated client's User-Agent. `false` leaves your own client's header untouched. |
| `spoof_peer_id` | `true` | Use one stable `peer_id` and `key` for the whole session, like a real client. |
| `spoof_peers` | `true` | Send the `numwant` value of the emulated client instead of the real one. |
| `peer_variance` | `0.15` | Reserved, not read. |
| `spoof_port` | `true` | Use one stable listening port for the whole session, taken from `port_range`. |
| `port_range` | `6881-6999` | Range for spoofed port selection. |
| `spoof_headers` | `true` | Inject realistic `Accept` and `Accept-Language` headers. |
| `intercept_scrape` | `false` | Silently drop scrape requests. A client that never scrapes is atypical. |
| `echo_trackerid` | `true` | Send back the `trackerid` a tracker hands out, as BEP 3 requires. |
| `tracker_whitelist` | _(empty)_ | Comma-separated domains to process. Empty = all. |
| `tracker_blacklist` | _(empty)_ | Comma-separated domains to skip. Takes precedence over whitelist. |
| `desync_announces` | `true` | Give every torrent its own announce period so torrents sharing a tracker never stay in lockstep. |
| `desync_spread_pct` | `0.18` | Maximum extra delay added to the tracker interval, as a fraction. Hard limit `0.45`. |
| `desync_min_gap` | `45` | Two announces to the same tracker closer than this count as a cluster and widen the spread. |
| `startup_burst` | `true` | Ramp the injected upload up over the first minutes instead of running at full speed from the first announce. |
| `startup_burst_window` | `300` | Length of that startup window, in seconds. |
| `startup_ramp_floor` | `0.15` | Upload multiplier at the very beginning of the window. |

The announce interval is only ever pushed **later**, never earlier, so NewGreedy can never
make your client announce more often than the tracker allows. A `min interval` larger than
`interval` is left untouched.

---

### [ssl]

| Key | Default | Description |
|---|---|---|
| `ssl_verify_trackers` | `true` | Reserved, not read. Certificate handling follows mitmproxy's own settings. |

---

### [stats]

| Key | Default | Description |
|---|---|---|
| `persist_stats` | `true` | Save stats to `stats.json`. Flushed at most once per 60 s; forced on stop. |
| `auto_purge_stopped` | `true` | Remove torrent from stats on `event=stopped`. |
| `stopped_purge_delay` | `120` | Seconds before a torrent that sent `event=stopped` is dropped. A client that comes back in time keeps its counters. `0` = immediate. |
| `tracker_cumul_ttl_days` | `30` | Days before an inactive tracker domain is dropped from the cumulative ratio. |

---

### [web]

| Key | Default | Description |
|---|---|---|
| `web_enabled` | `true` | Enable the FastAPI web UI and REST API. |
| `web_host` | `0.0.0.0` | Bind address for the web server. Use `127.0.0.1` to restrict to localhost. |
| `web_port` | `8080` | Port for the web UI. |
| `web_secret_token` | _(empty)_ | Token required to reach the UI/API. Pass it once as `?token=...`; it is then stored in a cookie. Empty disables authentication. |
| `web_path_prefix` | _(empty)_ | Serves the whole UI under `/<prefix>/`, hiding it from casual scans. Alphanumeric only. |
| `web_rate_limit` | `240` | Max requests per minute per client IP. `0` disables the limit. |

> **Exposing the UI beyond localhost?** Set both `web_path_prefix` and `web_secret_token`.
> With defaults, anyone who can reach the port can read stats and purge data.

---

### [advanced]

| Key | Default | Description |
|---|---|---|
| `inject_hours` | `0-23` | Active injection window in 24h format. Supports wrap-around (`22-6`). |
| `min_announce_interval` | `1800` | Minimum seconds between announces for the same torrent. |
| `interval_jitter_pct` | `0.08` | ±Jitter on announce interval as a fraction. |
| `stall_announce_threshold` | `8` | Consecutive zero-DL announces before `[STALL_NET]` is flagged. |
| `min_announces_before_stagnation` | `10` | Minimum announces before stagnation can trigger. |
| `log_level` | `INFO` | Verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `event_anomaly_probability` | `0.03` | Reserved, not read. A fake `event=started` contradicts a growing upload counter. |
| `corrupt_field_probability` | `0.05` | Probability of adding a `corrupt=` field. |
| `respect_tracker_interval` | `true` | Pace announces using the `interval` returned by the tracker when available. |
| `multi_tracker_delay_min` | `0.5` | Reserved, not read. |
| `multi_tracker_delay_max` | `8.0` | Reserved, not read. |
| `watch_folder` | `torrents` | Folder scanned every 15 s for new `.torrent` files. Empty disables auto-import. |

---

### [client]

| Key | Default | Description |
|---|---|---|
| `client_profile` | `random` | Emulated client: `qbittorrent`, `transmission`, `deluge`, `libtorrent`, `rtorrent` or `random`. Each profile pairs every peer_id prefix with the User-Agent of that exact version, and carries its own header set, default `numwant` and announce parameter order. |
| `rotate_profile_on_restart` | `true` | Legacy switch, read only when `session_identity` is empty (`true` = `rotate`, `false` = `stable`). |
| `session_identity` | `drift` | What survives a restart. `stable`: everything. `drift`: same client and port, fresh peer_id tail. `rotate`: a new client each time. |
| `version_bump_probability` | `0.10` | In `drift` mode, chance per start of moving up one release of the same client. |

---

### [bandwidth]

| Key | Default | Description |
|---|---|---|
| `global_max_speed_mbps` | `8.0` | Upload ceiling shared by every torrent, in MB/s. Models a single machine's uplink instead of letting each torrent inject independently. `0` disables. |
| `per_torrent_speed_cap_mbps` | `12.0` | Hard ceiling for the implicit speed of a single announce (`ΔUL / Δt`). |
| `max_download_speed_mbps` | `12.0` | Pace for the download NewGreedy reports beyond what your client reports, so `left` falls at a credible speed. `0` disables. |
| `circadian_smoothing` | `true` | Ramp injection up and down across `inject_hours` instead of switching on and off abruptly. |
| `circadian_night_factor` | `0.35` | Upload multiplier applied at the quietest point of the cycle. |

---

### [notifications]

| Key | Default | Description |
|---|---|---|
| `webhook_url` | _(empty)_ | Destination URL. Empty disables notifications. |
| `webhook_format` | `generic` | `generic` (plain JSON), `discord` or `slack`. |
| `webhook_events` | `target_reached,tracker_down,global_cap` | Subscribed events among `target_reached`, `tracker_down`, `tracker_up`, `global_cap`, `purged`. Identical alerts are de-duplicated within a 5-minute window. |

---

### [upstream]

| Key | Default | Description |
|---|---|---|
| `proxy_url` | _(empty)_ | Forward tracker announces through an external proxy, e.g. `http://gluetun:8888`. |
| `proxy_bypass` | _(empty)_ | Comma-separated domains that must not go through the upstream proxy. |

---

### [simulation]

| Key | Default | Description |
|---|---|---|
| `dry_run` | `false` | Compute and log everything without touching a single announce. Useful to validate a configuration before injecting for real. A banner appears on the dashboard while enabled. |

---

### Per-tracker overrides

Any `[tracker:domain]` section overrides the global settings for that domain only:

```ini
[tracker:private.example.org]
target_ratio                 = 1.05
max_global_ratio_per_tracker = 1.20
catch_up_factor              = 0.10

[tracker:open.example.com]
target_ratio                 = 2.50
```

Useful when a private tracker requires a modest ratio while another tolerates more.

---

## Web UI

| Page | URL | Description |
|---|---|---|
| Dashboard | `/` | Summary cards, tracker cap warning banner, auto-refresh every 15 s |
| Torrents | `/torrents` | Sortable, searchable table with per-torrent pause, ratio override and tags |
| Charts | `/charts` | Time-series per torrent, plus a compare mode overlaying several ratio curves |
| Config | `/config` | View and edit settings, client profiles, backup/restore, `.torrent` import |
| Logs | `/logs` | Live log stream, level filters, tracker ratio bar, GLOBAL_CAP counter |
| Help | `/help` | Log reference, API docs, qBittorrent setup, one-click update |

---

## REST API

| Endpoint | Method | Description |
|---|---|---|
| `/api/stats` | GET | All torrent stats |
| `/api/stats/csv` | GET | Export stats as CSV |
| `/api/stats/purge` | GET | Dry-run preview — count and hashes that would be purged |
| `/api/stats/purge` | DELETE | Purge torrents — params: `keep_active`, `inactive_hours` |
| `/api/stats/{ih}` | DELETE | Delete a single torrent by 8-char hash prefix |
| `/api/tracker_stats` | GET | Per-domain UL/DL cumul, ratio, and % of cap |
| `/api/udp_status` | GET | UDP trackers detected — these bypass NewGreedy |
| `/api/registry` | GET | Imported torrents: name, size, tracker list |
| `/api/config/editable` | GET | Settings that can be changed from the UI |
| `/api/config/set` | POST | Update one setting in `config.ini` |
| `/api/control` | GET | Paused torrents, ratio overrides and tags |
| `/api/control/pause` | POST | Pause or resume injection for one torrent |
| `/api/control/override` | POST | Force a target ratio or an upload total |
| `/api/control/tags` | POST | Assign tags to a torrent |
| `/api/profiles` | GET | Available emulated client profiles |
| `/api/backup` | GET | Download a zip with config, stats, registry and control file |
| `/api/restore` | POST | Restore a backup archive |
| `/api/update/status` | GET | Current version, update progress, pending restart |
| `/api/update/start` | POST | Download the latest release and replace program files |
| `/api/update/rollback` | POST | Restore the files saved before the last update |
| `/api/restart` | POST | Restart the service so new files take effect |
| `/metrics` | GET | Prometheus metrics |
| `/api/logs` | DELETE | Truncate `newgreedy.log` on the server |
| `/api/health` | GET | Stalled, anomalous, and target-reached torrents |
| `/api/history` | GET | Time-series snapshots for all torrents |
| `/api/history/{ih}` | GET | Time-series for one torrent |
| `/api/config` | GET | Current `config.ini` as JSON |
| `/api/config/reload` | POST | Reload `config.ini` without restarting |
| `/api/version` | GET | Current version vs latest GitHub release |
| `/api/upload` | POST | Upload a `.torrent` to pre-register infohash + size |
| `/ws/logs` | WebSocket | Real-time log stream |

**Purge parameters:**

| Parameter | Type | Default | Effect |
|---|---|---|---|
| `keep_active` | bool | `true` | Preserve torrents that announced recently |
| `inactive_hours` | int | `0` | Purge torrents with no announce in the last N hours. Entries without a timestamp are included. |

```
GET    /api/stats/purge?keep_active=true&inactive_hours=12   # preview
DELETE /api/stats/purge?keep_active=true&inactive_hours=12   # execute
DELETE /api/stats/a1b2c3d4                                   # single torrent
GET    /api/tracker_stats                                     # per-domain ratio
```

---

## Reading the logs

```
[DOWN] 99887766 | DL:  800.0M UL:  640.0M +  44.1M R:0.80 ETA:~12a #8
[DOWN] 99887766 | DL:  800.0M UL:  640.0M +   0.0M R:0.80 ETA:~12a #9 [STAG]
[SEED] abcdef12 | UL:  312.4M +  18.2M #23
[DOWN] 11223344 | DL: 1200.0M UL: 1960.0M +   0.1M R:1.63 ETA:~0a #41 [TARGET_REACHED]
[SEED] 44556677 | UL:   41.8M +   6.2M #2 [BURST]
[DOWN] 11223344 | DL:  600.0M UL:  480.0M +  12.7M R:0.80 ETA:~21a #7 [DESYNC]
[GLOBAL_CAP] tracker.opentrackr.org | tracker_ratio capped → UL adjusted to 1280.0M
[TRACKER_DOWN] tracker.openbittorrent.com — 3 consecutive errors, entering backoff
[TRACKER_UP] tracker.openbittorrent.com — recovered
```

| Field / Flag | Meaning |
|---|---|
| `[DOWN]` / `[SEED]` | Mode: downloading or pure seeder |
| `DL` | Cumulative downloaded reported by the client |
| `UL` | Cumulative injected upload sent to the tracker |
| `+N` | Upload delta this announce (0 during stagnation) |
| `R` | Current ratio — UL / DL |
| `ETA:~Na` | Estimated announces to reach `target_ratio` |
| `#N` | Total announce count for this torrent |
| `[STAG]` | Deliberate stagnation — delta set to 0 |
| `[STALL_NET]` | N consecutive zero-DL announces |
| `[STALL_ALGO]` | Engine stagnation active |
| `[TARGET_REACHED]` | Target ratio achieved — injection stopped |
| `[PURGED]` | Removed after `event=stopped` or manual purge |
| `[AUTO-PURGE]` | Removed automatically after 12 h of no announce |
| `[GLOBAL_CAP]` | Global tracker ratio guard triggered — UL reduced |
| `[TRACKER_DOWN]` | Tracker entering exponential backoff after repeated 5xx errors |
| `[TRACKER_UP]` | Tracker recovered — announces resume |
| `[UDP_BYPASS]` | UDP tracker detected — its announces are **not** intercepted |
| `[WATCH]` | Torrent auto-imported from the watch folder |
| `[CUMUL_PURGE]` | Inactive tracker domain dropped from the cumulative ratio |
| `[CUMUL_RESET]` | Per-tracker totals written before v1.7.7 were dropped at startup — they were over-counted and cannot be corrected after the fact |
| `[PURGE_SYNC]` | Torrent removed from memory after a purge from the web UI |
| `[PAUSED]` | Injection suspended for this torrent |
| `[DRY-RUN]` | Simulation mode — the announce was left untouched |
| `[BURST]` | Startup window — upload is still ramping up |
| `[DESYNC]` | Another torrent announced to the same tracker within `desync_min_gap`; the spread was widened |
| `[IDENTITY]` | Client profile rotated at startup |
| `[CONTROL]` | Pause, override or tag file reloaded |
| `[STOPPED]` | The client sent `event=stopped`; the torrent is retired once `stopped_purge_delay` has passed |
| `[RESUMED]` | It announced again before that delay — the purge was cancelled and the counters kept |
| `[DL_CAP]` | The reported download was held back to keep `left` falling at a plausible speed |
| `[TRACKERID]` | A tracker issued a `trackerid`; it is echoed from the next announce on (debug level) |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| New settings missing from the Config page | `config.ini` was kept as is by an older installer | Run `sudo ./install.sh --update` again from v1.7.7 |
| `git pull` refuses: *local changes to config.ini* | Docker or manual install: your settings live in the tracked file | See [Updating](#updating) — set it aside, pull, merge it back |
| `peer_id` changes at every restart | `session_identity = drift` redraws its random part, as a real client does | Expected. `stable` freezes it |
| `[CUMUL_RESET]` once after upgrading, tracker totals at zero | Totals from v1.7.6 were over-counted and are discarded | Expected. They rebuild from real figures |
| Ratio on tracker exceeds `max_global_ratio` | Pre-v1.7.5: cap was computed after accumulation | Upgrade to v1.7.5 — fixed by correct ordering |
| Ratio cap resets after restart | Pre-v1.7.5: `_tracker_cumul` not persisted | Upgrade to v1.7.5 — now saved in `stats.json` |
| `[STAG][STALL_ALGO][TARGET_REACHED]` together | Pre-v1.7.5: stagnation ran after target check | Upgrade to v1.7.5 — `_calc_upload` short-circuits on target |
| `[TRACKER_DOWN]` in logs, announces skipped | Tracker returning 5xx — backoff active | Wait for automatic recovery; check tracker status |
| Purge by inactivity does nothing | Entries have no timestamp | Use `GET /api/stats/purge?inactive_hours=12` to preview first |
| `mitmproxy crashed (password cannot be longer than 72 bytes)` | bcrypt 5.x is incompatible with the passlib version mitmproxy depends on | `pip install --break-system-packages "bcrypt<5"` — pinned in `requirements.txt` since v1.7.6 |
| `AttributeError: module 'bcrypt' has no attribute '__about__'` | Same bcrypt 5.x incompatibility (warning only, but precedes the crash) | Same fix as above |
| Service restarts in a loop after upgrade | The old systemd unit was not regenerated | `systemctl stop newgreedy && rm /etc/systemd/system/newgreedy.service && systemctl daemon-reload`, then run `install.sh` **without** `--update` |
| Ratio not rising on a tracker | The tracker is UDP — announces bypass the proxy | Check the dashboard banner / `GET /api/udp_status`. UDP interception is not supported |
| Identity changes after restart | Pre-v1.7.6: `peer_id`/`port` were not persisted | Upgrade to v1.7.6 — identity is now stored in `stats.json` |
| Web UI reachable by anyone | Default bind is `0.0.0.0` with no auth | Set `web_secret_token` and `web_path_prefix`, or bind to `127.0.0.1` |
| Stats lost after `docker stop` | Pre-v1.7.6: no SIGTERM handler | Upgrade to v1.7.6 — a final flush runs on shutdown |
| `.torrent` in watch folder ignored | Folder disabled or file too large | Check `watch_folder` and the 10 MB limit; see `[WATCH]` lines in the log |
| Ratio not increasing | 0 leechers (swarm-aware block) | Wait for leechers; check Charts for history |
| `[STALL_NET]` on all torrents | Client reports 0 download consistently | Normal for pure seeders — lower `stall_announce_threshold` |
| HTTPS announces not intercepted | mitmproxy CA not trusted | Re-run installer or import CA manually |
| Web UI not loading | Missing dependencies | `pip install fastapi uvicorn python-multipart` |
| Config changes not applied | Service not reloaded | Click **Reload config.ini** or `systemctl restart newgreedy` |

---

## Files

| File | Role |
|---|---|
| `newgreedy.py` | Launcher — watchdog, update check, web UI thread |
| `_version.py` | Shared constants: version check, bencode reader, webhook delivery |
| `client_profiles.py` | Emulated client definitions — peer_id, headers, parameter order |
| `updater.py` | Downloads a GitHub release, swaps program files, keeps a rollback copy |
| `config_merge.py` | Brings an existing `config.ini` up to the shipped one without losing a setting; used by every installer and by the one-click update |
| `control.json` | Paused torrents, ratio overrides and tags — written by the web UI |
| `newgreedy_addon.py` | mitmproxy addon — intercepts announces, ratio engine, stats, backoff |
| `newgreedy_web.py` | FastAPI app — web UI and REST API |
| `config.ini` | Main configuration file |
| `stats.json` | Persisted stats (schema v7) — includes `_tracker_cumul`, the session identity, reported progress and stored tracker ids |
| `torrent_registry.json` | Infohash, size, name and tracker list from `.torrent` imports |
| `torrents/` | Watch folder — drop `.torrent` files here for auto-import |
| `newgreedy.log` | Log file (streamed live in Logs page) |
| `static/` | Web UI assets |
| `install.sh` | Installer — Linux / macOS |
| `install.ps1` | Installer — Windows |
| `install-macos.sh` | Installer — macOS (launchd) |
| `uninstall.sh` | Uninstaller — Linux / macOS |
| `requirements.txt` | Python dependencies |
| `LICENSE` | GNU General Public License v3.0 |
| `.gitignore` | Excludes runtime data (`stats.json`, logs, `.torrent` files) from Git |
| `Dockerfile` | Docker image |
| `docker-compose.yml` | Docker Compose stack |

---

## License

NewGreedy is free software licensed under the **GNU General Public License v3.0**.

You may use, study, modify and redistribute it under the terms of the GPLv3. Any redistributed version — modified or not — must remain under the same license and ship its complete source code. See [LICENSE](LICENSE) for the full text.

```
NewGreedy — BitTorrent announce proxy
Copyright (C) 2026  Mrt0t0

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
```

---

## Changelog

### v1.7.7

| | v1.7.6 | v1.7.7 |
|---|---|---|
| Client identity | One peer_id, port and client per torrent | One identity for the whole session; 5 client profiles, peer_id always matching the User-Agent |
| Across restarts | Frozen for ever | `drift`: same client and port, fresh peer_id, occasional version bump |
| Announce timing | Independent jitter, torrents stay in lockstep | Per-torrent offset, torrents restarted together fan out |
| Startup | Full rate from the first announce | Upload ramps up over the first minutes |
| `downloaded` / `left` | `left` passed through untouched | `downloaded + left = size`, credible download speed |
| `numwant` / `event` | Randomised / fake `started` injected | Stable profile value / never altered |
| `trackerid` | Not returned | Returned, as BEP 3 requires |
| `event=stopped` | Forwarded unmodified, torrent dropped at once | Rewritten; removal deferred and batched |
| Scrapes | Dropped by default | Forwarded |
| Aggregate tracker cap | Download over-counted, cap fading over time | Counts real download |
| Bandwidth | Per torrent, on/off active hours | Shared uplink budget, smooth day/night curve |
| Control | — | Pause, ratio override and tags per torrent; `[tracker:domain]` overrides |
| Integrations | — | Webhooks (Discord, Slack, JSON), upstream proxy, `dry_run` |
| Backup | — | Backup / restore archive from the Config page |
| Update | `install.sh --update`, `config.ini` kept as is | Same command, `config.ini` merged; one-click update from the web UI |
| `config.ini` | 7 sections, 50 keys | 12 sections, 73 keys |
| Python files | 4 | 7 — `client_profiles.py`, `updater.py`, `config_merge.py` added |

- **Upgrading** — per-tracker totals written by v1.7.6 are discarded once (`[CUMUL_RESET]`), and `intercept_scrape` moves to `false`; everything else in `config.ini` is kept

### v1.7.6
- **Anti-detection** — persistent `peer_id`/`port`/`key`, tracker-provided announce interval, `key` spoofing
- **UDP awareness** — `udp://` trackers detected, logged as `[UDP_BYPASS]` and flagged in the UI (they bypass the proxy)
- **Security** — optional path prefix + secret token, per-IP rate limiting, secret redaction, 10 MB upload limit, filename sanitisation
- **Robustness** — atomic `stats.json` writes, log rotation (10 MB × 3), graceful SIGTERM/SIGINT flush, hourly memory cleanup, config validation, tracker cumul retention, history compression
- **Features** — watch folder auto-import, settings editor, `/metrics` for Prometheus, per-torrent tracker breakdown, torrent names in UI
- **Performance** — config parsed once with `__slots__`, unified read cache, cached GitHub version check, shared `_version.py` module
- **Fixes** — log stream survives truncation/rotation, rate limiting restricted to API routes, WebSocket back-off, versioned static assets, `bcrypt<5` pinned
- **Platforms** — dedicated macOS installer (launchd), config and stats preserved on update for all installers
- **Dependencies** — version bounds pinned in `requirements.txt`
- **License** — project relicensed under GNU GPL v3.0

### v1.7.5
- **Bug fixes** — global ratio cap no longer exceeded, `_tracker_cumul` persisted across restarts, `[TARGET_REACHED]` short-circuits stagnation logic, purge by inactivity fixed, CSV ratio calculation corrected
- **New endpoints** — `GET /api/tracker_stats`, `GET /api/stats/purge` dry-run, `DELETE /api/stats/{ih}`
- **Tracker backoff** — exponential backoff after repeated 5xx errors, `[TRACKER_DOWN]` / `[TRACKER_UP]` logs
- **Web UI** — dashboard tracker cap warning, Torrents "Last seen" column, Logs level filters, live tracker ratio bar
- **Performance** — timer-based stats flush (60 s), 2 s API cache, registry cached at startup
- **Code quality** — CSS/JS deduplicated into shared `style.css` + `app.js` (−62% HTML), FastAPI routing order fixed, race condition in save thread resolved, dead code removed

### v1.7.0
- Ratio history charts, `inject_hours` active window, `.torrent` file import
- Dark / light theme toggle, Real UL vs Injected UL columns
- Auto-purge after 12 h inactivity, full English web UI

### v1.6.5
- Fixed `STALL_ALGO` blocking at ~40%, fixed `STALL_NET` false positives on pure seeders
- Added swarm-aware injection, auto-stop at target ratio, Pareto noise, CSV export

### v1.5 – v1.6
- Web UI introduced (Dashboard, Torrents, Charts, Config, Logs)
- Smart stagnation, announce jitter, target ratio buffer, progress bars

### v1.3 – v1.4
- Ratio-based upload engine, `peer_id` rotation, stats persistence, seed credit
- Windows installer, Docker support, per-torrent and global ratio caps

### v1.0 – v1.2
- Initial HTTP/HTTPS proxy via mitmproxy
- Upload multiplier with noise and logistic progression


---

## Detailed changelog

### v1.7.6

**Anti-detection**
- `peer_id`, port and `key` kept per torrent across restarts, instead of a new identity at every start
- Announces paced by the interval the tracker returns (`respect_tracker_interval`)
- UDP trackers detected at import and flagged: their announces bypass the proxy and report real figures

**Security**
- Optional access token and hidden path for the web UI (`web_secret_token`, `web_path_prefix`)
- Per-IP rate limiting on the API (`web_rate_limit`); secrets masked in API responses
- 10 MB upload limit and sanitised file names for `.torrent` imports

**Robustness**
- Stats flushed on `SIGTERM`/`SIGINT`, so `systemctl stop` and `docker stop` lose nothing
- Atomic writes for `stats.json`, log rotation (10 MB × 3), hourly memory cleanup
- History compressed beyond 24 h; inactive trackers dropped after `tracker_cumul_ttl_days`
- Numeric settings validated at startup with an explicit warning

**Features**
- Watch folder: `.torrent` files dropped in `torrents/` are imported automatically
- Settings editor on the Config page, Prometheus `/metrics`, per-tracker breakdown per torrent
- Torrent names shown instead of hashes; macOS installer with a launchd agent

**Fixes**
- Live log stream survives log truncation and rotation; WebSocket reconnects with back-off
- Rate limiting no longer blocks pages and static files; GitHub version check cached for an hour

### v1.7.5

**Engine**
- Global ratio cap checked before the upload is added, so it can no longer be exceeded
- Per-tracker totals persisted across restarts
- Target reached stops injection before any stagnation logic runs
- Exponential backoff after repeated tracker 5xx errors (`[TRACKER_DOWN]` / `[TRACKER_UP]`)
- Stats flushed at most once a minute instead of on every announce

**API**
- `GET /api/tracker_stats`, `GET /api/stats/purge` (dry-run preview), `DELETE /api/stats/{ih}`
- Purge by inactivity and CSV ratio fixed; `purge` route reachable again

**Web UI**
- Warning banner when a tracker passes 80 % of its cap
- *Last seen* column, log level filters, live tracker ratio bar
- Shared `style.css` and `app.js`: pages 62 % lighter

---

<div align="center">

**NewGreedy** — [Report an issue](https://github.com/Mrt0t0/NewGreedy/issues) · [Releases](https://github.com/Mrt0t0/NewGreedy/releases)

</div>
