<div align="center">


### BitTorrent announce proxy — Upload ratio spoofer

[![Version](https://img.shields.io/badge/version-1.6.0-blue?style=flat-square)]()
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

NewGreedy is a **local transparent proxy** that runs on a single port (default: **3456**).
It intercepts BitTorrent tracker announces — both **HTTP and HTTPS** — and rewrites the `uploaded`
value reported to the tracker before the request is forwarded.

> Your torrent client is **never modified**. Only what is reported to the tracker changes.

### Features

| Feature | Description |
|---|---|
| **Ratio spoofing** | Drives upload ratio toward a configurable target using a smart non-linear catch-up algorithm |
| **Multi-torrent** | Tracks every torrent independently — each gets its own progression curve, peer_id, and port |
| **Persistent stats** | Per-torrent stats survive restarts via `stats.json`; auto-saved every 5 minutes |
| **Anti-detection** | Announce interval jitter, DUL smoothing, persistent peer_id, UA matching, uploaded monotone guard |
| **Dead tracker detection** | Automatically blacklists failing trackers for 12h; resets when they recover |
| **Torrent lifecycle** | Detects removed torrents (END status) and purges them automatically |
| **Real-time Web UI** | Dashboard, torrent table, charts, live log viewer, config viewer — all in-browser on port 8080 |
| **HTTP + HTTPS** | Intercepts both protocols equally |
| **Docker ready** | Single `docker compose up -d` to deploy |

---

## :gear: How the calculation works

```text
downloaded (cumulative value from tracker announce)
     │
     ├─ target_ratio + target_ratio_buffer  →  internal target (e.g. 1.60 + 0.03 = 1.63)
     ├─ non-linear catch-up  (exponential decay × catch_up_factor)
     ├─ DUL smoothing  (spread over multiple announces — alpha=0.4)
     ├─ smart stagnation  (context-aware probability near plateau)
     ├─ capped by  max_simulated_speed_mbps × interval
     ├─ capped by  max_ratio_per_torrent
     ├─ capped by  max_global_ratio_per_tracker
     ├─ never below previous cumulative value  (monotone guard)
     └─ ± Gaussian noise  (upload_noise_pct)
          │
          ▼
  cumul_rep_ul ──► sent to tracker as `uploaded`   ← ALWAYS CUMULATIVE
```

---

## :computer: Installation

> **The installer handles everything automatically** — Python check, dependencies, CA certificate generation and trust, and auto-start setup.

Choose your platform:

- [Linux](#linux)
- [macOS](#macos)
- [Windows](#windows)
- [Docker](#docker) ← recommended if you already have Docker

---

### Linux

#### Step 1 — Prerequisites

You only need `git`. The installer handles Python and everything else.

```bash
# Debian / Ubuntu
sudo apt install git -y

# Fedora
sudo dnf install git -y

# Arch
sudo pacman -S git
```

#### Step 2 — Download NewGreedy

```bash
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
```

#### Step 3 — Run the installer

```bash
sudo ./install.sh
```

That's it. The script automatically:

- ✅ Checks for Python 3.9+ and installs it if missing
- ✅ Installs all Python packages (`mitmproxy`, `fastapi`, `uvicorn`)
- ✅ Copies files to `/opt/newgreedy`
- ✅ Generates the mitmproxy CA certificate
- ✅ Adds the certificate to your system trust store
- ✅ Creates and enables a **systemd service** (auto-start on boot)

At the end you will see:

```
[✓] NewGreedy v1.6.0 installation complete!
    Proxy:   127.0.0.1:3456
    Web UI:  http://localhost:8080
```

To update an existing installation:
```bash
sudo ./install.sh --update
```

#### Step 4 — Configure your torrent client

In qBittorrent: **Settings → Connection → Proxy Server**

| Field | Value |
|---|---|
| Type | HTTP |
| Host | `127.0.0.1` |
| Port | `3456` |
| Use proxy for tracker communication | ✅ checked |

> **Important:** Disable **UDP trackers** in your client. UDP bypasses the proxy and cannot be intercepted.

#### Manage the service

```bash
sudo systemctl start   newgreedy
sudo systemctl stop    newgreedy
sudo systemctl restart newgreedy
sudo systemctl status  newgreedy
```

---

### macOS

#### Step 1 — Prerequisites

Install [Homebrew](https://brew.sh) if not already installed:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Install git:
```bash
brew install git
```

#### Step 2 — Download NewGreedy

```bash
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
```

#### Step 3 — Run the installer

```bash
sudo ./install.sh
```

The script automatically:

- ✅ Checks for Python 3.9+ and installs it via Homebrew if missing
- ✅ Installs all Python packages
- ✅ Generates and trusts the CA certificate in **macOS Keychain**
- ✅ Creates a **launchd agent** for auto-start at login

> If macOS prompts for permission to add the certificate to Keychain, click **Allow**.

#### Step 4 — Configure your torrent client

Same as Linux — set HTTP proxy to `127.0.0.1:3456` and disable UDP trackers.

---

### Windows

#### Step 1 — Prerequisites

- **Git** — download from [git-scm.com](https://git-scm.com/download/win)

Python is optional — the installer will auto-install it via **winget** if missing.

#### Step 2 — Download NewGreedy

```powershell
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
```

#### Step 3 — Run the installer (as Administrator)

Right-click PowerShell → **Run as Administrator**, then:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The script automatically:

- ✅ Checks for Python 3.9+ and installs it via **winget** if missing
- ✅ Installs all Python packages
- ✅ Generates the mitmproxy CA certificate
- ✅ Imports the certificate into **Windows Trusted Root store**
- ✅ Registers a **Scheduled Task** to start NewGreedy at logon
- ✅ Starts NewGreedy immediately in the background

At the end you will see:

```
NewGreedy v1.6.0 installation complete!
  Proxy:   127.0.0.1:3456
  Web UI:  http://localhost:8080
```

To update:
```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Update
```

#### Step 4 — Configure your torrent client

Same as Linux — set HTTP proxy to `127.0.0.1:3456` and disable UDP trackers.

---

### Docker

Docker is ideal if you want a clean, isolated setup with no installation steps.

#### Prerequisites

- **Docker Desktop** on [Windows](https://www.docker.com/products/docker-desktop/) or [macOS](https://www.docker.com/products/docker-desktop/)
- On Linux:

```bash
sudo apt install docker.io docker-compose-plugin -y
sudo usermod -aG docker $USER
# Log out and back in for group change to take effect
```

#### Step 1 — Download NewGreedy

```bash
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
```

#### Step 2 — Start

```bash
docker compose up -d
```

Done. NewGreedy is running:
- Proxy: **127.0.0.1:3456**
- Web UI: **http://localhost:8080**

#### Step 3 — Trust the CA certificate

The certificate is generated inside the container on first start. Extract it to your host:

```bash
docker compose cp newgreedy:/root/.mitmproxy/mitmproxy-ca-cert.pem ./mitmproxy-ca-cert.pem
```

Then trust it:
- **Linux:** `sudo cp mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/mitmproxy.crt && sudo update-ca-certificates`
- **macOS:** `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain mitmproxy-ca-cert.pem`
- **Windows:** double-click the `.pem` → install in **Trusted Root Certification Authorities**

#### Step 4 — Configure your torrent client

Same as other platforms — HTTP proxy `127.0.0.1:3456`, disable UDP trackers.

#### Useful commands

```bash
docker compose logs -f          # live logs
docker compose down             # stop
docker compose restart          # restart
docker compose up -d --build    # update after code change
```

> `stats.json` and `newgreedy.log` are mounted as volumes — your data persists across restarts.

---

## :nut_and_bolt: Configuration

| Platform | Config path |
|---|---|
| Linux / macOS | `./config.ini` (same directory as `newgreedy.py`) |
| Windows | same directory as `newgreedy.py` |
| Docker | `./config.ini` (mounted volume — edit on host) |

Edit `config.ini` then **restart NewGreedy** to apply, or on Linux send `SIGHUP` to reload without restart:
```bash
kill -HUP $(pgrep -f newgreedy.py)
```

<details>
<summary><strong>Full config reference — v1.6.0 (click to expand)</strong></summary>

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
catch_up_factor           = 0.22
seed_credit_mb            = 5.0
seeding_dl_ratio          = 0.85
max_simulated_speed_mbps  = 10.0
upload_noise_pct          = 3.0
stagnation_probability    = 0.08

[anti_detection]
user_agent_mode           = random
user_agent_value          = qBittorrent/4.6.8
spoof_user_agent          = true
spoof_peer_id             = true        ; peer_id persisted across restarts
spoof_peers               = true
peer_variance             = 0.15
spoof_port                = true
port_range                = 6881-6999
spoof_headers             = true
intercept_scrape          = true
tracker_whitelist         =
tracker_blacklist         =
dul_smooth_alpha          = 0.4         ; DUL smoothing (0=flat, 1=instant)

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
stall_announce_threshold  = 8
min_announces_before_stagnation = 3
interval_jitter_pct       = 0.15        ; ±15% announce interval randomization
startup_grace_seconds     = 15          ; ignore announces on startup
stats_save_interval       = 300         ; periodic save interval (seconds)
dead_tracker_threshold    = 3           ; errors before blacklisting a tracker
dead_tracker_ttl_hours    = 12          ; blacklist duration in hours
stag_warn_threshold       = 3           ; consecutive stagnations before WARN
end_ttl_minutes           = 60          ; minutes of silence before END status
purge_ttl_minutes         = 120         ; minutes after END before stats purge
```

</details>

---

## :bar_chart: Reading the logs

```text
[SEEDING    ] 0f14172e | DL: 10521.69MB | RealUL:    0.00MB | CumUL: 15420.00MB | +DUL: 1718.67MB | SentUL: 15420.00MB | Ratio:1.465 ETA:~2ann | Ann#9
[SEEDING    ] ce785f27 | DL:  5798.45MB | RealUL:  266.97MB | CumUL:  9451.46MB | +DUL:   0.00MB | SentUL:  9451.46MB | Ratio:1.630 ETA:~0ann | Ann#70 [STAG]
[SEEDING    ] c6ebd40f | DL:   480.47MB | RealUL:    0.16MB | CumUL:   605.59MB | +DUL:  74.55MB | SentUL:   605.59MB | Ratio:1.260 ETA:~1ann | Ann#5
[DOWNLOADING] 8a98b8cd | DL:     0.00MB | RealUL:    0.00MB | CumUL:     0.00MB | +DUL:   0.00MB | Ratio:0.000 ETA:~0ann | Ann#9 [STALL_NET]
```

| Field | Meaning |
|---|---|
| `DL` | Cumulative downloaded (MB) |
| `RealUL` | Real upload measured this announce cycle |
| `CumUL` | Cumulative reported upload (all announces) |
| `+DUL` | Upload delta added this announce (0 during stagnation) |
| `SentUL` | Exact value sent to tracker as `uploaded` |
| `Ratio` | CumUL / DL |
| `ETA` | Estimated announces remaining to reach target |
| `Ann#N` | Total announce count for this torrent |
| `[STAG]` | Stagnation cycle — +DUL=0, SentUL preserved |
| `[STALL_NET]` | No peers — upload naturally zero |
| `[STALL_ALGO]` | Algorithm failed to inject upload |
| `[CONV_DONE]` | Ratio stabilized at target |

---

## :globe_with_meridians: Web UI

Available at **http://localhost:8080**:

| Page | URL | Description |
|---|---|---|
| Dashboard | `/` | Health bar, KPIs, torrent table with mode badges |
| Torrents | `/torrents` | Full list with ratio progress bars |
| Charts | `/charts` | Ratio and CumUL charts per torrent |
| Config | `/config` | Read-only view of `config.ini` |
| Logs | `/logs` | Live WebSocket log stream, 7 filters |
| Help | `/help` | Full inline documentation |

### Torrent status badges

| Badge | Meaning |
|---|---|
| `SEED` | Pure seeder — no download data |
| `DL+SEED` | Downloading with upload injection active |
| `CONV_DONE` | Ratio stabilized at target |
| `END` | No announce for `end_ttl_minutes` — torrent likely removed |
| `STALL_NET` | No peers available |
| `STALL_ALGO` | Algorithm unable to inject upload |
| `SEED ACTIVE` | Real upload detected from peers |

### API endpoints

| Endpoint | Description |
|---|---|
| `GET /api/stats` | Per-torrent stats JSON |
| `GET /api/health` | Global health — active, errors, dead trackers |
| `GET /api/logs?n=500` | Last N log lines |
| `GET /api/config` | Current config as JSON |
| `WS /ws/logs` | Live log stream (WebSocket) |
| `WS /ws/stats` | Stats push every 5 seconds (WebSocket) |

---

## :file_folder: Files

| File | Role |
|---|---|
| `newgreedy.py` | Entry point — starts mitmproxy + web UI thread |
| `newgreedy_addon.py` | Core logic — announce rewriting, anti-detection, lifecycle |
| `newgreedy_web.py` | FastAPI web UI and JSON/WebSocket API |
| `config.ini` | All configuration parameters |
| `stats.json` | Auto-generated — per-torrent persisted state |
| `install.sh` | Automated installer — Linux / macOS |
| `install.ps1` | PowerShell installer — Windows |
| `requirements.txt` | Python dependencies |
| `Dockerfile` | Docker image |
| `docker-compose.yml` | Docker Compose stack |
| `static/` | Web UI HTML, CSS, JS assets |

---

## :hammer_and_wrench: Troubleshooting

| Problem | Cause | Solution |
|---|---|---|
| Ratio not increasing | `STALL_NET` | Check peer availability in your client |
| Ratio stuck despite peers | `STALL_ALGO` | Check logs; verify `catch_up_factor` |
| HTTPS not intercepted | CA not trusted | Follow the CA trust steps for your OS above |
| Web UI unavailable | Missing deps | `pip install fastapi uvicorn` |
| `mitmproxy not found` | Missing dep | `pip install mitmproxy` |
| Tracker shows as DEAD | 3+ consecutive errors | Check `/logs`; auto-resolves after 12h |
| Torrent disappeared | Purged after END | Increase `purge_ttl_minutes` |
| Stats lost after restart | Periodic save missed | Ensure `persist_stats = true`; `stats.json` writable |
| Config changes ignored | Not reloaded | Restart, or `kill -HUP $(pgrep -f newgreedy.py)` on Linux |
| UDP tracker not working | UDP bypasses proxy | Disable UDP trackers in your client |

---

## :memo: Changelog

### v1.6.0 - Current
- **FIX-01** Startup grace period (15s)
- **FIX-02** Connect errors → `[WARN]` with cumulative counter
- **FIX-03** `[STALL]` split into `[STALL_NET]` and `[STALL_ALGO]`
- **FIX-04** `cumul_rep_dl` artefact guard
- **FIX-05** Periodic `stats.json` save every 5 minutes
- **FIX-06** Torrent `END` status + auto-purge
- **NEW-01** HTTP tracker DUL injection verified
- **NEW-02** Dead tracker auto-blacklist (3 errors → 12h)
- **NEW-03** Announce timeout → `[WARN]`
- **NEW-04** Cross-tracker uploaded value lock
- **DET-01** `[STAG_PROLONGED]` warning
- **DET-02** `[CONV_DONE]` detection
- **DET-03** `[SEED_ACTIVE]` detection
- **SEC-01** Announce interval jitter ±15%
- **SEC-02** User-Agent preserved intact
- **SEC-03** +DUL smoothing (`dul_smooth_alpha`)
- **SEC-04** Persistent `peer_id` across restarts
- **SEC-05** Uploaded hard monotone guard
- **UI** Health bar, mode badges, Chart.js, live log page (WebSocket + 7 filters), Help page

### v1.5.1
- Fixed `uploaded` sent as delta instead of cumulative total
- Fixed stagnation sending `uploaded=0`
- Fixed `uploaded` could decrease within a session
- New: `target_ratio_buffer`, `min_announces_before_stagnation`, smart stagnation, `/api/health`

### v1.5
- Announce interval jitter, `corrupt=` field, `event=started` reset
- Cross-tracker ratio cap, Web UI

### v1.4
- Ratio-based engine, `peer_id` rotation, UA headers, multi-tracker desync

### v1.3
- Single port 3456, seed credit, Windows installer, Docker

### v1.2 / v1.1 / v1.0
- Initial releases
