<div align="center">

# NewGreedy

### BitTorrent announce proxy — Upload ratio spoofer

[![Version](https://img.shields.io/badge/version-1.7.5-blue?style=flat-square)](https://github.com/Mrt0t0/NewGreedy/releases)
[![Python](https://img.shields.io/badge/python-3.9%2B-green?style=flat-square)](https://python.org)
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

---

### [web]

| Key | Default | Description |
|---|---|---|
| `web_enabled` | `true` | Enable the FastAPI web UI and REST API. |
| `web_host` | `0.0.0.0` | Bind address for the web server. |
| `web_port` | `8080` | Port for the web UI. |

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

---

## Web UI

| Page | URL | Description |
|---|---|---|
| Dashboard | `/` | Summary cards, tracker cap warning banner, auto-refresh every 15 s |
| Torrents | `/torrents` | Full table — Real DL, Real UL, Injected UL, Ratio, ETA, Last seen, purge |
| Charts | `/charts` | Time-series UL and ratio per torrent |
| Config | `/config` | Current `config.ini` values + `.torrent` import |
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
[GLOBAL_CAP] tracker.AAABBBCCC.org | tracker_ratio capped → UL adjusted to 1280.0M
[TRACKER_DOWN] tracker.AAABBBCCC.com — 3 consecutive errors, entering backoff
[TRACKER_UP] tracker.AAABBBCCC.com — recovered
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

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Ratio on tracker exceeds `max_global_ratio` | Pre-v1.7.5: cap was computed after accumulation | Upgrade to v1.7.5 — fixed by correct ordering |
| Ratio cap resets after restart | Pre-v1.7.5: `_tracker_cumul` not persisted | Upgrade to v1.7.5 — now saved in `stats.json` |
| `[STAG][STALL_ALGO][TARGET_REACHED]` together | Pre-v1.7.5: stagnation ran after target check | Upgrade to v1.7.5 — `_calc_upload` short-circuits on target |
| `[TRACKER_DOWN]` in logs, announces skipped | Tracker returning 5xx — backoff active | Wait for automatic recovery; check tracker status |
| Purge by inactivity does nothing | Entries have no timestamp | Use `GET /api/stats/purge?inactive_hours=12` to preview first |
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
| `newgreedy_addon.py` | mitmproxy addon — intercepts announces, ratio engine, stats, backoff |
| `newgreedy_web.py` | FastAPI app — web UI and REST API |
| `config.ini` | Main configuration file |
| `stats.json` | Persisted stats (schema v4) — includes `_tracker_cumul` |
| `torrent_registry.json` | Infohash + size from `.torrent` imports |
| `newgreedy.log` | Log file (streamed live in Logs page) |
| `static/` | Web UI assets |
| `install.sh` | Installer — Linux / macOS |
| `install.ps1` | Installer — Windows |
| `uninstall.sh` | Uninstaller — Linux / macOS |
| `requirements.txt` | Python dependencies |
| `Dockerfile` | Docker image |
| `docker-compose.yml` | Docker Compose stack |

---

## Changelog

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
- Auto-purge after 12 h inactivity

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
