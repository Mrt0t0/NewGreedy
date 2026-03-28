<div align="center">

# NewGreedy

### BitTorrent announce proxy -- Upload ratio spoofer

[![Version](https://img.shields.io/badge/version-1.4-blue?style=flat-square)]()
[![Python](https://img.shields.io/badge/python-3.9%2B-green?style=flat-square)]()
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
It intercepts BitTorrent tracker announces -- both **HTTP and HTTPS** -- and rewrites
the `uploaded` field before it reaches the tracker.

```
Your client         NewGreedy :3456 (HTTP + HTTPS)       Tracker
     |                       |                               |
     |---- /announce -------->|                               |
     |     uploaded=50MB      |   uploaded=80MB ------------->|
     |     downloaded=200MB   |   downloaded=200MB            |
     |                        |<----------- OK ---------------|
     |<-----------------------|                               |
```

> Your torrent client is **never modified**. Only what is reported to the tracker changes.

---

## :gear: How the calculation works

```
downloaded (per torrent, cumulative)
     │
     ├─ target_ratio (randomized 1.42–1.54)
     ├─ non-linear progression (exponential decay with announce count)
     ├─ catch_up_factor (fraction of delta applied this announce)
     ├─ capped by max_simulated_speed_mbps × interval
     ├─ capped by max_ratio_per_torrent
     ├─ 15% stagnation announces (no boost)
     └─ ± Gaussian noise (upload_noise_pct)
          │
          ▼
  reported_uploaded ──► sent to tracker
```

---

## :rocket: Quick Start

### 1. Get the files

```bash
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
```

### 2. Install (see platform sections below)

### 3. Configure your torrent client

**qBittorrent** -- Settings -> Connection -> Proxy:

| Field | Value |
|---|:---:|
| Type | HTTP |
| Host | 127.0.0.1 |
| Port | **3456** |
| Use proxy for tracker communication | :white_check_mark: |


---

## :penguin: Linux

### Install

```bash
chmod +x install.sh
sudo ./install.sh
```

What the installer does:
- Detects your distro (Debian/Ubuntu or RHEL/CentOS)
- Installs system packages: `git`, `python3-pip`, `ca-certificates`
- `pip install mitmproxy requests`
- Clones the repo to `/opt/newgreedy/`  (preserves your `config.ini` on update)
- Generates the mitmproxy CA and installs it into the system trust store
- Creates and enables a **systemd service**

### Manage

```bash
# Status
systemctl status newgreedy.service

# Real-time logs
tail -f /opt/newgreedy/newgreedy.log
journalctl -u newgreedy.service -f

# Reload config without restart
sudo kill -HUP $(systemctl show --property MainPID newgreedy.service | cut -d= -f2)

# Update (git pull + pip upgrade + restart)
sudo ./install.sh --update

# Stop / disable
systemctl stop    newgreedy.service
systemctl disable newgreedy.service
```

---

## :apple: macOS

### Install

```bash
chmod +x install.sh
sudo ./install.sh
```

The script detects macOS and:
- Uses `brew` if available
- Installs the CA into the **macOS Keychain** (`security add-trusted-cert`)
- Prints manual start instructions

### Run

```bash
cd /opt/newgreedy
python3 newgreedy.py

# Follow logs
tail -f /opt/newgreedy/newgreedy.log
```

### Update

```bash
sudo ./install.sh --update
```

---

## :computer: Windows

### Requirements

- Python 3.9+  -- [python.org](https://python.org)
- Git          -- [git-scm.com](https://git-scm.com)

### Install (PowerShell as Administrator)

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install.ps1
```

What the installer does:
- `pip install mitmproxy requests`
- Clones the repo to `%LOCALAPPDATA%\NewGreedy`
- Generates the mitmproxy CA and installs it into **Windows Trusted Root** via `certutil`
- Creates a **Windows Scheduled Task** that starts NewGreedy at logon

### Manage

```powershell
# View real-time logs
Get-Content "$env:LOCALAPPDATA\NewGreedy\newgreedy.log" -Wait

# Update
.\install.ps1 -Update

# Stop / Start
Stop-ScheduledTask  -TaskName "NewGreedy"
Start-ScheduledTask -TaskName "NewGreedy"
```

> :warning: SIGHUP config hot-reload is not available on Windows.
> Edit `config.ini` then restart the scheduled task to apply changes.

---

## :whale: Docker

### Start

```bash
docker compose up -d
```

### Logs

```bash
docker compose logs -f
docker exec newgreedy tail -f /app/newgreedy.log
```

### Update

```bash
git pull
docker compose up -d --build
```

### Mount your own config

Uncomment in `docker-compose.yml`:

```yaml
volumes:
  - ./config.ini:/app/config.ini:ro
```

Then: `docker compose up -d`

### Install the CA on the host (required for HTTPS)

```bash
# Extract CA from the container
docker cp newgreedy:/root/.mitmproxy/mitmproxy-ca-cert.pem ./mitmproxy-ca.pem

# Linux (Debian/Ubuntu)
sudo cp mitmproxy-ca.pem /usr/local/share/ca-certificates/mitmproxy-newgreedy.crt
sudo update-ca-certificates

# macOS
security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain mitmproxy-ca.pem

# Windows (PowerShell as Admin)
certutil -addstore -f ROOT mitmproxy-ca.pem
```

---

## :nut_and_bolt: Configuration

| Platform | Config path |
|---|---|
| Linux (systemd) | `/opt/newgreedy/config.ini` |
| macOS | `/opt/newgreedy/config.ini` |
| Windows | `%LOCALAPPDATA%\NewGreedy\config.ini` |
| Docker | `./config.ini` (if mounted) |

**Edit and reload live (Linux/macOS):**

```bash
nano /opt/newgreedy/config.ini
kill -HUP $(systemctl show --property MainPID newgreedy.service | cut -d= -f2)
```

<details>
<summary><strong>Full config reference (click to expand)</strong></summary>

```ini
; ------------------------------------------------------------
[proxy]
; ------------------------------------------------------------

; Port that NewGreedy (mitmproxy) listens on.
; Configure this same value in your torrent client proxy settings.
; Both HTTP and HTTPS tracker announces are handled on this single port.
listen_port               = 3456

; Timeout in seconds when forwarding an announce to the real tracker.
; Increase if your tracker is slow to respond.
tracker_timeout           = 5

; ------------------------------------------------------------
[spoofing]
; ------------------------------------------------------------

; Upload reporting mode.
;   ratio_based  ->  reported_ul = total_downloaded x target_ratio  (recommended)
;                    Works even if your real upload is 0 MB.
;   multiplier   ->  reported_ul = real_ul x a multiplier  (v1.2 compat)
;                    If real_ul = 0, reported_ul = 0. Not recommended.
upload_mode               = ratio_based

; [ratio_based only]
; Target ratio to maintain per torrent.
; Example: 1.5 means NewGreedy will report 1500 MB uploaded for every 1000 MB downloaded.
; Typical private tracker minimum: 1.0. Recommended: 1.2 to 2.0.
; Accepted range: 1.0 to 5.0
target_ratio              = 1.5

; [ratio_based only] -- NEW v1.4
; If target_ratio = 1.5 at first run, auto-randomizes it to 1.42-1.54.
; Prevents all instances reporting identical ratios (tracker detection vector).
; Set to false to keep the exact value defined above.
anti_clustering           = true

; [ratio_based only]
; Hard cap per torrent. Once the reported ratio reaches this value,
; NewGreedy stops boosting and only reports the real upload.
; Prevents suspiciously high ratios (e.g. 10.0) on low-traffic torrents.
max_ratio_per_torrent     = 3.0

; [ratio_based only]
; How fast NewGreedy catches up to the target ratio.
; Each announce closes this fraction of the remaining gap.
;   0.15  ->  closes 15% of the gap per announce  (~30 announces to reach target)
;   0.30  ->  closes 30% of the gap per announce  (~15 announces to reach target)
; Lower = more gradual and natural. Higher = faster but more suspicious.
; Recommended: 0.10 to 0.25
catch_up_factor           = 0.15

; [ratio_based only]
; MB of upload to report per announce when the torrent has no download reference
; (pure seeder: left=0 and real downloaded=0, e.g. file already on disk).
; The actual value is randomized using a triangular distribution (x0.4 to x1.8)
; so it is never the same twice.
; Set to 0 to disable seed credit entirely.
seed_credit_mb            = 5.0

; [ratio_based only]
; When the torrent is in SEEDING mode and real downloaded=0,
; NewGreedy injects a fake "downloaded" value into the announce.
; reported_downloaded = reported_ul x seeding_dl_ratio
; This prevents the tracker from seeing a 0/0 ratio which can be suspicious.
; Set to 0.0 to disable downloaded injection.
seeding_dl_ratio          = 0.85

; Maximum simulated upload speed in MB/s.
; NewGreedy will never report more upload than:
;   max_simulated_speed_mbps x announce_interval  (seconds)
; Example: 10.0 MB/s x 1800s = 18000 MB max per announce.
; Prevents impossible values (e.g. 100 GB uploaded in 30 minutes).
; Set to your real upload speed or slightly above.
max_simulated_speed_mbps  = 10.0

; Gaussian noise added to the final reported upload value (+/- this %).
; Makes the reported upload look like a real client with slight variations.
; Also applied as variance on the effective target_ratio itself.
; Recommended: 2.0 to 5.0. Set to 0 to disable.
upload_noise_pct          = 3.0

; Probability (0.0 to 1.0) that an announce reports NO extra upload credit. -- NEW v1.4
; Simulates natural pauses, disconnects, or tracker misses.
; Recommended: 0.10 to 0.20 for maximum realism.
stagnation_probability    = 0.15

; ------------------------------------------------------------
[anti_detection]
; ------------------------------------------------------------

; How to set the User-Agent header sent to the tracker.
;   random       ->  picks a realistic torrent client UA on each announce
;   fixed        ->  always uses the value defined in user_agent_value
;   passthrough  ->  forwards the original UA from your torrent client
user_agent_mode           = random

; [user_agent_mode = fixed only]
; The exact User-Agent string to send.
; Must match a real torrent client version to avoid detection.
user_agent_value          = qBittorrent/4.6.7

; Enable User-Agent spoofing (set false to disable entirely).
spoof_user_agent          = true

; Generate a peer_id consistent with the spoofed User-Agent.
; A mismatched peer_id and UA is a common detection vector.
; Rotated every 4-6 hours instead of fixed per session. -- UPDATED v1.4
; Recommended: true
spoof_peer_id             = true

; Randomize numwant / num_peers / num_seeds in the announce query.
; Adds natural variation to peer count values.
spoof_peers               = true

; +/- variation applied to peer count values (0.15 = +/-15%).
peer_variance             = 0.15

; Randomize the port announced to the tracker.
; Your real listening port is never exposed. A random port from port_range is used.
spoof_port                = true

; Range of ports to pick from when spoof_port = true.
; Should match a typical torrent client range.
port_range                = 6881-6999

; Remove proxy-revealing HTTP headers (X-Forwarded-For, Via, etc.)
; and replace with realistic torrent client headers.
; Recommended: true
spoof_headers             = true

; Intercept /scrape requests and patch the response to stay consistent
; with the spoofed upload values. Prevents ratio discrepancies on trackers
; that cross-check announce and scrape data.
intercept_scrape          = true

; Only spoof announces to trackers matching these domains (comma-separated).
; Leave empty to spoof all trackers.
; Example: tracker.mysite.org, private.tracker.net
tracker_whitelist         =

; Never spoof announces to trackers matching these domains (comma-separated).
; Takes precedence over tracker_whitelist.
; Example: tracker.opentrackr.org, open.tracker.cl
tracker_blacklist         =

; ------------------------------------------------------------
[ssl]
; ------------------------------------------------------------

; Verify SSL certificates when NewGreedy connects to the tracker.
; true   ->  standard SSL verification (recommended)
; false  ->  skip verification (use only if a tracker has a self-signed
;            or expired certificate and you see SSL errors in the logs)
ssl_verify_trackers       = true

; ------------------------------------------------------------
[stats]
; ------------------------------------------------------------

; Save per-torrent stats (cumulated DL, reported UL, ratio, announce count)
; to a JSON file. Stats are restored on restart so the ratio is never reset.
; Set to false to disable persistence (stats reset on every restart).
persist_stats             = true

; Path to the stats file (relative to the install directory, or absolute).
stats_file                = stats.json

; ------------------------------------------------------------
[advanced]
; ------------------------------------------------------------

; Minimum interval (seconds) between two announces to the same tracker.
; NewGreedy ignores announces that arrive sooner than 90% of this value.
; The tracker itself sends its preferred interval in the announce response
; (that value takes precedence at runtime).
; Default matches the BitTorrent standard (1800s = 30 minutes).
min_announce_interval     = 1800

; Log verbosity level. -- NEW v1.4
;   DEBUG    ->  shows per-announce detectability score (0-10) and full rewrite detail
;   INFO     ->  standard announce log (recommended)
;   WARNING  ->  only unusual events
;   ERROR    ->  only errors
log_level                 = INFO

; Random delay (seconds) between duplicate announces on multi-tracker torrents. -- NEW v1.4
; Prevents all trackers receiving simultaneous identical announces.
; Recommended: 0.5 to 8.0
multi_tracker_delay_min   = 0.5
multi_tracker_delay_max   = 8.0

; Probability (0.0 to 1.0) of injecting a spontaneous event=stopped + event=started. -- NEW v1.4
; Simulates reconnects to explain ratio jumps and peer count changes naturally.
; Recommended: 0.02 to 0.05
event_anomaly_probability = 0.03
```

</details>

---

## :bar_chart: Reading the logs

```text
[17:30:12] [DOWNLOADING] 6000079 | DL: 200.00MB | RealUL: 0.00MB | RepUL:  4.52MB | Ratio: 1.050 | Ann#1
[18:00:14] [SEEDING    ] 80002223 | DL:   0.00MB | RealUL: 3.28MB | RepUL:  8.87MB | SeedUL:2849M | Ann#49
[18:32:29] [SEEDING    ] 80002223 | DL:   0.00MB | RealUL: 3.60MB | RepUL:  3.60MB | SeedUL:2856M | Ann#51 [STAG]
```

| Field | Meaning |
|---|---|
| `DL` | Real downloaded this announce cycle |
| `RealUL` | Real uploaded this announce cycle |
| `RepUL` | What NewGreedy reported to the tracker for this cycle |
| `Ratio` | Cumulative reported ratio (shown when DL > 0) |
| `SeedUL` | Cumulative reported upload |
| `Ann#N` | Total announce count for this torrent |
| `[STAG]` | **Stagnation cycle:** no extra boost was applied (simulates a natural pause) |
| `[DUP]` | **Duplicate tracker:** skipped to prevent double-counting on multi-tracker torrents |
---

## :file_folder: Files

| File | Platform | Role |
|---|---|---|
| `newgreedy.py` | All | Launcher -- starts mitmproxy with the addon |
| `newgreedy_addon.py` | All | Core logic -- intercepts and rewrites announces |
| `config.ini` | All | Configuration (inline documentation) |
| `requirements.txt` | All | Python dependencies |
| `install.sh` | Linux / macOS | Installer + updater (`--update`) |
| `install.ps1` | Windows | PowerShell installer + updater (`-Update`) |
| `Dockerfile` | Docker | Container image definition |
| `docker-compose.yml` | Docker | Compose stack |

---

## :hammer_and_wrench: Troubleshooting

| Problem | Cause | Solution |
|---|---|---|
| Announces not intercepted | Proxy not configured | type=HTTP, host=127.0.0.1, port=3456 |
| HTTPS not intercepted | CA not trusted | Re-run installer or add CA manually |
| `mitmproxy not found` | Dep missing | `pip install mitmproxy` |
| `SSL: CERTIFICATE_VERIFY_FAILED` | CA not installed | Re-run installer |
| Ratio not improving | `catch_up_factor` too low | Raise to 0.25-0.35 |
| Ratio improving too fast | `target_ratio` too high | Lower `target_ratio` |
| Service not starting | Port in use | Change `listen_port` in config.ini |
| Stats lost after restart | Wrong path or disabled | Check `persist_stats = true` |
| Docker: HTTPS not intercepted | CA not on host | Follow the Docker CA install steps |

---

## :memo: Changelog

### v1.4 -- Current
- **Anti-clustering ratio**: `target_ratio = 1.5` auto-randomized to **1.42–1.54** on first run
- **Stagnation simulation**: 15% of announces report no extra credit (`stagnation_probability`)
- **Exponential decay**: upload credit is heavier early in seeding, tapers naturally with announce count
- **Simulated downloaded**: per-torrent session value, no longer derived from `uploaded × fixed ratio`
- **peer_id rotation** every 4–6h instead of fixed per session
- **UA-specific HTTP headers**: header sets vary per client family (qBittorrent / Deluge / Transmission)
- **Multi-tracker desync**: random 0.5–8s delay between duplicate announces across trackers
- **Event anomalies**: ~3% spontaneous `event=stopped` + `event=started` to simulate reconnects
- **Detectability score**: per-announce suspicion score (0–10) logged at `DEBUG` level
- New config keys: `anti_clustering`, `stagnation_probability`, `log_level`, `multi_tracker_delay_min/max`, `event_anomaly_probability`
- Added: `install.ps1` Windows installer (Task Scheduler + `certutil` CA + `-Update` flag)
- Fix: duplicate multi-tracker announces no longer accumulate double credit
- Fix: `SeedUL` label only shown for pure seeders (`DL=0`, `left=0`)
- Fix: double logging removed (stdout only, no stale `FileHandler`)
- Fix: `peer_id` now correctly rotated on `SIGHUP` hot-reload

### v1.3
- **Single port 3456** for HTTP and HTTPS (mitmproxy now required)
- New `ratio_based` mode: `reported_ul = total_downloaded x target_ratio`
- Upload reported even when `real_ul = 0`
- Gradual catch-up via `catch_up_factor` -- no announce spikes
- Gaussian variance on effective ratio -- never perfectly constant
- Triangular distribution on `seed_credit_mb` for pure seeders
- Per-torrent ratio hard cap (`max_ratio_per_torrent`)
- `install.sh --update` and `install.ps1 -Update` for one-command GitHub updates
- Added: `Dockerfile`, `docker-compose.yml`, `install.ps1`
- Multiplier mode kept as `upload_mode = multiplier` (backward compat)

### v1.2
- Dual-port: HTTP proxy (:3456) + optional mitmproxy (:8080)
- Upload multiplier with Gaussian noise and logistic S-curve
- Global ratio cooldown
- peer_id / UA / port spoofing, scrape patch, tracker whitelist/blacklist
- SIGHUP hot-reload, stats persistence

### v1.1
- Fix: info_hash binary corruption on intercept
- HTTPS via separate mitmproxy addon and port

### v1.0
- Initial HTTP-only proxy with upload multiplier
