# NewGreedy v1.1 — HTTP/HTTPS Proxy for BitTorrent Clients
**MrT0t0** / [https://github.com/Mrt0t0/NewGreedy](https://github.com/Mrt0t0/NewGreedy)

> Linux (Debian/Ubuntu) · macOS · Windows · Docker — Python 3.8+

---

## Responsible Use

NewGreedy is intended for **educational and research purposes only**.
- ✅ Use it on **content you own** or content that is **freely and legally distributed**
- ✅ Use it to study HTTP proxy mechanics, BitTorrent protocol internals, or network traffic
- ❌ **Do not use it to download or share copyrighted content without authorization**
- ❌ **Do not use it to violate the terms of service of any private tracker**

The authors take no responsibility for misuse of this software.

---

## Description

NewGreedy is an HTTP/HTTPS proxy for BitTorrent clients (GreedyTorrent-like).
It intercepts tracker announce requests and modifies `uploaded` statistics,
with advanced anti-detection (UA spoofing, coherent progression, peers spoofing),
ratio management, and persistent stats across restarts.

---

## What's new in v1.1

| Feature | Description |
|---|---|
| **User-Agent spoofing** | 3 modes : `random` · `fixed` · `passthrough` — configurable in `config.ini` |
| **Coherent progression** | Slope guard — no unrealistic upload spike between two announces |
| **Numpeers/numseeds spoofing** | Randomizes `numwant`, `num_peers`, `num_seeds` with configurable variance |
| **Config validation** | Startup check — warnings for risky values, hard stop on critical errors |
| **Stats persistence** | `stats.json` auto-saved every 60s (atomic write) — ratios survive restarts |
| **info_hash display fix** | URL-encoded binary hash decoded to readable hex in all log lines |
| **`uninstall.sh`** | Clean Linux uninstall — service, files, CA |
| **Docker support** | Dockerfile + docker-compose — standard and mitmproxy modes |

---

## Architecture

```
MODE 1 — Standard (newgreedy.py)
─────────────────────────────────────────────────────────────────────
qBittorrent ──HTTP──► newgreedy.py:3456 ──HTTP──►  tracker   ✅ modified
qBittorrent ──HTTP──► newgreedy.py:3456 ──HTTPS──► tracker   ✅ modified
qBittorrent ──HTTP──► newgreedy.py:3456 ══CONNECT►  tracker   ⚠️  tunnel only

MODE 2 — mitmproxy (newgreedy_addon.py)
─────────────────────────────────────────────────────────────────────
qBittorrent ──HTTP──► mitmdump:3456 ──HTTP──►  tracker        ✅ modified
qBittorrent ──HTTP──► mitmdump:3456 ──HTTPS──► tracker        ✅ modified (SSL inspection)
```

| Situation | Recommended mode |
|---|---|
| Trackers use `http://` | Standard (`newgreedy.py`) |
| Trackers use `https://` | **mitmproxy** (`newgreedy_addon.py`) |
| Mixed HTTP + HTTPS | **mitmproxy** (`newgreedy_addon.py`) |

---

## Files

| File | Description |
|---|---|
| `newgreedy.py` | Standalone HTTP proxy — standard mode |
| `newgreedy_addon.py` | mitmproxy addon — full HTTP+HTTPS interception |
| `config.ini` | Shared configuration for both modes |
| `install.sh` | Automated installer — Linux only |
| `uninstall.sh` | Clean uninstaller — Linux only |
| `Dockerfile` | Docker image — standard + mitmproxy modes |
| `docker-compose.yml` | Docker Compose deployment |
| `docker-entrypoint.sh` | Docker entrypoint script |
| `README.md` | This file |

---

## Features

| Feature | Description |
|---|---|
| **User-Agent Spoofing** | `random` · `fixed` · `passthrough` modes — see config reference |
| **Coherent Progression** | Slope guard: limits upload delta to prevent announce spikes |
| **Peers/Seeds Spoofing** | Randomizes `numwant`, `num_peers`, `num_seeds` with ±variance |
| **Intelligent Seeding** | Lighter multiplier after torrent completion (`left=0`) |
| **Cooldown Mode** | Reports real upload after global ratio limit is reached |
| **Randomized Multiplier** | Adds ±variance to reported values |
| **Upload Speed Cap** | Prevents unrealistic upload spikes between announces |
| **Global Ratio Limiter** | Prevents suspiciously high ratios |
| **Config Validation** | Startup check with warnings and hard stops |
| **Stats Persistence** | Atomic `stats.json` auto-saved every 60s, reloaded at startup |
| **info_hash hex display** | Correct hex display in all log lines |
| **HTTPS Tracker Support** | Both modes support HTTPS tracker forwarding |
| **Full SSL Inspection** | mitmproxy mode intercepts and modifies HTTPS announces |
| **Auto Certificate** | Generates self-signed cert automatically if missing |
| **TLS 1.2 minimum** | Enforced on HTTPS proxy listener |
| **Dual Logging** | Console + file logging (UTF-8) |
| **Multi-threaded** | Handles concurrent clients |
| **Auto Update Check** | Notifies of new GitHub releases at startup |
| **Docker support** | Dockerfile + docker-compose, standard and mitmproxy modes |

---

## Requirements

- Python 3.8+
- `requests` — `pip install requests`
- `openssl` — optional, for auto certificate generation
- `mitmproxy` — optional, required for mitmproxy mode (`pip install mitmproxy`)
- Docker — optional

---

## 🐧 Linux — Quick Start

### Standard mode (HTTP trackers)

```bash
cd /tmp
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
chmod +x install.sh
sudo ./install.sh
```

### mitmproxy mode (HTTPS trackers) — recommended

```bash
sudo ./install.sh --mitmproxy
```

### Uninstall

```bash
chmod +x uninstall.sh
sudo ./uninstall.sh
```

### Linux — HTTPS CA setup (mitmproxy mode)

```bash
cp ~/.mitmproxy/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/mitmproxy.crt
update-ca-certificates
systemctl restart newgreedy.service
```

---

## 🍎 macOS — Quick Start

### 1. Install dependencies

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python git
pip3 install requests
# For mitmproxy mode (HTTPS trackers):
pip3 install mitmproxy
```

### 2. Clone and run

```bash
cd ~
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
```

**Standard mode:**
```bash
python3 newgreedy.py
```

**mitmproxy mode (HTTPS trackers):**
```bash
mitmdump -p 3456 --ssl-insecure -s newgreedy_addon.py
```

### 3. Auto-start at login (optional)

Create `~/Library/LaunchAgents/com.newgreedy.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.newgreedy</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/YOUR_USERNAME/NewGreedy/newgreedy.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/YOUR_USERNAME/NewGreedy</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key>
    <string>/Users/YOUR_USERNAME/NewGreedy/newgreedy.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/YOUR_USERNAME/NewGreedy/newgreedy.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.newgreedy.plist
```

### 4. macOS — HTTPS CA setup (mitmproxy mode)

```bash
mitmdump -p 3456 --ssl-insecure -s newgreedy_addon.py  # Ctrl+C after start
sudo security add-trusted-cert -d -r trustRoot \
    -k /Library/Keychains/System.keychain \
    ~/.mitmproxy/mitmproxy-ca-cert.pem
```

---

## 🪟 Windows — Quick Start

### 1. Install dependencies

1. Download **Python 3.8+** from [python.org](https://www.python.org/downloads/) — ✅ check **"Add Python to PATH"**
2. Download **Git** from [git-scm.com](https://git-scm.com/download/win)
3. Open **Command Prompt** and run:

```cmd
pip install requests
:: For mitmproxy mode:
pip install mitmproxy
```

### 2. Clone and run

```cmd
cd %USERPROFILE%
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
```

**Standard mode:**
```cmd
python newgreedy.py
```

**mitmproxy mode:**
```cmd
mitmdump -p 3456 --ssl-insecure -s newgreedy_addon.py
```

### 3. Auto-start at login (optional)

Create `start_newgreedy.bat`:
```bat
@echo off
cd /d %USERPROFILE%\NewGreedy
start /min python newgreedy.py
```
Place a shortcut in `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`

### 4. Windows — HTTPS CA setup (mitmproxy mode)

```cmd
mitmdump -p 3456 --ssl-insecure -s newgreedy_addon.py
```
Then:
1. Open `%USERPROFILE%\.mitmproxy\`
2. Double-click `mitmproxy-ca-cert.p12`
3. Install → **Local Machine** → **Trusted Root Certification Authorities**

---

## 🐳 Docker — Quick Start

### Standard mode

```bash
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy

docker build -t newgreedy .

docker run -d \
  --name newgreedy \
  -p 3456:3456 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/config.ini:/app/config.ini:ro \
  newgreedy
```

### mitmproxy mode (HTTPS trackers)

```bash
docker run -d \
  --name newgreedy-mitm \
  -p 3456:3456 \
  -v $(pwd)/data:/app/data \
  -v $(pwd)/config.ini:/app/config.ini:ro \
  newgreedy --mitmproxy
```

### Docker Compose

```bash
# Standard mode (default)
docker compose up -d

# mitmproxy mode: uncomment the command line in docker-compose.yml, then:
docker compose up -d
```

### Docker — HTTPS CA setup (mitmproxy mode)

```bash
# Extract the CA from the container after first run
docker cp newgreedy-mitm:/root/.mitmproxy/mitmproxy-ca-cert.pem ./mitmproxy-ca.pem
```
Then install `mitmproxy-ca.pem` on the host using the OS method above.

### Docker — Useful commands

```bash
docker logs -f newgreedy          # Live logs
docker ps                          # Status
docker compose down && docker compose up -d  # Restart

# Update
git pull origin main
docker compose build --no-cache
docker compose up -d
```

> **Note — qBittorrent in Docker:** if qBittorrent runs in the same Docker network,
> use the container name instead of `127.0.0.1`:
> ```
> Host: newgreedy   Port: 3456
> ```

---

## Configure qBittorrent (all platforms)

```
Tools → Options → Connection → Proxy Server
Type  : HTTP
Host  : 127.0.0.1   (or container name if using Docker)
Port  : 3456
```

> ✅ Enable  : **Use proxy for tracker connections**
> ❌ Disable : **Use proxy for peer connections**

---

## Configuration Reference

| Key | Default | Description |
|---|---|---|
| `listen_port` | `3456` | Local port the proxy listens on |
| `tracker_timeout` | `5` | Tracker request timeout in seconds |
| `max_upload_multiplier` | `1.6` | Upload multiplier while downloading |
| `seeding_multiplier` | `1.2` | Upload multiplier while seeding |
| `randomization_factor` | `0.25` | Random variance ±% applied to multiplier |
| `max_simulated_speed_mbps` | `7.6` | Max simulated upload speed (Mbps) |
| `global_ratio_limit` | `1.8` | Ratio threshold before cooldown |
| `cooldown_duration_minutes` | `10` | Cooldown duration in minutes |
| `max_upload_slope` | `2.0` | Max upload delta / download delta ratio per announce |
| `spoof_user_agent` | `true` | Enable User-Agent spoofing |
| `user_agent_mode` | `random` | UA mode: `random` \| `fixed` \| `passthrough` |
| `user_agent_value` | `qBittorrent/4.6.7` | UA string used when `user_agent_mode = fixed` |
| `spoof_peers` | `true` | Randomize numwant/num_peers/num_seeds |
| `peer_variance` | `0.15` | Variance ±% applied to peer counts |
| `enable_https` | `false` | Enable HTTPS on proxy listener (standard mode) |
| `ssl_certfile` | `cert.pem` | SSL certificate path |
| `ssl_keyfile` | `key.pem` | SSL private key path |
| `ssl_autogenerate_cert` | `true` | Auto-generate self-signed cert if missing |
| `ssl_verify_trackers` | `true` | Verify SSL cert of remote trackers |
| `persist_stats` | `true` | Enable stats persistence to JSON |
| `stats_file` | `stats.json` | Stats file path |
| `log_file` | `newgreedy.log` | Log file path |

### User-Agent modes

| Mode | Behaviour |
|---|---|
| `random` | Keeps original UA if it's a known client, otherwise picks randomly from built-in list |
| `fixed` | Always sends the value defined in `user_agent_value` |
| `passthrough` | Forwards original UA unchanged — no modification |

> **Recommendation:** use `fixed` with your actual qBittorrent version for maximum consistency between announces.

---

## Monitoring

```bash
# Live log (Linux/macOS)
tail -f newgreedy.log

# Live log (Windows PowerShell)
Get-Content newgreedy.log -Wait

# Docker
docker logs -f newgreedy

# Service status (Linux)
systemctl status newgreedy.service

# Count intercepted announces
grep -c "DOWNLOADING\|SEEDING\|COOLDOWN" newgreedy.log

# Check warnings
grep "WARNING\|ERROR" newgreedy.log | head -20
```

Expected log output:
```
[INFO] Config validation OK — ua_mode=fixed
[INFO] Stats loaded from stats.json (3 torrents)
[INFO] HTTP proxy listening on port 3456
[DOWNLOADING ] 866274e7 | DL:    12.45 MB | Real UL:     0.00 MB | Reported UL:    19.92 MB | Mul: 1.600
[SEEDING     ] def67890 | DL:   512.00 MB | Real UL:     1.20 MB | Reported UL:   614.40 MB | Mul: 1.182
[COOLDOWN    ] ghi11223 — 240s remaining
```

---

## Update

**Linux:**
```bash
cd /opt/newgreedy && git pull origin main && systemctl restart newgreedy.service
```

**macOS / Windows:**
```bash
cd ~/NewGreedy   # or cd %USERPROFILE%\NewGreedy
git pull origin main
# Restart the proxy manually or via your auto-start method
```

**Docker:**
```bash
git pull origin main
docker compose build --no-cache && docker compose up -d
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `fatal: detected dubious ownership` | `git config --global --add safe.directory /opt/newgreedy` |
| Service crashes immediately | `journalctl -u newgreedy.service -n 30 --no-pager` |
| `mitmdump: command not found` | `pip install mitmproxy` |
| No log lines after torrent starts | Check qBittorrent proxy: HTTP / 127.0.0.1 / 3456 |
| `operation canceled` in qBittorrent | Switch to mitmproxy mode — tracker is HTTPS-only |
| `Connection reset by peer` | Switch to mitmproxy mode — CONNECT tunnel cannot modify HTTPS |
| Python not found (Windows) | Reinstall Python and check "Add to PATH" |
| Port 3456 already in use | Change `listen_port` in `config.ini` |
| Config validation ERROR at boot | Check `tracker_timeout` ≥ 1 in `config.ini` |
| Stats reset after restart | Ensure `persist_stats = true` and `stats_file` is writable |
| Hash displayed as `0/0000p.0L` | Update to v1.1 — info_hash display fix included |
| Docker: proxy unreachable from qBittorrent | Check both containers share the same Docker network |
| Docker: CA not trusted | Copy CA from container and install on host (see Docker CA setup) |

---

## Changelog

### v1.1
- **User-Agent modes** — `random` \| `fixed` \| `passthrough` configurable in `config.ini`
- **Coherent upload progression** — slope guard (`max_upload_slope`)
- **Numpeers/numseeds spoofing** — `spoof_peers`, `peer_variance`
- **Config validation** — startup warnings + hard stop on critical values
- **Stats persistence** — atomic `stats.json` (write via `.tmp` + rename, no corruption on crash)
- **info_hash display fix** — URL-encoded binary hash decoded to readable 40-char hex in logs
- **`uninstall.sh`** — clean Linux uninstall
- **Docker support** — Dockerfile + docker-compose, standard and mitmproxy modes
- **TLS 1.2 minimum** enforced on HTTPS proxy listener
- **Granular error handling** — SSLError / ConnectionError / Timeout logged separately
- **Hop-by-hop headers** stripped before forwarding

### v1.0
- Full HTTPS support — proxy listener (TLS 1.2+) and tracker forwarding
- `newgreedy_addon.py` — mitmproxy addon for full HTTPS announce interception
- `install.sh --mitmproxy` — automated mitmproxy mode setup (Linux)
- macOS and Windows manual setup support
- `do_CONNECT` handler — TCP tunnel for HTTPS tracker connections (standard mode)
- Automatic self-signed certificate generation
- Service runs as root on Linux
- `compute_reported_upload()` shared function between both modes
