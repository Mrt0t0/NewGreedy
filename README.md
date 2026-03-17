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

Use it with any torrent client that supports an HTTP proxy (e.g. qBittorrent).

---

## Features

| Feature | Description |
|---|---|
| **Intelligent Seeding** | Detects torrent completion (`left=0`) and switches to a lighter, configurable seeding multiplier |
| **Cooldown Mode** | Temporarily reports real upload values after the global ratio limit is reached |
| **Randomized Multiplier** | Adds realistic variance to reported upload values to break statistical patterns |
| **Upload Speed Cap** | Restricts the maximum reported upload speed to avoid unrealistic spikes |
| **Global Ratio Limiter** | Prevents suspiciously high ratios that could trigger tracker detection |
| **HTTPS Server** | Proxy can listen over HTTPS with TLS 1.2+ enforcement |
| **HTTPS Tracker Support** | Forwards requests to HTTPS trackers with configurable SSL verification |
| **Auto Certificate** | Generates a self-signed certificate automatically if none is found |
| **Dual Logging** | Logs activity concurrently to console and file |
| **Multi-threaded** | Handles multiple simultaneous client requests efficiently |
| **Auto Update Check** | Notifies users of new GitHub releases at startup |

---

## Dependencies

- Python 3.8+
- `requests` library
- `openssl` (optional — required for automatic self-signed certificate generation)

```bash
pip install requests
```

---

## Configuration (`config.ini`)

| Key | Default | Description |
|---|---|---|
| `listen_port` | `3456` | Local port the proxy listens on |
| `max_upload_multiplier` | `1.6` | Multiplier applied to reported upload while downloading |
| `seeding_multiplier` | `1.2` | Multiplier applied when the torrent is complete (`left=0`) |
| `randomization_factor` | `0.25` | Random variance on the multiplier (e.g. `0.25` = ±25%) |
| `max_simulated_speed_mbps` | `7.6` | Maximum simulated upload speed in Mbps |
| `global_ratio_limit` | `1.8` | Global ratio threshold before entering cooldown |
| `cooldown_duration_minutes` | `10` | Duration of the cooldown period in minutes |
| `enable_https` | `false` | Enable HTTPS on the proxy listener |
| `ssl_certfile` | `cert.pem` | Path to the SSL certificate |
| `ssl_keyfile` | `key.pem` | Path to the SSL private key |
| `ssl_autogenerate_cert` | `true` | Auto-generate a self-signed cert if missing (dev only) |
| `ssl_verify_trackers` | `true` | Verify SSL certificates of remote trackers |
| `log_file` | `newgreedy.log` | Path to the persistent log file |

---

## Installation & Usage

### 1. Clone the repository

```bash
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
```

### 2. Install dependencies

```bash
pip install requests
```

### 3. Customize `config.ini`

Edit `config.ini` to match your preferences before running.
See the configuration table above for all available options.

### 4. Run the installation script (Linux)

Sets up the proxy as a systemd service running under a dedicated `newgreedy` user:

```bash
chmod +x install.sh
sudo ./install.sh
```

Default installation directory: `/opt/newgreedy`

### 5. Configure your BitTorrent client

In qBittorrent (or any compatible client):

```
Tools → Options → Connection → Proxy Server
Type  : HTTP
Host  : 127.0.0.1
Port  : 3456  (or your configured listen_port)
```

Add any torrent and check the NewGreedy logs to verify interception.

### 6. Monitor the service

```bash
# Check service status
sudo systemctl status newgreedy.service

# Follow live logs
sudo journalctl -u newgreedy.service -f

# Check the log file directly
tail -f /opt/newgreedy/newgreedy.log
```

---

## HTTPS Setup

### Option A — Self-signed certificate (dev / local use)

Set in `config.ini`:
```ini
enable_https = true
ssl_autogenerate_cert = true
```

On first launch, NewGreedy calls `openssl` automatically to generate `cert.pem` and `key.pem`.

> ⚠️ Self-signed certificates are for local/development use only.
> Your BitTorrent client may require you to add a certificate exception.

### Option B — Let's Encrypt (production)

```bash
sudo certbot certonly --standalone -d your-domain.com
```

Then in `config.ini`:
```ini
enable_https       = true
ssl_certfile       = /etc/letsencrypt/live/your-domain.com/fullchain.pem
ssl_keyfile        = /etc/letsencrypt/live/your-domain.com/privkey.pem
ssl_autogenerate_cert = false
```

Auto-renewal:
```bash
# Test renewal
sudo certbot renew --dry-run

# Cron job (runs twice daily)
0 0,12 * * * /usr/bin/certbot renew --quiet
```

---

## Updating from GitHub

To update your local installation with the latest changes:

```bash
# 1. Navigate to the installation directory
cd /opt/newgreedy

# 2. Pull the latest changes
git pull origin main

# 3. Update Python dependencies if needed
python3 -m pip install --upgrade requests

# 4. Restart the service
sudo systemctl restart newgreedy.service

# 5. Verify the update
sudo systemctl status newgreedy.service
journalctl -u newgreedy.service -f
```

> Your `config.ini` is never overwritten by `git pull`.
> After a major update, compare your config with `config.ini.new` if present.

---

## Changelog

### v1.0
- Full HTTPS support — proxy listener and tracker forwarding
- Automatic self-signed certificate generation via `openssl`
- TLS 1.2 minimum enforced on all SSL connections
- Dedicated `newgreedy` system user in `install.sh` (no longer runs as root)
- `allow_reuse_address` on server socket — no more restart errors
- Pre-compiled regex patterns for improved request handling performance
- `time.monotonic()` replaces `time.time()` for reliable time deltas
- Thread-safe cooldown state with dedicated lock
- Graceful startup failure with clear log message if HTTPS cert is missing
- Detailed SSL error reporting with actionable guidance
