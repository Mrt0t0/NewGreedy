<div align="center">

# :green_circle: NewGreedy

### BitTorrent announce proxy — Upload ratio spoofer

[![Version](https://img.shields.io/badge/version-1.5.1-blue?style=flat-square)]()
[![Python](https://img.shields.io/badge/python-3.9%2B-green?style=flat-square)]()
[![License](https://img.shields.io/badge/license-GPL--3.0-gray?style=flat-square)]()
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows%20%7C%20Docker-lightgray?style=flat-square)]()

</div>

---

> :warning: **Disclaimer**
> NewGreedy is provided for **educational and research purposes only**.
> Using this tool on private trackers may violate their terms of service and result in a permanent ban.
> The author assumes no responsibility for any misuse. **Use at your own risk.**

---

## :wrench: What does NewGreedy do?

NewGreedy is a **local proxy** that runs on a **single port (default: 3456)**.
It intercepts BitTorrent tracker announces — both **HTTP and HTTPS** — and rewrites
reported values before they reach the tracker.

> Your torrent client is **never modified**. Only what is reported to the tracker changes.

---

## :rocket: v1.5.1 — What changed from v1.4

### Bug Fixes

| # | Bug | Status |
|---|---|---|
| 1 | `uploaded` field sent as delta instead of cumulative session total | **Fixed** |
| 2 | Stagnation with `real_ul=0` sent `uploaded=0`, causing ratio regression on tracker | **Fixed** |
| 3 | Stagnation could produce a cumulative value lower than the previous announce | **Fixed** |
| 4 | Session restart sent incorrect `uploaded` value relative to previous session | **Fixed** |
| 5 | `stall_announce_threshold` default was 10 — stall detection was too slow | **Fixed (now 8)** |

### Changes

| # | Change | v1.4 | v1.5.1 |
|---|---|---|---|
| 1 | Stagnation during `Ann#1–N` | always possible | blocked for first N announces (`min_announces_before_stagnation`) |
| 2 | Stagnation probability | flat 15% | context-aware: lower when far from target, higher near plateau |
| 3 | `catch_up_factor` default | 0.15 | **0.22** — faster catch-up for high-target ratios |
| 4 | `stagnation_probability` default | 0.15 | **0.08** — reduced risk of ratio regression |
| 5 | Cumulative upload guard | none | `uploaded` never decreases within a session |
| 6 | `SentUL` log field | absent | **added** — shows exact value sent to tracker |

### New Features

| # | Feature | Config key |
|---|---|---|
| 1 | Target ratio buffer — internal target slightly above configured value | `target_ratio_buffer` |
| 2 | Minimum announces before stagnation may occur | `min_announces_before_stagnation` |
| 3 | Smart stagnation — probability scales with ratio progress | automatic |
| 4 | Health API endpoint `/api/health` — lists stalled and anomalous torrents | Web UI |
| 5 | `[STALL]` flag threshold reduced from 10 to 8 announces | `stall_announce_threshold` |

---

## :gear: How the calculation works

```text
downloaded (per torrent, cumulative)
     │
     ├─ target_ratio + target_ratio_buffer (e.g. 1.60 + 0.03 = 1.63 internal)
     ├─ non-linear progression (exponential decay × catch_up_factor)
     ├─ smart stagnation (context-aware probability)
     ├─ capped by max_simulated_speed_mbps × interval
     ├─ capped by max_ratio_per_torrent
     ├─ capped by max_global_ratio_per_tracker
     ├─ never below previous cumulative value  ← NEW in v1.5.1
     └─ ± Gaussian noise (upload_noise_pct)
          │
          ▼
  cumul_rep_ul ──► sent to tracker as `uploaded`  ← ALWAYS CUMULATIVE
```

---

## :rocket: Quick Start

### 1. Get the files

```bash
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
```

### 2. Install

```bash
# Linux / macOS
sudo ./install.sh

# Update existing installation
sudo ./install.sh --update

# Windows
powershell -ExecutionPolicy Bypass -File .\install.ps1

# Update Windows
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Update
```

### 3. Configure your torrent client

**qBittorrent** — Settings → Connection → Proxy:

| Field | Value |
|---|---:|
| Type | HTTP |
| Host | 127.0.0.1 |
| Port | 3456 |
| Use proxy for tracker communication | :white_check_mark: |

---

## :nut_and_bolt: Configuration

| Platform | Config path |
|---|---|
| Linux (systemd) | `/opt/newgreedy/config.ini` |
| macOS | `/opt/newgreedy/config.ini` |
| Windows | `%LOCALAPPDATA%\NewGreedy\config.ini` |
| Docker | `./config.ini` (mounted volume) |

<details>
<summary><strong>Full config reference (click to expand)</strong></summary>

```ini
[proxy]
listen_port               = 3456
tracker_timeout           = 5

[spoofing]
upload_mode               = ratio_based
target_ratio              = 1.60
target_ratio_buffer       = 0.03        ; internal target = target_ratio + buffer
anti_clustering           = true
max_ratio_per_torrent     = 3.0
max_global_ratio_per_tracker = 2.5
catch_up_factor           = 0.22        ; was 0.15 in v1.4
seed_credit_mb            = 5.0
seeding_dl_ratio          = 0.85
max_simulated_speed_mbps  = 10.0
upload_noise_pct          = 3.0
stagnation_probability    = 0.08        ; was 0.15 in v1.4

[anti_detection]
user_agent_mode           = random
user_agent_value          = qBittorrent/4.6.8
spoof_user_agent          = true
spoof_peer_id             = true
spoof_peers               = true
peer_variance             = 0.15
spoof_port                = true
port_range                = 6881-6999
spoof_headers             = true
intercept_scrape          = true
tracker_whitelist         =
tracker_blacklist         =

[ssl]
ssl_verify_trackers       = true

[stats]
persist_stats             = true
stats_file                = stats.json

[web]
web_enabled               = true
web_host                  = 0.0.0.0
web_port                  = 8080

[advanced]
min_announce_interval     = 1800
log_level                 = INFO
multi_tracker_delay_min   = 0.5
multi_tracker_delay_max   = 8.0
event_anomaly_probability = 0.03
corrupt_field_probability = 0.20
stall_announce_threshold  = 8           ; was 10 in v1.4
min_announces_before_stagnation = 3     ; NEW in v1.5.1
interval_jitter_pct       = 0.08
```

</details>

---

## :bar_chart: Reading the logs

```text
[SEEDING    ] 0f14172e | DL: 10521.69MB | RealUL:    0.00MB | CumUL: 15420.00MB | +DUL: 1718.67MB | SentUL: 15420.00MB | Ratio:1.465 ETA:~2ann | Ann#9
[SEEDING    ] a326b536 | DL:  4752.55MB | RealUL:    0.00MB | CumUL:  2865.96MB | +DUL: 2865.96MB | SentUL:  2865.96MB | Ratio:0.428 ETA:~9ann | Ann#14
[SEEDING    ] 14da1f2a | DL:     0.00MB | RealUL:    0.00MB | CumUL:   270.54MB | +DUL:    6.26MB | SeedUL:   270.54MB | Ann#28
[DOWNLOADING] 8a98b8cd | DL:     0.00MB | RealUL:    0.00MB | CumUL:     0.00MB | +DUL:    0.00MB | Ratio:0.000 ETA:~0ann | Ann#9 [STALL]
```

| Field | Meaning |
|---|---|
| `DL` | Cumulative downloaded (MB) |
| `RealUL` | Real upload this announce cycle |
| `CumUL` | Cumulative reported upload (all announces) |
| `+DUL` | Upload delta added this announce (0 during stagnation) |
| `SentUL` | **Exact value sent to tracker as `uploaded`** (= CumUL) |
| `Ratio` | CumUL / DL (only when DL > 0) |
| `SeedUL` | CumUL for pure seeders (DL = 0) |
| `ETA` | Estimated announces to reach target ratio |
| `Ann#N` | Total announce count for this torrent |
| `[STAG]` | Stagnation cycle: +DUL = 0, SentUL = previous CumUL |
| `[STALL]` | Torrent flagged as stalled after N consecutive DL=0 announces |

---

## :globe_with_meridians: Web UI

Available on port `8080`:

| Endpoint | Description |
|---|---|
| `/` | Dashboard with global stats |
| `/static/torrents.html` | Per-torrent stats and ratio progress |
| `/static/charts.html` | Upload/ratio charts |
| `/static/config.html` | Config editor with live reload |
| `/static/logs.html` | Live log viewer |
| `/api/stats` | JSON stats for all tracked torrents |
| `/api/health` | Health check: stalled torrents, anomalies |
| `/api/config` | Current config as JSON |
| `/api/config/reload` | Reload config.ini without restart |

---

## :file_folder: Files

| File | Role |
|---|---|
| `newgreedy.py` | Launcher — starts mitmproxy and the web interface |
| `newgreedy_addon.py` | Core logic — announce rewriting, anti-detection, stats |
| `newgreedy_web.py` | FastAPI web UI and JSON API |
| `config.ini` | Configuration |
| `install.sh` | Automated installer — Linux / macOS |
| `install.ps1` | PowerShell installer — Windows |
| `requirements.txt` | Python dependencies |
| `Dockerfile` | Docker image |
| `docker-compose.yml` | Docker stack |
| `static/` | Web UI assets |

---

## :hammer_and_wrench: Troubleshooting

| Problem | Cause | Solution |
|---|---|---|
| Ratio not increasing | STALL torrent | Check peer availability; look for `[STALL]` in logs |
| HTTPS not intercepted | CA not trusted | Re-run installer or trust the CA manually |
| Web UI unavailable | Missing dependencies | `pip install fastapi uvicorn` |
| `mitmproxy not found` | Dependency missing | `pip install mitmproxy` |
| Ratio regresses after restart | Old bug (v1.4) | Fixed in v1.5.1 — upgrade |
| Config changes not applied | Service not reloaded | POST `/api/config/reload` or send SIGHUP |

---

## :memo: Changelog

### v1.5.1 — Current
- **Fixed**: `uploaded` is now always the cumulative session total, never a per-announce delta.
- **Fixed**: Stagnation no longer sends `uploaded=0`; it preserves the previous cumulative value.
- **Fixed**: Guard added — `uploaded` can never decrease within a session.
- **Fixed**: Session restart correctly inherits persisted `cumul_rep_ul` as `prev_rep_ul`.
- **Fixed**: `stall_announce_threshold` reduced from 10 to 8.
- **New**: `target_ratio_buffer` — internal target slightly above configured value.
- **New**: `min_announces_before_stagnation` — stagnation blocked on early announces.
- **New**: Smart stagnation — probability scales with ratio progress.
- **New**: `SentUL` log field — exact value sent to tracker.
- **New**: `/api/health` endpoint — lists stalled and anomalous torrents.
- **Changed**: `catch_up_factor` default 0.15 → 0.22.
- **Changed**: `stagnation_probability` default 0.15 → 0.08.

### v1.5
- Announce interval jitter, residual bytes, `corrupt=` field injection.
- `event=started` session reset, cross-tracker global ratio cap.
- Web UI (dashboard, torrents, charts, config, logs).
- Updated installers and Docker.

### v1.4
- Ratio-based upload engine with anti-clustering and stagnation.
- peer_id rotation, UA-specific headers, multi-tracker desync.
- Detectability score logging, stats persistence, SIGHUP reload.

### v1.3
- Single port 3456 for HTTP and HTTPS. Ratio-based upload mode.
- Seed credit, per-torrent cap, Windows installer, Docker.

### v1.2
- Dual-port proxy. Upload multiplier with noise and logistic progression.
- peer_id / UA / port spoofing.

### v1.1
- HTTPS interception via mitmproxy addon. Fixed info_hash corruption.

### v1.0
- Initial HTTP proxy with upload multiplier.
