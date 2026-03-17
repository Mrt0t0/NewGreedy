# NewGreedy v1.0 — HTTP/HTTPS Proxy for BitTorrent Clients
**MrT0t0** / [https://github.com/Mrt0t0/NewGreedy](https://github.com/Mrt0t0/NewGreedy)

Cross-Platform Support (Windows / macOS / Linux)

---

## Responsible Use

NewGreedy is intended for **educational and research purposes only**.
- ✅ Use it on **content you own** or content that is **freely and legally distributed**.
- ✅ Use it to study HTTP proxy mechanics, BitTorrent protocol internals, or network traffic.
- ❌ **Do not use it to download or share copyrighted content without authorization.**
- ❌ **Do not use it to violate the terms of service of any private tracker.**

The authors take no responsibility for misuse of this software.
Always comply with the laws of your country and the rules of the platforms you use.

## Description

NewGreedy is an HTTP/HTTPS proxy for BitTorrent clients (GreedyTorrent-like).
It intercepts tracker "announce" requests and intelligently modifies the `uploaded` statistic
to simulate realistic upload ratios while avoiding tracker detection.

HTTPS support (server-side and tracker-side), automatic self-signed certificate
generation.

Use it with any torrent client that supports an HTTP/HTTPS proxy (e.g. qBittorrent).

---
## Architecture

NewGreedy offers two operating modes depending on whether your trackers use HTTP or HTTPS:

```
MODE 1 — Standard (newgreedy.py)
─────────────────────────────────────────────────────────────────
qBittorrent ──HTTP──► newgreedy.py:3456 ──HTTP──► tracker        ✅ uploaded modified
qBittorrent ──HTTP──► newgreedy.py:3456 ──HTTPS──► tracker       ✅ uploaded modified
qBittorrent ──HTTP──► newgreedy.py:3456 ══CONNECT══► tracker     ⚠️  tunnel only, NOT modified

MODE 2 — mitmproxy (newgreedy_addon.py)
─────────────────────────────────────────────────────────────────
qBittorrent ──HTTP──► mitmdump:3456 ──HTTP──► tracker            ✅ uploaded modified
qBittorrent ──HTTP──► mitmdump:3456 ──HTTPS──► tracker           ✅ uploaded modified (SSL inspection)
```

| Situation | Recommended mode |
|---|---|
| Trackers use `http://` | Standard (`newgreedy.py`) |
| Trackers use `https://` | mitmproxy (`newgreedy_addon.py`) |
| Mixed HTTP + HTTPS | mitmproxy (`newgreedy_addon.py`) |

---

## Files

| File | Description |
|---|---|
| `newgreedy.py` | Standalone HTTP proxy — standard mode |
| `newgreedy_addon.py` | mitmproxy addon — full HTTP+HTTPS interception |
| `config.ini` | Shared configuration for both modes |
| `install.sh` | Automated installer — supports both modes |
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
| **Auto Certificate** | Generates self-signed cert automatically if missing |
| **Dual Logging** | Console + file logging |
| **Multi-threaded** | Handles concurrent clients |
| **Auto Update Check** | Notifies of new GitHub releases at startup |

---

## Requirements

- Python 3.8+
- `requests` (`pip install requests`)
- `openssl` — optional, for auto self-signed certificate generation (apt install openssl)
- `mitmproxy` — optional, required for mitmproxy mode (`pip install mitmproxy`)

---

## Quick Start

### Standard mode (HTTP trackers)

```bash
cd ./tmp
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
pip install requests
chmod +x install.sh
sudo ./install.sh
```

### mitmproxy mode (HTTPS trackers)

```bash
cd ./tmp
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
pip install requests mitmproxy
chmod +x install.sh
sudo ./install.sh --mitmproxy
```

---

## Configure qBittorrent

```
Tools → Options → Connection → Proxy Server
Type  : HTTP
Host  : 127.0.0.1
Port  : 3456
```

> ✅ Enable  : "Use proxy for tracker connections"
> ❌ Disable : "Use proxy for peer connections"

---

## HTTPS Setup (mitmproxy mode only)

mitmproxy performs SSL inspection — qBittorrent must trust its CA certificate:

```bash
# Step 1 — Generate the mitmproxy CA (run once, then stop with Ctrl+C)
mitmdump -p 3456 --ssl-insecure -s /opt/newgreedy/newgreedy_addon.py

# Step 2 — Install the CA system-wide
cp ~/.mitmproxy/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/mitmproxy.crt
update-ca-certificates

# Step 3 — Restart the service
systemctl restart newgreedy.service
```

---

## Configuration Reference

| Key | Default | Description |
|---|---|---|
| `listen_port` | `3456` | Local port the proxy listens on |
| `max_upload_multiplier` | `1.6` | Multiplier while downloading |
| `seeding_multiplier` | `1.2` | Multiplier while seeding |
| `randomization_factor` | `0.25` | Random variance ±% on multiplier |
| `max_simulated_speed_mbps` | `7.6` | Max simulated upload speed in Mbps |
| `global_ratio_limit` | `1.8` | Ratio threshold before cooldown |
| `cooldown_duration_minutes` | `10` | Cooldown duration in minutes |
| `tracker_timeout` | `5` | Tracker request timeout in seconds |
| `enable_https` | `false` | Enable HTTPS on proxy listener (standard mode) |
| `ssl_certfile` | `cert.pem` | SSL certificate path |
| `ssl_keyfile` | `key.pem` | SSL private key path |
| `ssl_autogenerate_cert` | `true` | Auto-generate cert if missing |
| `ssl_verify_trackers` | `true` | Verify SSL on remote trackers |
| `log_file` | `newgreedy.log` | Log file path |

---

## Monitoring

```bash
# Live log
tail -f /opt/newgreedy/newgreedy.log

# Service status
systemctl status newgreedy.service

# Count intercepted announces
grep -c "DOWNLOADING\|SEEDING\|COOLDOWN" /opt/newgreedy/newgreedy.log

# Check for errors
grep "ERROR\|WARNING" /opt/newgreedy/newgreedy.log | tail -20
```

Expected log output:
```
[DOWNLOADING] tracker.example.com | DL: 12.45 MB | Real UL: 0.00 MB | Reported UL: 19.92 MB | Protocol: HTTPS
[SEEDING]     tracker.example.com | DL: 512.00 MB | Real UL: 1.20 MB | Reported UL: 614.40 MB | Protocol: HTTPS
[COOLDOWN]    tracker.example.com | DL: 800.00 MB | Real UL: 5.00 MB | Reported UL: 5.00 MB | Protocol: HTTPS
```

---

## Update

```bash
cd /opt/newgreedy && git pull origin main && systemctl restart newgreedy.service
```
