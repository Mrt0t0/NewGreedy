<div align="center">

# NewGreedy

### BitTorrent announce proxy — Upload ratio spoofer

[![Version](https://img.shields.io/badge/version-1.7.0-blue?style=flat-square)](https://github.com/Mrt0t0/NewGreedy/releases)
[![Python](https://img.shields.io/badge/python-3.9%2B-green?style=flat-square)](https://python.org)
[![License: GPL v3](https://img.shields.io/badge/license-GPL%20v3-blue?style=flat-square)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows%20%7C%20Docker-lightgray?style=flat-square)](https://github.com/Mrt0t0/NewGreedy)

</div>

---

> **Disclaimer** — NewGreedy is provided for **educational and research purposes only**.
> Using this tool on private trackers may violate their terms of service and result in a permanent ban.
> The author assumes no responsibility for any misuse. **Use at your own risk.**

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
                         └─ Forwards patched request to tracker
```

**Upload calculation per torrent:**
```
target_ul  = cumul_downloaded × target_ratio
remaining  = target_ul − cumul_reported_ul
increment  = remaining × catch_up_factor × e^(−decay) × Pareto_noise
             capped at max_simulated_speed_mbps × interval
             capped at max_ratio_per_torrent × cumul_downloaded
             capped by global tracker ratio guard
             ──► cumul_rep_ul sent to tracker as `uploaded`
```

Injection stops automatically when `target_ratio` is reached (`auto_stop_at_target = true`).

---

## v1.7.0 — Changes from v1.6.5

**New:**
- Ratio history recorded every 5 announces → time-series charts in the web UI
- Hourly injection scheduler (`inject_hours`) — restrict active window, e.g. `8-22`
- `.torrent` file import to pre-register infohash + file size before first announce
- Real UL vs Injected UL shown as separate columns in the Torrents table
- Auto-purge of torrents inactive for 12 h (no announce received)
- Dark / light theme toggle in the navbar (preference saved in browser)

**Fixed:**
- `configparser` now correctly parses inline comments in `config.ini` — prevented startup
- Pure seeder `downloaded` field now sends a stable hash-derived value instead of `0`
- Mode SEED/DOWN written explicitly to `stats.json` (was a JS heuristic, often wrong)
- Manual purge now also removes untracked legacy entries (pre-v1.7 stats)

---

## Requirements

| Dependency | Version |
|---|---|
| Python | 3.9+ (3.11 recommended) |
| mitmproxy | 10.0+ |
| fastapi | 0.110+ |
| uvicorn | 0.27+ |
| python-multipart | 0.0.9+ |

---

## Installation

### Linux / macOS

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
# open the web UI
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
journalctl -u newgreedy -f       # live logs
```

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

Files: `%LOCALAPPDATA%\NewGreedy\`  
Config: `%LOCALAPPDATA%\NewGreedy\config.ini`

---

### Docker

**1. Create the required data files**

Docker volumes fail silently if the host file doesn't exist. Create them first:

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

Data files (`config.ini`, `stats.json`, `static/`) are bind-mounted and survive container restarts.

---

### Manual (no installer)

```bash
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
pip install -r requirements.txt
python3 newgreedy.py
```

To generate and trust the CA manually:
```bash
python3 -c "
import os
from mitmproxy.certs import CertStore
CertStore.from_store(os.path.expanduser('~/.mitmproxy'), 'mitmproxy')
"
```
Then import `~/.mitmproxy/mitmproxy-ca-cert.pem` into your system or browser trust store.

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

Only enable the proxy for **tracker communication**. Enabling it for peer connections routes actual transfer traffic through NewGreedy unnecessarily.

### Deluge / Transmission / Other clients

Set HTTP proxy `127.0.0.1:3456` in the network/proxy settings. The option is usually labelled "Tracker proxy" or "Announce proxy".

---

## Configuration reference

Config file location:

| Platform | Path |
|---|---|
| Linux / macOS | `/opt/newgreedy/config.ini` |
| Windows | `%LOCALAPPDATA%\NewGreedy\config.ini` |
| Docker | `./config.ini` (bind-mounted) |
| Manual | `config.ini` next to `newgreedy.py` |

Changes are applied live via the **Reload** button in the web UI Config page, or by sending `SIGHUP` to the process. A full restart is only needed for `listen_port` and `web_port`.

---

### [proxy]

| Key | Default | Description |
|---|---|---|
| `listen_port` | `3456` | Port NewGreedy listens on. Must match the proxy port set in your torrent client. |
| `tracker_timeout` | `5` | Seconds before a tracker request times out. |

---

### [spoofing]

| Key | Default | Description |
|---|---|---|
| `upload_mode` | `ratio_based` | Engine mode. `ratio_based` is the only supported value. |
| `target_ratio` | `1.60` | The UL/DL ratio to reach per torrent. The engine ramps up injection until this is met. |
| `target_ratio_buffer` | `0.03` | Internal overshoot buffer. Effective target = `target_ratio + buffer` (e.g. `1.63`). Ensures the ratio never dips below the nominal target due to noise. |
| `auto_stop_at_target` | `true` | Stop injecting extra upload once the target ratio is reached. Only real upload is reported after that. |
| `catch_up_factor` | `0.22` | Aggressiveness of catch-up toward the target. Higher = faster ratio growth, less natural profile. Range: `0.05` (slow) – `0.50` (aggressive). |
| `max_simulated_speed_mbps` | `10.0` | Hard cap on simulated upload speed in MB/s per announce interval. Prevents unrealistically large jumps. |
| `max_ratio_per_torrent` | `3.0` | Maximum ratio for a single torrent, regardless of the target. |
| `max_global_ratio_per_tracker` | `2.5` | Maximum aggregate UL/DL ratio across all torrents on one tracker domain. Prevents the global ratio from looking abnormal. |
| `upload_noise_pct` | `3.0` | Percentage of Pareto-distributed noise applied to each upload increment. Makes reported speed look less uniform. |
| `stagnation_probability` | `0.03` | Probability per announce that the engine deliberately skips injection (reports 0 delta). Mimics natural upload pauses. Disabled for the first `min_announces_before_stagnation` announces and when ratio progress is below 65%. |
| `seed_credit_mb` | `5.0` | MB credited per announce for pure seeders (torrents with no download history). |
| `seed_target_mb` | `500.0` | Total upload target for pure seeders, used to drive the progress bar in the web UI. |
| `anti_clustering` | `true` | Adds jitter to announce timing to avoid perfectly regular intervals. |

---

### [anti_detection]

| Key | Default | Description |
|---|---|---|
| `user_agent_mode` | `random` | `random`: rotate User-Agent to match the spoofed `peer_id` client. `fixed`: always use `user_agent_value`. |
| `user_agent_value` | `qBittorrent/4.6.8` | Static User-Agent string, used only when `user_agent_mode = fixed`. |
| `spoof_user_agent` | `true` | Replace the HTTP `User-Agent` header to match the peer_id client. |
| `spoof_peer_id` | `true` | Assign a stable but randomised `peer_id` per torrent per session. Rotates across qBittorrent, Transmission, Deluge, libtorrent. |
| `spoof_peers` | `true` | Randomise the `numwant` field ±`peer_variance`% to avoid a fixed request pattern. |
| `peer_variance` | `0.15` | Variance factor for `numwant` randomisation (15% = ±15% of the original value). |
| `spoof_port` | `true` | Assign a stable random port per torrent, drawn from `port_range`. |
| `port_range` | `6881-6999` | Range used for spoofed port selection. |
| `spoof_headers` | `true` | Inject realistic `Accept` and `Accept-Language` HTTP headers. |
| `intercept_scrape` | `true` | Silently drop scrape requests. Scrape responses can reveal real stats and contradict injected announces. |
| `tracker_whitelist` | _(empty)_ | Comma-separated tracker domains to process. All others are passed through unchanged. Empty = process all. |
| `tracker_blacklist` | _(empty)_ | Comma-separated tracker domains to skip entirely. Takes precedence over the whitelist. |

---

### [ssl]

| Key | Default | Description |
|---|---|---|
| `ssl_verify_trackers` | `true` | Verify tracker SSL certificates. Set to `false` only for trackers with self-signed certificates. |

---

### [stats]

| Key | Default | Description |
|---|---|---|
| `persist_stats` | `true` | Save torrent stats to `stats.json` every 5 announces. Stats survive restarts. Set to `false` to run in memory-only mode. |
| `auto_purge_stopped` | `true` | When a torrent client sends `event=stopped`, immediately remove that torrent from in-memory stats and from `stats.json`. |

---

### [web]

| Key | Default | Description |
|---|---|---|
| `web_enabled` | `true` | Enable the FastAPI web UI and REST API. Set to `false` to run in headless mode. |
| `web_host` | `0.0.0.0` | Address the web server binds to. Use `127.0.0.1` to restrict access to localhost only. |
| `web_port` | `8080` | Port for the web UI. |

---

### [advanced]

| Key | Default | Description |
|---|---|---|
| `inject_hours` | `0-23` | Active injection window in 24h format (`HH-HH`). Outside this window, the engine forces stagnation. Examples: `8-22` = daytime only, `0-23` = always active. Supports midnight wrap-around (`22-6`). |
| `min_announce_interval` | `1800` | Minimum seconds enforced between announces for the same torrent. Prevents over-announcing by aggressive clients. |
| `interval_jitter_pct` | `0.08` | ±Jitter applied to the announce interval as a fraction. `0.08` = ±8%, preventing perfectly regular timing. |
| `stall_announce_threshold` | `8` | Consecutive announces with `downloaded = 0` before `[STALL_NET]` is flagged. |
| `min_announces_before_stagnation` | `10` | Minimum announce count before stagnation can trigger. Prevents early stagnation on new torrents. |
| `log_level` | `INFO` | Logging verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `event_anomaly_probability` | `0.03` | Probability of injecting a fake `event=started` on a regular announce. |
| `corrupt_field_probability` | `0.05` | Probability of adding a `corrupt=` field to mimic real client behaviour. |

---

## Web UI

Open **[http://localhost:8080](http://localhost:8080)** after starting NewGreedy.

| Page | URL | Description |
|---|---|---|
| Dashboard | `/` | Summary cards, filter by mode, auto-refresh every 15 s |
| Torrents | `/torrents` | Full table — Real DL, Real UL, Injected UL, Ratio, ETA, CSV export, purge |
| Charts | `/charts` | Time-series UL and ratio per torrent (requires at least 5 announces) |
| Config | `/config` | Current `config.ini` values + `.torrent` import |
| Logs | `/logs` | Live log stream via WebSocket, colour-coded |
| Help | `/help` | Log field reference, API docs, qBittorrent setup, update check |

---

## REST API

| Endpoint | Method | Description |
|---|---|---|
| `/api/stats` | GET | All torrent stats: mode, Real UL, Injected UL, ratio, history |
| `/api/stats/csv` | GET | Download stats as a CSV file |
| `/api/stats/purge` | DELETE | Purge torrents — params: `keep_active`, `inactive_hours` |
| `/api/health` | GET | Lists stalled, anomalous, and target-reached torrents |
| `/api/history` | GET | Time-series snapshots for all torrents |
| `/api/history/{ih}` | GET | Time-series for one torrent (8-char hash prefix) |
| `/api/config` | GET | Current `config.ini` as JSON |
| `/api/config/reload` | POST | Reload `config.ini` without restarting |
| `/api/version` | GET | Current version vs latest GitHub release |
| `/api/upload` | POST | Upload a `.torrent` file to pre-register infohash + size |
| `/ws/logs` | WebSocket | Real-time log stream |

**Purge parameters:**

| Parameter | Type | Default | Effect |
|---|---|---|---|
| `keep_active` | bool | `true` | Preserve torrents that announced recently |
| `inactive_hours` | int | `0` | Also purge torrents with no announce in the last N hours. Entries without a timestamp (pre-v1.7) are always purged when this is set. |

```
DELETE /api/stats/purge?keep_active=true&inactive_hours=12
```

---

## Reading the logs

```
[DOWN] 99887766 | DL:  800.0M UL:  640.0M +  44.1M R:0.80 ETA:~12a #8
[DOWN] 99887766 | DL:  800.0M UL:  640.0M +   0.0M R:0.80 ETA:~12a #9 [STAG]
[SEED] abcdef12 | UL:  312.4M +  18.2M #23
[SEED] abcdef12 | UL:  312.4M +   0.0M #24 [STALL_NET]
[DOWN] 11223344 | DL: 1200.0M UL: 1960.0M +   0.1M R:1.63 ETA:~0a #41 [TARGET_REACHED]
```

| Field | Meaning |
|---|---|
| `[DOWN]` / `[SEED]` | Mode: downloading, or seeding with no download history |
| `DL` | Cumulative downloaded as reported by the client |
| `UL` | Cumulative injected upload sent to the tracker |
| `+N` | Upload delta added this announce (0 during stagnation) |
| `R` | Current ratio — UL / DL |
| `ETA:~Na` | Estimated announces remaining to reach `target_ratio` |
| `#N` | Total announce count for this torrent |
| `[STAG]` | Deliberate stagnation — upload delta set to 0 this cycle |
| `[STALL_NET]` | N consecutive announces with `downloaded = 0` from the client |
| `[STALL_ALGO]` | Engine stagnation active |
| `[TARGET_REACHED]` | Target ratio achieved — injection stopped |
| `[PURGED]` | Torrent removed from stats (event=stopped or manual purge) |
| `[AUTO-PURGE]` | Torrent removed automatically after 12 h of no announce |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Ratio not increasing | 0 leechers on tracker (swarm-aware block) | Wait for leechers; check Charts page for history |
| `[STALL_NET]` on all torrents | Client reports 0 download consistently | Normal for pure seeders — lower `stall_announce_threshold` if needed |
| Injection only during certain hours | `inject_hours` restricts the window | Set `inject_hours = 0-23` for always-on |
| HTTPS announces not intercepted | mitmproxy CA not trusted | Re-run installer, or import CA manually (see Installation) |
| Web UI not loading | Missing dependencies | `pip install fastapi uvicorn python-multipart` |
| `mitmproxy` not found | Dependency not installed | `pip install mitmproxy` |
| Config changes not applied | Service not reloaded | Click **Reload config.ini** in Config page, or `systemctl restart newgreedy` |
| Torrents still listed after purge | Entries without timestamp (pre-v1.7 stats) | Use **Purge inactive 12h** button — old entries are cleared |
| Service crashes at startup | Inline comments in old config not parsed | Fixed in v1.7 — delete `stats.json` and restart if upgrading from v1.6 |

---

## Files

| File | Role |
|---|---|
| `newgreedy.py` | Launcher — starts the watchdog, update check thread, and web UI thread |
| `newgreedy_addon.py` | mitmproxy addon — intercepts announces, runs the ratio engine, writes stats |
| `newgreedy_web.py` | FastAPI application — serves the web UI and REST API |
| `config.ini` | Main configuration file |
| `stats.json` | Persisted torrent stats (schema v3) — created on first announce |
| `torrent_registry.json` | Stores infohash + size from `.torrent` imports |
| `newgreedy.log` | Log file (also streamed live in the Logs page) |
| `static/` | Web UI assets (HTML, CSS, JS) |
| `install.sh` | Installer — Linux / macOS |
| `install.ps1` | Installer — Windows |
| `uninstall.sh` | Uninstaller — Linux / macOS |
| `requirements.txt` | Python dependencies |
| `Dockerfile` | Docker image |
| `docker-compose.yml` | Docker Compose stack |

---

## Changelog

### v1.7.0
Ratio history charts, `inject_hours` scheduler, `.torrent` import, dark/light theme, Real UL vs Injected UL columns, auto-purge inactive after 12 h, explicit mode field in stats, `configparser` inline comment fix, full English web UI.

### v1.6.5
Fixed `STALL_ALGO` blocking at ~40%. Fixed `STALL_NET` false positives on pure seeders. Added update check at boot, watchdog, CSV export, status filters, Help page, swarm-aware injection, auto-stop at target, Pareto noise, stats schema v2.

### v1.5 – v1.6
Web UI introduced (dashboard, torrents, charts, config, logs). Announce interval jitter, corrupt field injection, smart stagnation, target ratio buffer, progress bars.

### v1.3 – v1.4
Ratio-based upload engine, peer_id rotation, stats persistence, SIGHUP reload, seed credit, per-torrent cap, Windows installer, Docker support.

### v1.0 – v1.2
Initial HTTP proxy. HTTPS interception via mitmproxy. Upload multiplier with noise and logistic progression.
