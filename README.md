<div align="center">

# :green_circle: NewGreedy

### BitTorrent announce proxy -- Upload ratio spoofer

[![Version](https://img.shields.io/badge/version-1.3-blue?style=flat-square)]()
[![Python](https://img.shields.io/badge/python-3.9%2B-green?style=flat-square)]()
[![License](https://img.shields.io/badge/license-MIT-gray?style=flat-square)]()
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
total_downloaded  (cumulated)
        |
        x target_ratio  (+ Gaussian variance)
        |
        = target_ul_total
        |
        - already_reported
        |
        = delta_needed
        |
        x catch_up_factor (default 15%)     <- gradual ramp, no spike
        |
        min(result, max_speed_mbps x interval)  <- speed cap
        max(result, real_ul)                    <- never below real
        +/- Gaussian noise (upload_noise_pct)   <- natural variation
        |
        if per-torrent ratio >= max_ratio: reported = real_ul
        |
        -----> sent to tracker
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

; Generate a peer_id consistent with the spoofed User-Agent.
; A mismatched peer_id and UA is a common detection vector.
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
```

</details>

---

## :bar_chart: Reading the logs

```
[17:30:12] [DOWNLOADING] 6217ba79 | DL: 200.00MB | RealUL: 0.00MB | RepUL: 4.52MB | Ratio:0.022 | Ann#1
[18:00:14] [SEEDING    ] 6217ba79 | DL:   0.00MB | RealUL: 0.00MB | RepUL: 4.80MB | Ratio:0.312 | Ann#8
```

| Field | Meaning |
|---|---|
| `DL` | Real downloaded this announce cycle |
| `RealUL` | Real uploaded this announce cycle |
| `RepUL` | What NewGreedy reported to the tracker |
| `Ratio` | Cumulative reported ratio for this torrent |
| `Ann#N` | Total announce count for this torrent |

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

### v1.3 -- Current
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
