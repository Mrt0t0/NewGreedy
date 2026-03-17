# NewGreedy v1.0 — HTTP/HTTPS Proxy for BitTorrent Clients
**MrT0t0** / [https://github.com/Mrt0t0/NewGreedy](https://github.com/Mrt0t0/NewGreedy)

> Linux (Debian/Ubuntu) · macOS · Windows — Python 3.8+

---

## Responsible Use

NewGreedy is intended for **educational and research purposes only**.
- ✅ Use it on **content you own** or content that is **freely and legally distributed**
- ✅ Use it to study HTTP proxy mechanics, BitTorrent protocol internals, or network traffic
- ❌ **Do not use it to download or share copyrighted content without authorization**
- ❌ **Do not use it to violate the terms of service of any private tracker**

The authors take no responsibility for misuse of this software.
Always comply with the laws of your country and the rules of the platforms you use.

---

## Description

NewGreedy is an HTTP/HTTPS proxy for BitTorrent clients (GreedyTorrent-like).
It intercepts tracker announce requests and modifies the `uploaded` statistic to simulate
realistic upload ratios, with built-in anti-detection mechanisms (randomization, speed cap,
cooldown, ratio limiter).

Use it with any torrent client that supports an HTTP proxy (e.g. qBittorrent).

---

## Architecture

```
MODE 1 — Standard (newgreedy.py)
─────────────────────────────────────────────────────────────────────
qBittorrent ──HTTP──► newgreedy.py:3456 ──HTTP──►  tracker   ✅ uploaded modified
qBittorrent ──HTTP──► newgreedy.py:3456 ──HTTPS──► tracker   ✅ uploaded modified
qBittorrent ──HTTP──► newgreedy.py:3456 ══CONNECT►  tracker   ⚠️  tunnel only, NOT modified

MODE 2 — mitmproxy (newgreedy_addon.py)
─────────────────────────────────────────────────────────────────────
qBittorrent ──HTTP──► mitmdump:3456 ──HTTP──►  tracker        ✅ uploaded modified
qBittorrent ──HTTP──► mitmdump:3456 ──HTTPS──► tracker        ✅ uploaded modified (SSL inspection)
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
| `README.md` | This file |

---

## Features

| Feature | Description |
|---|---|
| **Intelligent Seeding** | Detects torrent completion (`left=0`), applies lighter seeding multiplier |
| **Cooldown Mode** | Reports real upload after global ratio limit is reached |
| **Randomized Multiplier** | Adds ±variance to reported values — breaks statistical patterns |
| **Upload Speed Cap** | Prevents unrealistic upload spikes between announces |
| **Global Ratio Limiter** | Prevents suspiciously high ratios |
| **HTTPS Tracker Support** | Both modes support HTTPS tracker forwarding |
| **Full SSL Inspection** | mitmproxy mode intercepts and modifies HTTPS announces |
| **Auto Certificate** | Generates self-signed cert automatically if missing (openssl) |
| **Dual Logging** | Console + file logging |
| **Multi-threaded** | Handles concurrent clients |
| **Auto Update Check** | Notifies of new GitHub releases at startup |

---

## Requirements

- Python 3.8+
- `requests` — `pip install requests`
- `openssl` — optional, for auto certificate generation
- `mitmproxy` — optional, required for mitmproxy mode (`pip install mitmproxy`)

---

## 🐧 Linux — Quick Start

> The service runs as **root** — required on minimal systems (containers, seedboxes)
> where pip libraries are only accessible to root.

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
cd /tmp
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
chmod +x install.sh
sudo ./install.sh --mitmproxy
```

### Linux — HTTPS CA setup (mitmproxy mode)

```bash
# CA is auto-generated at first launch — install it system-wide:
cp ~/.mitmproxy/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/mitmproxy.crt
update-ca-certificates
systemctl restart newgreedy.service
```

---

## 🍎 macOS — Quick Start

### 1. Install dependencies

```bash
# Install Homebrew if not already installed
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

Create a launchd service `~/Library/LaunchAgents/com.newgreedy.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.newgreedy</string>
    <key>ProgramArguments</key>
    <array>
        <!-- Standard mode: -->
        <string>/usr/bin/python3</string>
        <string>/Users/YOUR_USERNAME/NewGreedy/newgreedy.py</string>
        <!-- mitmproxy mode: replace above two lines with: -->
        <!-- <string>/usr/local/bin/mitmdump</string> -->
        <!-- <string>-p</string><string>3456</string> -->
        <!-- <string>--ssl-insecure</string> -->
        <!-- <string>-s</string> -->
        <!-- <string>/Users/YOUR_USERNAME/NewGreedy/newgreedy_addon.py</string> -->
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/YOUR_USERNAME/NewGreedy</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/YOUR_USERNAME/NewGreedy/newgreedy.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/YOUR_USERNAME/NewGreedy/newgreedy.log</string>
</dict>
</plist>
```

```bash
# Replace YOUR_USERNAME then load:
launchctl load ~/Library/LaunchAgents/com.newgreedy.plist
```

### 4. macOS — HTTPS CA setup (mitmproxy mode)

```bash
# Run mitmdump once to generate the CA, then Ctrl+C
mitmdump -p 3456 --ssl-insecure -s newgreedy_addon.py

# Trust the CA in macOS Keychain
sudo security add-trusted-cert -d -r trustRoot     -k /Library/Keychains/System.keychain     ~/.mitmproxy/mitmproxy-ca-cert.pem
```

---

## 🪟 Windows — Quick Start

### 1. Install dependencies

1. Download and install **Python 3.8+** from [python.org](https://www.python.org/downloads/)
   - ✅ Check **"Add Python to PATH"** during installation
2. Open **Command Prompt** (`Win+R` → `cmd`) and run:

```cmd
pip install requests
:: For mitmproxy mode (HTTPS trackers):
pip install mitmproxy
```

3. Download and install **Git** from [git-scm.com](https://git-scm.com/download/win)

### 2. Clone and run

Open **Command Prompt** or **PowerShell**:

```cmd
cd %USERPROFILE%
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
```

**Standard mode:**
```cmd
python newgreedy.py
```

**mitmproxy mode (HTTPS trackers):**
```cmd
mitmdump -p 3456 --ssl-insecure -s newgreedy_addon.py
```

Leave the window open — the proxy runs in the foreground.

### 3. Auto-start at login (optional)

Create a file `start_newgreedy.bat`:

```bat
@echo off
:: Standard mode:
cd /d %USERPROFILE%\NewGreedy
start /min python newgreedy.py

:: mitmproxy mode — comment out above and uncomment below:
:: start /min mitmdump -p 3456 --ssl-insecure -s newgreedy_addon.py
```

Place a shortcut to `start_newgreedy.bat` in:
```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
```

### 4. Windows — HTTPS CA setup (mitmproxy mode)

```cmd
:: Run mitmdump once to generate the CA, then Ctrl+C
mitmdump -p 3456 --ssl-insecure -s newgreedy_addon.py
```

Then install the CA:
1. Open `%USERPROFILE%\.mitmproxy\`
2. Double-click `mitmproxy-ca-cert.p12`
3. Install into **"Local Machine"** → **"Trusted Root Certification Authorities"**

---

## Configure qBittorrent (all platforms)

```
Tools → Options → Connection → Proxy Server
Type  : HTTP
Host  : 127.0.0.1
Port  : 3456
```

> ✅ Enable  : **Use proxy for tracker connections**
> ❌ Disable : **Use proxy for peer connections**

---

## Configuration Reference

| Key | Default | Description |
|---|---|---|
| `listen_port` | `3456` | Local port the proxy listens on |
| `max_upload_multiplier` | `1.6` | Upload multiplier while downloading |
| `seeding_multiplier` | `1.2` | Upload multiplier while seeding |
| `randomization_factor` | `0.25` | Random variance ±% applied to multiplier |
| `max_simulated_speed_mbps` | `7.6` | Max simulated upload speed (Mbps) |
| `global_ratio_limit` | `1.8` | Ratio threshold before entering cooldown |
| `cooldown_duration_minutes` | `10` | Cooldown duration in minutes |
| `tracker_timeout` | `5` | Tracker request timeout in seconds (standard mode) |
| `enable_https` | `false` | Enable HTTPS on proxy listener (standard mode only) |
| `ssl_certfile` | `cert.pem` | SSL certificate path |
| `ssl_keyfile` | `key.pem` | SSL private key path |
| `ssl_autogenerate_cert` | `true` | Auto-generate self-signed cert if missing |
| `ssl_verify_trackers` | `true` | Verify SSL cert of remote trackers |
| `log_file` | `newgreedy.log` | Log file path |

---

## Monitoring

```bash
# Live log (Linux/macOS)
tail -f /opt/newgreedy/newgreedy.log

# Live log (Windows PowerShell)
Get-Content newgreedy.log -Wait

# Service status (Linux)
systemctl status newgreedy.service

# Count intercepted announces
grep -c "DOWNLOADING\|SEEDING\|COOLDOWN" newgreedy.log

# Check for errors
grep "ERROR\|WARNING" newgreedy.log | tail -20
```

Expected log output:
```
[DOWNLOADING] tracker.example.com | DL: 12.45 MB | Real UL: 0.00 MB | Reported UL: 19.92 MB | Protocol: HTTPS
[SEEDING]     tracker.example.com | DL: 512.00 MB | Real UL: 1.20 MB | Reported UL: 614.40 MB | Protocol: HTTPS
[COOLDOWN]    tracker.example.com | DL: 800.00 MB | Real UL: 5.00 MB | Reported UL: 5.00 MB   | Protocol: HTTPS
```

---

## Update

**Linux:**
```bash
cd /opt/newgreedy && git pull origin main && systemctl restart newgreedy.service
```

**macOS / Windows:**
```bash
cd ~/NewGreedy   # or cd %USERPROFILE%\NewGreedy on Windows
git pull origin main
# Then restart the proxy manually or via your auto-start method
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `fatal: detected dubious ownership` (Linux) | `git config --global --add safe.directory /opt/newgreedy` |
| Service crashes immediately (Linux) | `journalctl -u newgreedy.service -n 30 --no-pager` |
| `mitmdump: command not found` | `pip install mitmproxy` |
| No log lines after torrent starts | Check qBittorrent proxy: HTTP / 127.0.0.1 / 3456 |
| `operation canceled` in qBittorrent | Switch to mitmproxy mode — tracker is HTTPS-only |
| `Connection reset by peer` | Switch to mitmproxy mode — CONNECT tunnel cannot modify HTTPS |
| Python not found (Windows) | Reinstall Python and check "Add to PATH" |
| Port 3456 already in use | Change `listen_port` in `config.ini` |

---

## Changelog

### v1.0
- Full HTTPS support — proxy listener (TLS 1.2+) and tracker forwarding
- `newgreedy_addon.py` — mitmproxy addon for full HTTPS announce interception
- `install.sh --mitmproxy` — automated mitmproxy mode setup (Linux)
- macOS and Windows manual setup support
- `do_CONNECT` handler — TCP tunnel for HTTPS tracker connections (standard mode)
- `tracker_timeout` — configurable via config.ini
- Automatic self-signed certificate generation via openssl
- Service runs as root on Linux — compatible with pip system-wide installs
- `git config safe.directory` auto-added during install
- `compute_reported_upload()` extracted as shared function between both modes
- Protocol field added to all announce log lines
