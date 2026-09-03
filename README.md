<div align="center">

# NewGreedy

### BitTorrent announce proxy — Upload ratio spoofer

[![Version](https://img.shields.io/badge/version-1.7.6-blue?style=flat-square)](https://github.com/Mrt0t0/NewGreedy/releases)
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
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation) — [Linux](#linux) · [macOS](#macos) · [Windows](#windows) · [Docker](#docker)
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
Every time your client sends an announce, NewGreedy intercepts it and rewrites the `uploaded`, `downloaded`, `peer_id`, and `port` fields before forwarding the request.

Your torrent client and the files on disk are **never touched**. Only the statistics reported to trackers change.

---

## How it works

```
Torrent client  ──► NewGreedy :3456 (proxy)  ──► Tracker
                         │
                         ├─ Intercepts announces (HTTP + HTTPS via mitmproxy)
                         ├─ Rewrites: uploaded / downloaded / peer_id / port / numwant
                         ├─ Applies ratio engine (non-linear progression, noise, stagnation)
                         ├─ Guards per-tracker global ratio cap
                         └─ Forwards patched request to tracker
```

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

**Update an existing install:**
```bash
sudo ./install.sh --update
```

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

**Update / uninstall:**
```bash
./install-macos.sh --update
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

**Update:**
```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Update
```

**Uninstall:**
```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall
```

---

### Docker

**1. Create the required data files**

```bash
touch stats.json torrent_registry.json newgreedy.log
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

**Update:**
```bash
docker compose pull
docker compose up -d --build
```

---

### Manual (no installer)

```bash
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
pip install -r requirements.txt
python3 newgreedy.py
```

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
| `tracker_timeout` | `5` | Seconds before a tracker request times out. |

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
| `anti_clustering` | `true` | Jitter on announce timing. |

---

### [anti_detection]

| Key | Default | Description |
|---|---|---|
| `user_agent_mode` | `random` | `random`: rotate UA to match peer_id client. `fixed`: always use `user_agent_value`. |
| `user_agent_value` | `qBittorrent/4.6.8` | Static UA, used when `user_agent_mode = fixed`. |
| `spoof_user_agent` | `true` | Replace `User-Agent` header to match peer_id client. |
| `spoof_peer_id` | `true` | Assign a stable randomised `peer_id` per torrent per session. |
| `spoof_peers` | `true` | Randomise `numwant` ±`peer_variance`%. |
| `peer_variance` | `0.15` | Variance factor for `numwant` randomisation. |
| `spoof_port` | `true` | Assign a stable random port per torrent from `port_range`. |
| `port_range` | `6881-6999` | Range for spoofed port selection. |
| `spoof_headers` | `true` | Inject realistic `Accept` and `Accept-Language` headers. |
| `intercept_scrape` | `true` | Silently drop scrape requests. |
| `tracker_whitelist` | _(empty)_ | Comma-separated domains to process. Empty = all. |
| `tracker_blacklist` | _(empty)_ | Comma-separated domains to skip. Takes precedence over whitelist. |

---

### [ssl]

| Key | Default | Description |
|---|---|---|
| `ssl_verify_trackers` | `true` | Verify tracker SSL certificates. |

---

### [stats]

| Key | Default | Description |
|---|---|---|
| `persist_stats` | `true` | Save stats to `stats.json`. Flushed at most once per 60 s; forced on stop. |
| `auto_purge_stopped` | `true` | Remove torrent from stats on `event=stopped`. |
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
| `event_anomaly_probability` | `0.03` | Probability of injecting a fake `event=started`. |
| `corrupt_field_probability` | `0.05` | Probability of adding a `corrupt=` field. |
| `respect_tracker_interval` | `true` | Pace announces using the `interval` returned by the tracker when available. |
| `watch_folder` | `torrents` | Folder scanned every 15 s for new `.torrent` files. Empty disables auto-import. |

---

## Web UI

| Page | URL | Description |
|---|---|---|
| Dashboard | `/` | Summary cards, tracker cap warning banner, auto-refresh every 15 s |
| Torrents | `/torrents` | Full table — name, Real DL/UL, Injected UL, Ratio, ETA, Trackers, Last seen, purge |
| Charts | `/charts` | Time-series UL and ratio per torrent |
| Config | `/config` | View settings, edit an allow-listed setting, `.torrent` import |
| Logs | `/logs` | Live log stream, level filters, tracker ratio bar, GLOBAL_CAP counter |
| Help | `/help` | Log field reference, API docs, qBittorrent setup, update check |

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
| `[PURGE_SYNC]` | Torrent removed from memory after a purge from the web UI |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
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
| `_version.py` | Shared version constants and GitHub release check |
| `newgreedy_addon.py` | mitmproxy addon — intercepts announces, ratio engine, stats, backoff |
| `newgreedy_web.py` | FastAPI app — web UI and REST API |
| `config.ini` | Main configuration file |
| `stats.json` | Persisted stats (schema v5) — includes `_tracker_cumul` and per-torrent identity |
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
| `tests/` | pytest suite (ratio engine, caps, routing, sanitisation) |
| `.github/workflows/` | GitHub Actions CI (lint, test, Docker build) |

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

### v1.7.6
- **Anti-detection** — persistent `peer_id`/`port`/`key`, tracker-provided announce interval, `key` spoofing
- **UDP awareness** — `udp://` trackers detected, logged as `[UDP_BYPASS]` and flagged in the UI (they bypass the proxy)
- **Security** — optional path prefix + secret token, per-IP rate limiting, secret redaction, 10 MB upload limit, filename sanitisation
- **Robustness** — atomic `stats.json` writes, log rotation (10 MB × 3), graceful SIGTERM/SIGINT flush, hourly memory cleanup, config validation, tracker cumul retention, history compression
- **Features** — watch folder auto-import, settings editor, `/metrics` for Prometheus, per-torrent tracker breakdown, torrent names in UI
- **Performance** — config parsed once with `__slots__`, unified read cache, cached GitHub version check, shared `_version.py` module
- **Fixes** — log stream survives truncation/rotation, rate limiting restricted to API routes, WebSocket back-off, versioned static assets, `bcrypt<5` pinned
- **Platforms** — dedicated macOS installer (launchd), config and stats preserved on update for all installers
- **Verification** — pytest suite (18 tests), GitHub Actions CI (lint + test + Docker smoke test), pinned dependencies
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

#### Anti-detection

**Stable identity across restarts**
`peer_id`, `port` and the announce `key` are now generated once per torrent and persisted in `stats.json` (schema v5). Before this, every restart made each torrent announce with a brand-new identity to the same tracker — an easily detectable pattern.

**`key` parameter spoofing**
The BitTorrent `key` field is now set to a stable per-torrent value instead of being passed through untouched.

**Tracker-provided announce interval**
The `interval` returned by the tracker is parsed and stored per torrent/domain, then used to pace announces instead of always applying `min_announce_interval`. Disable with `respect_tracker_interval = false`.

**UDP tracker detection**
mitmproxy cannot intercept UDP announces, so those bypass NewGreedy entirely and report *real* statistics. Imported torrents are now scanned for `udp://` trackers; affected domains are logged as `[UDP_BYPASS]` at startup and a red banner appears on the dashboard. Exposed through `GET /api/udp_status`.

#### Security

**Web UI authentication**
Two optional layers, modelled on Joal: `web_path_prefix` serves the whole UI under an obfuscated path, and `web_secret_token` requires a token (query parameter, `X-Auth-Token` header or cookie). Both empty by default, which preserves the previous behaviour.

**API rate limiting**
`web_rate_limit` caps requests per minute per client IP (default 240, `0` disables). Excess requests get HTTP 429.

**Secret redaction**
`GET /api/config` masks `web_secret_token`, and `GET /api/stats` no longer exposes `peer_id` or `key`.

#### Robustness

**Graceful shutdown**
`SIGTERM` and `SIGINT` now trigger a final stats flush before exit, so `systemctl stop` and `docker stop` no longer lose up to 60 s of data.

**Tracker cumul retention**
Tracker domains with no announce for `tracker_cumul_ttl_days` (default 30) are dropped from `_tracker_cumul` instead of accumulating forever.

**History compression**
Per-torrent history keeps full resolution for the last 24 h and one point per hour beyond that, capped at 200 points, keeping `stats.json` small on long-running instances.

**Unambiguous internal keys**
Internal maps now use an explicit `hash|domain` separator instead of raw concatenation, removing a prefix-collision risk during cleanup.

#### Features

**Watch folder**
Drop `.torrent` files into the folder set by `watch_folder` (default `torrents/`) and they are imported automatically within ~15 s — no UI interaction needed.

**Settings editor in the UI**
The Config page can now edit a curated allow-list of settings and write them straight to `config.ini` via `POST /api/config/set`. Ports and secrets are deliberately excluded.

**Prometheus metrics**
`GET /metrics` exposes torrent count, upload/download bytes, per-torrent ratio, announce counts, per-tracker ratio, stalled and target-reached totals.

**Per-torrent tracker breakdown**
Upload and download are now tracked per tracker domain within each torrent, shown in a new *Trackers* column and exposed in `GET /api/stats`.

**Torrent names everywhere**
The Torrents table and Charts selector show the imported torrent name instead of the raw hash, with the hash kept as a tooltip.

**macOS installer**
`install-macos.sh` installs to `~/Library/Application Support/NewGreedy`, registers a launchd agent and adds the mitmproxy CA to the login keychain.

#### Fixes

**Live log stream no longer freezes**
The `/ws/logs` reader now detects when `newgreedy.log` shrinks — after a manual truncation or an automatic rotation — and resumes from the start of the new file instead of seeking past its end and going silent forever.

**Rate limiting no longer locks the UI out**
`web_rate_limit` now applies only to `/api/` and `/metrics`. Pages and static assets are never counted, so a burst of reloads can no longer make `app.js` unreachable and break the whole interface. Idle IP buckets are also pruned.

**Log file writing survives mitmproxy's logging reset**
The rotating file handler is now attached to the `NewGreedy` logger itself and re-checked on every stats flush, instead of relying on the root logger that mitmproxy reconfigures at startup. Previously `newgreedy.log` could silently stop being written, leaving the Logs page showing days-old entries.

**Tracker ratio bar no longer floods the API**
The bar was refreshed on *every* incoming log line — 500 requests in a burst when the WebSocket connected, which tripped the rate limiter and rendered the `429` body as tracker data (`error`). It now refreshes on a 20 s timer, and ignores malformed responses.

**GitHub version check cached**
`/api/version` now caches the GitHub response for one hour (five minutes on failure). Previously every page load hit the GitHub API, and the unauthenticated limit of 60 requests/hour per IP was reached quickly, leaving the Help page showing an empty *Latest on GitHub*. Failures now report a readable reason (rate limit, no network) instead of a silent dash.

**passlib noise silenced**
The harmless `AttributeError: module 'bcrypt' has no attribute '__about__'` traceback printed by passlib on startup is suppressed.

**Server-side log purge**
The Logs page gains a *Purge file* button (`DELETE /api/logs`) that truncates `newgreedy.log` on the server, next to *Clear view* which only clears the browser display.

**WebSocket reconnection back-off**
The Logs page now retries with an increasing delay (3 s → 30 s) instead of hammering the server every 3 seconds while it is unreachable.

#### Also included in this release


#### Robustness

**Atomic writes for `stats.json`**
Stats are now written to a temporary file and moved into place with `os.replace()` after `fsync`. A crash mid-write can no longer leave a truncated or corrupted `stats.json` — the previous file stays intact until the new one is fully flushed.

**Log rotation**
`newgreedy.log` now uses `RotatingFileHandler` (10 MB per file, 3 backups). On a 24/7 instance the log can no longer grow without bound and fill the disk.

**Memory cleanup**
The in-memory dicts `_last_seen`, `_swarm_leechers`, `_ports`, `_peer_ids` and `_uas` are now pruned hourly of entries older than 24 h or belonging to removed torrents, preventing a slow memory leak on long-running instances.

**Config validation at startup**
Numeric config keys are validated on load. An invalid value (e.g. `target_ratio = abc`) now logs an explicit warning instead of silently falling back.

#### Security

**Upload size limit**
`/api/upload` now rejects files larger than 10 MB with HTTP 413, preventing a multi-GB `.torrent` from exhausting memory.

**Filename sanitisation**
Uploaded filenames are stripped of path separators, control characters and truncated to 255 chars before being stored in the registry.

#### Performance & code cleanup

**Config parsed once**
`TorrentStats` now receives a pre-parsed parameter tuple and uses `__slots__`, instead of re-parsing the full config for every torrent on load.

**Unified read cache**
The purge preview endpoint now reads through the shared 2 s cache instead of hitting the disk directly.

**Shared version module**
Version parsing and the GitHub release check are consolidated into `_version.py`, removing duplication between the launcher and the web app. `import os` and `import configparser` dead imports removed.

#### Verification

**Test suite**
A `tests/` folder with 10 pytest cases covers the ratio engine, per-torrent and global caps, target-reached short-circuit, atomic writes, FastAPI routing order, filename sanitisation and `inject_hours` parsing.

**CI pipeline**
`.github/workflows/ci.yml` runs ruff lint, the pytest suite, and a Docker build + `/api/health` smoke test on every push and pull request.

**Pinned dependencies**
`requirements.txt` now uses version bounds (e.g. `mitmproxy>=10.0,<12.0`) to guard against breaking releases.

---

---

### v1.7.5

#### Front-end refactor (CSS / JS deduplication)

**Single shared stylesheet**
The 96-line CSS block that was duplicated inline in all six HTML pages is now served once from `static/style.css` via `<link rel="stylesheet" href="/static/style.css">`. The CSS is byte-for-byte identical to the previous inline block — the UI appearance is unchanged.

**Single shared script**
The common front-end helpers (`fB`, `fR`, `fE`, `fAge`, `getMode`, `stag`, `modeTxt`, `rbar`, `ratioNum`, `initUI`) that were copy-pasted into every page are now defined once in `static/app.js` and loaded via `<script src="/static/app.js">`. Each page keeps only its page-specific logic.

**Result:** total HTML payload reduced from ~91 KB to ~34 KB (−62%). Editing a shared style or helper now touches one file instead of six.

#### Robustness fixes (code review pass)

**FastAPI routing order — `purge` vs `{ih}`**
`DELETE /api/stats/purge` is now declared before `DELETE /api/stats/{ih}`. Previously the dynamic route captured `purge` as a hash and returned `not found`, making the purge endpoint unreachable.

**Race condition in `_save_stats`**
The flush thread now iterates over `dict(self._stats)` and `dict(self._tracker_cumul)` snapshots instead of the live dicts, preventing `RuntimeError: dictionary changed size during iteration` when an announce arrives mid-flush.

**Anchored hash regex**
`_load_stats` validation regex changed from `[0-9a-f]{6,40}` to `^[0-9a-f]{6,40}$` so partial or malformed keys can no longer match.

**Unified stats cache**
`_load_stats` and `_load_tracker_cumul` now share a single cached file read (`_read_stats_file`, 2 s TTL). `/api/tracker_stats` no longer triggers a second disk read per poll.

**Registry cached at startup**
`torrent_registry.json` is loaded once into `self._registry` instead of being re-read on every new torrent in the announce hot path.

**`inject_hours` validation**
Out-of-range or malformed `inject_hours` values now log a warning and fall back to `0-23` instead of silently disabling injection all day.

**`_tail_log` no longer truncates mid-line**
Reads `max(32768, n * 256)` bytes and drops the first partial line, so long debug lines no longer corrupt the tail output.

**Config consistency**
`corrupt_field_probability` fallback aligned to `0.05` (matching `config.ini`); previously the code fallback was `0.20`.

**Dead code removed**
Unused `import os` and the unused `ws_clients` list removed from `newgreedy_web.py`.

#### Engine fixes (`newgreedy_addon.py`)

**Global ratio cap — correct ordering fix**
The cap guard now checks `(tc["ul"] + delta_ul) / tc["dl"]` before accumulating, then recomputes the allowed delta and updates `tc["ul"]` with the corrected value. Previously, `delta_ul` was added unconditionally first, so the cap was detected one announce too late and the tracker cumul remained inflated.

**`_tracker_cumul` persistence across restarts**
The per-domain UL/DL counters are now saved to `stats.json` under the key `_tracker_cumul` and reloaded on startup (schema v4). Before this fix, the global ratio guard was blind to all announces that happened before the last restart.

**`[TARGET_REACHED]` short-circuit in `_calc_upload`**
Once the target ratio is reached, `_calc_upload` now returns immediately before any stagnation logic runs. This eliminates the spurious `[STAG][STALL_ALGO][TARGET_REACHED]` flag combinations visible in logs.

**Timer-based `_save_stats` flush**
Stats are now flushed at most once every 60 seconds (configurable via `_flush_interval`). A `threading.Lock` protects the write. Forced flushes still happen on `event=stopped` and `done()`. Under 20 active torrents at 250 announces/hour, this reduces disk writes from ~50/hour to ~1/hour.

**Tracker backoff with exponential delay**
After `TRACKER_BACKOFF_THRESHOLD` (3) consecutive HTTP 5xx errors from a tracker domain, NewGreedy stops forwarding announces to that domain and enters exponential backoff (300s × 2^N, capped at 1800s). Recovery is automatic on first successful response. Logs `[TRACKER_DOWN]` on entry and `[TRACKER_UP]` on recovery.

**`[GLOBAL_CAP]` log line**
When the global ratio guard triggers, a `[GLOBAL_CAP] domain | tracker_ratio capped → UL adjusted to X.XM` line is emitted for diagnostics.

#### API fixes and additions (`newgreedy_web.py`)

**Purge by inactivity** — removed the erroneous `last_announce_ts > 0` check: entries with no timestamp are now correctly included when `inactive_hours` is set.

**CSV ratio** — fixed double division by 1e6 (ratio is now `ul / dl`, both already in MB).

**`/api/config` target_ratio** — dashboard and torrents pages now read `c.spoofing.target_ratio` correctly from the nested config structure.

**Purge `keep_active=false`** — added `isinstance(v, dict)` guard before deletion.

**`DELETE /api/stats/{ih}`** — delete a single torrent entry by 8-char hash prefix.

**`GET /api/stats/purge`** — dry-run preview returning count and hash list without modifying data.

**`GET /api/tracker_stats`** — new endpoint exposing per-domain UL/DL cumul, ratio, and percentage of the configured cap.

**`/api/logs` bounds** — `lines` parameter validated `ge=10, le=2000` via FastAPI `Query`.

**Stats cache** — `_load_stats()` now caches results for 2 seconds (TTL) to avoid repeated disk reads under concurrent API polling. Cache is invalidated on purge and single-delete operations.

#### Web UI additions

**Dashboard** — warning banner appears automatically when any tracker domain exceeds 80% of `max_global_ratio_per_tracker`. Fetches `/api/tracker_stats` on each refresh cycle.

**Torrents** — added `Last seen` column showing time since last announce in human-readable format (`Xs ago`, `Xm ago`, `Xh ago`, `Xd ago`).

**Logs** — three level-filter buttons (`All` / `Warn` / `Error`) narrow the log view without clearing history. Log analysis section gains two new counters: `GLOBAL_CAP` events and `TRACKER_DOWN` events. A live tracker ratio bar fetches `/api/tracker_stats` and renders a progress bar per domain coloured green / amber / red based on cap proximity.

---

<div align="center">

**NewGreedy** — [Report an issue](https://github.com/Mrt0t0/NewGreedy/issues) · [Releases](https://github.com/Mrt0t0/NewGreedy/releases)

</div>
