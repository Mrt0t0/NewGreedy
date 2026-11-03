<div align="center">

# NewGreedy

### BitTorrent announce proxy — Upload ratio spoofer

[![Version](https://img.shields.io/badge/version-1.7.8-blue?style=flat-square)](https://github.com/Mrt0t0/NewGreedy/releases)
[![Python](https://img.shields.io/badge/python-3.9%2B-green?style=flat-square)](https://python.org)
[![License](https://img.shields.io/badge/license-GPLv3-gray?style=flat-square)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20Windows%20%7C%20Docker-lightgray?style=flat-square)](https://github.com/Mrt0t0/NewGreedy)

</div>

---

> **Disclaimer** — NewGreedy is provided for **educational and research purposes only**.
> Using this tool on private trackers may violate their terms of service and result in a permanent ban.
> The author assumes no responsibility for any misuse. **Use at your own risk.**

---

## Contents

- [What does NewGreedy do?](#what-does-newgreedy-do)
- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation) — [Linux](#linux) · [macOS](#macos) · [Windows](#windows) · [Docker](#docker)
- [Updating](#updating)
- [Configure your torrent client](#configure-your-torrent-client)
- [Configuration reference](#configuration-reference)
- [Web UI](#web-ui)
- [REST API](#rest-api)
- [Reading the logs](#reading-the-logs)
- [Troubleshooting](#troubleshooting)
- [Files](#files)
- [License](#license)
- [Changelog](#changelog)

---

## What does NewGreedy do?

NewGreedy is a **local HTTP/HTTPS proxy** that sits between your torrent client and the tracker.
Every time your client sends an announce, NewGreedy intercepts it and rewrites the `uploaded`, `downloaded`, `left`, `peer_id` and `port` fields before forwarding the request, as one consistent client that behaves the way the real one would.

Your torrent client and the files on disk are **never touched**. Only the statistics reported to trackers change.

---

## Features

**Ratio engine**
- Brings each torrent to `target_ratio` with a non-linear catch-up, random noise and deliberate pauses, then stops
- Caps per torrent (`max_ratio_per_torrent`) and per tracker across all torrents (`max_global_ratio_per_tracker`)
- Credits upload to pure seeders, and holds back when the tracker reports no leecher
- One upload budget shared by every torrent, with active hours and a smooth day/night curve

**Looking like a real client**
- One identity per session — peer_id, port, `key`, User-Agent, headers and parameter order of a real qBittorrent, Transmission, Deluge, libtorrent or rTorrent build
- Carried across restarts the way a real install behaves: same client, fresh peer_id, occasional version upgrade
- Announces spread per torrent, a gradual start after a restart, tracker intervals only ever lengthened
- `downloaded` and `left` always consistent with the torrent size; `trackerid` returned; scrapes forwarded

**Control**
- Pause, ratio override and tags per torrent, applied within seconds without a restart
- `[tracker:domain]` sections to set different rules for one tracker, kept across restarts
- Watch folder: drop `.torrent` files to register their name, size and trackers
- Settings applied live: `config.ini` is reloaded by the running engine within 5 s
- Purged torrents go to a trash and can be restored for `trash_retention_days`
- Dry-run mode that logs every decision without changing a single announce

**Web UI and integrations**
- Dashboard, sortable torrents table with per-torrent actions, ratio charts, live logs and settings editor
- Status page: self-check, engine state, live "announce received" test and client setup guide
- REST API and Prometheus `/metrics`
- Webhooks to Discord, Slack, ntfy, Gotify, Telegram or any JSON endpoint, with a test button
- Upstream proxy such as Gluetun; access token with sign-in page, hidden path and rate limiting

**Operations**
- Installers for Linux (systemd, dedicated service account, venv), macOS (launchd) and Windows (Task Scheduler), plus a Docker image
- Update from the command line or in one click from the web UI — release notes shown first, checksum required, settings merged, data kept
- Automatic rotating backups, restore in one click, atomic writes, log rotation, clean flush on shutdown
- `--check-config` self-check; the engine fails closed instead of letting announces through unmodified
- UDP trackers detected and flagged, since their announces bypass the proxy

---

## How it works

```
Torrent client  ──► NewGreedy :3456 (proxy)  ──► Tracker
                         │
                         ├─ Intercepts announces (HTTP + HTTPS via mitmproxy)
                         ├─ Rewrites: uploaded / downloaded / left / peer_id / port
                         │            numwant / key / trackerid
                         ├─ Applies ratio engine (non-linear progression, noise, stagnation)
                         ├─ Guards per-tracker global ratio cap
                         ├─ Keeps downloaded + left = size, and left falling at a real speed
                         ├─ Spreads announce timing across torrents (see below)
                         └─ Forwards patched request to tracker
```

**Announce timing:**
```
tracker interval  ──► + per-torrent offset (stable, derived from infohash + session)
                      + slow hourly drift
                      + extra spread when several torrents cluster on one tracker
                      ──► rewritten in the tracker reply, later only, never earlier
```

Torrents that start together at launch would otherwise keep announcing together forever.
Each one gets its own period, so the group fans out over the first hours and the tracker
sees a natural, uncorrelated stream instead of a repeating burst.

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
| Python | 3.10+ (3.12 or newer for mitmproxy 12) |
| mitmproxy | 10.0 to 12.x — on Python 3.14, only mitmproxy 12 installs |
| fastapi | 0.110+ |
| uvicorn | 0.27+ |
| python-multipart | 0.0.9+ |

`bcrypt` is no longer pinned. With mitmproxy 10 or 11, **bcrypt 5.x** breaks the passlib they use: remove it — see [Troubleshooting](#troubleshooting). mitmproxy 12 needs bcrypt 5 itself and no longer uses passlib.

---

## Installation

### Linux

**1. Clone the repository**

```bash
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
```

**2. Run the installer**

Requires `sudo` — installs to `/opt/newgreedy` and registers a systemd service.

```bash
sudo bash install.sh
```

The installer:
- Creates a system account `newgreedy` — the service no longer runs as root
- Installs Python dependencies in a virtual environment, `/opt/newgreedy/venv` (falls back to the system Python when `python3-venv` is missing)
- Copies all files to `/opt/newgreedy/`
- Generates the mitmproxy CA in `/var/lib/newgreedy/mitmproxy` — key readable by `newgreedy` only. It is **not** added to the system trust store unless you pass `--trust-ca` (see step 4)
- Merges an existing `config.ini` instead of replacing it, whether or not `--update` is given
- Creates and enables a `systemd` service (`newgreedy.service`) that starts at boot

**3. Verify**

```bash
systemctl status newgreedy
sudo -u newgreedy NEWGREEDY_MITM_CONFDIR=/var/lib/newgreedy/mitmproxy \
  /opt/newgreedy/venv/bin/python /opt/newgreedy/newgreedy.py --check-config
xdg-open http://localhost:8080
```

The **Status** page runs the same checks while the service is up.

**4. Let the torrent client trust the CA (HTTPS trackers)**

A CA trusted system-wide lets whoever gets its private key intercept every TLS connection of the machine, so the installer no longer does it by default. Two ways:

- **Only the torrent client** (recommended): the installer writes `/var/lib/newgreedy/client-ca-bundle.pem` (system CAs + the NewGreedy CA). Start the client with
  `SSL_CERT_FILE=/var/lib/newgreedy/client-ca-bundle.pem CURL_CA_BUNDLE=/var/lib/newgreedy/client-ca-bundle.pem qbittorrent`
  (for a systemd unit: `Environment=SSL_CERT_FILE=...`). Re-run the installer after a `ca-certificates` update to refresh the bundle.
- **The whole system**: `sudo bash install.sh --trust-ca` installs it as `/usr/local/share/ca-certificates/newgreedy-mitmproxy-ca.crt`. A CA trusted this way by an older version stays trusted on update.

Keep the cloned folder: updates are run from it — see [Updating](#updating).

**Uninstall:**
```bash
sudo bash uninstall.sh
```
Removes the service, the program, its data, the `newgreedy` account **and the NewGreedy CA from the system trust store** — only that certificate, compared byte for byte. `/root/.mitmproxy`, used by versions before v1.7.8 and by other mitmproxy tools, is left in place.

**Service management:**
```bash
systemctl start newgreedy
systemctl stop newgreedy
systemctl restart newgreedy
journalctl -u newgreedy -f
```

---

### macOS

**1. Clone and run the installer**

```bash
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
bash install-macos.sh
```

The installer:
- Checks for `python3` (install with `brew install python@3.11` if missing)
- Installs Python dependencies in `~/Library/Application Support/NewGreedy/venv`
- Copies files to `~/Library/Application Support/NewGreedy`
- Generates the mitmproxy CA; `--trust-ca` adds it to the login keychain (every program of the account then trusts it)
- Merges an existing `config.ini` instead of replacing it
- Registers a launchd agent that starts at login

**2. Verify**

Open `http://localhost:8080`. The proxy listens on `127.0.0.1:3456`.

**Manage the service:**
```bash
launchctl unload ~/Library/LaunchAgents/com.mrt0t0.newgreedy.plist   # stop
launchctl load   ~/Library/LaunchAgents/com.mrt0t0.newgreedy.plist   # start
tail -f "$HOME/Library/Application Support/NewGreedy/newgreedy.log"
```

Keep the cloned folder: updates are run from it — see [Updating](#updating).

**Uninstall:**
```bash
bash install-macos.sh --uninstall
```
Removes the launchd agent and the NewGreedy CA from the login keychain — found by its SHA-1 fingerprint, so other mitmproxy certificates stay; data is kept.

**Firefox** keeps its own certificate store — import manually:
`~/.mitmproxy/mitmproxy-ca-cert.pem`

---

### Windows

**1. Open PowerShell** (administrator rights are not needed)

**2. Run the installer**

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -TrustCA
```

The installer:
- Checks for Python and downloads it if missing (Python 3.11) — the installer only runs when Windows confirms its Python Software Foundation signature
- Installs Python dependencies
- Copies files to `%LOCALAPPDATA%\NewGreedy\`; an existing `config.ini` is merged, not replaced
- With `-TrustCA`, imports the mitmproxy CA into the user certificate store (HTTPS trackers need it)
- Registers a Scheduled Task that starts NewGreedy at logon, without administrator rights

**3. Verify**

Open `http://localhost:8080` in your browser. The proxy listens on `127.0.0.1:3456`.

**4. Trust the CA in Firefox** (if used)

Firefox → Settings → Privacy & Security → Certificates → Import
File: `%USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.p12`

Keep the folder you installed from: updates are run from it — see [Updating](#updating).

**Uninstall:**
```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall
```
Also removes the NewGreedy CA from the user certificate store — by its thumbprint, so other mitmproxy certificates stay.

---

### Docker

**1. Start the stack**

```bash
docker compose up -d
```

The image `ghcr.io/mrt0t0/newgreedy` (amd64, arm64) is pulled; without network access to the registry, Compose builds it from the folder. Everything the container writes lives in `./data/` — `config.ini`, stats, registry, trash, backups, log. Nothing needs to be created beforehand.

**2. Read the access token**

The web UI listens on every interface inside the container, so a token is generated at the first start:

```bash
docker compose logs newgreedy | grep -A2 "token was generated"
```

Open the printed URL once; the browser keeps a cookie. The token is also in `./data/config.ini` (`web_secret_token`).

**3. Export and trust the mitmproxy CA**

```bash
docker exec newgreedy cat /home/newgreedy/.mitmproxy/mitmproxy-ca-cert.pem > mitmproxy-ca.crt
```

Import `mitmproxy-ca.crt` into your OS or browser trust store, then restart your torrent client. The CA is created at the first start, in the `mitmproxy_ca` volume — never at build time.

**4. Point the torrent client at the proxy**

The proxy is published on `127.0.0.1:3456` of the host only. A client running in another container on the same Docker network uses `newgreedy:3456`.

The container runs as the unprivileged user `newgreedy` (uid 1000). Updates: see [Updating](#updating).

---

### Manual (no installer)

```bash
git clone https://github.com/Mrt0t0/NewGreedy.git
cd NewGreedy
pip install -r requirements.txt
python3 newgreedy.py --check-config   # optional self-check
python3 newgreedy.py
```

Updates: see [Updating](#updating).

---

## Updating

Every method keeps your data — `stats.json`, `torrent_registry.json`, `control.json`, `trash.json`, `torrents/` — and **merges** `config.ini`: your values stay, the settings a new version adds are inserted with their comments, and retired settings are removed.

| Install | Command-line update | Web button |
|---|---|---|
| Linux | `git pull`, then `sudo bash install.sh --update` | ✅ restarts on its own |
| macOS | `git pull`, then `bash install-macos.sh --update` | ✅ restarts on its own |
| Windows | `git pull`, then `.\install.ps1 -Update` | ✅ start it again if it does not come back |
| Docker | `docker compose pull && docker compose up -d` | ❌ disabled — the program lives in the image |
| Manual | save `config.ini`, `git pull`, merge, restart | ✅ start NewGreedy again afterwards |

**Coming from v1.7.6:** it has no update button — use the command line once.

### Coming from v1.7.7

Four defaults change. Check them before restarting:

| Setting | v1.7.7 | v1.7.8 | If you relied on the old behaviour |
|---|---|---|---|
| `[proxy] listen_host` | `0.0.0.0`, fixed | `127.0.0.1` | Torrent client on another machine: set `listen_host = 0.0.0.0` |
| `[web] web_host` | `0.0.0.0` | `127.0.0.1` for new installs | Your value is kept by the merge; if it is not local, a token is generated and printed at the next start |
| Linux service user | `root` | `newgreedy` | Nothing to do: `install.sh --update` moves the CA from `/root/.mitmproxy`, so the torrent client keeps trusting it |
| mitmproxy CA | Trusted at install | Trusted only with `--trust-ca` / `-TrustCA` | Nothing to do on update: a CA already trusted stays trusted. On a new machine, pass the option or point the client at the bundle — see [Linux step 4](#linux) |

- **Docker**: the files are now mounted as one folder. Before `docker compose up -d` with the new `docker-compose.yml`:
  ```bash
  docker compose down
  mkdir -p data && mv config.ini stats.json torrent_registry.json control.json newgreedy.log data/ 2>/dev/null
  mv torrents data/ 2>/dev/null
  ```
  The CA volume is reused as is. `config.ini` in `data/` is merged with the image's own at every start.
- **One-click update from v1.7.7**: the v1.7.7 button still works for this step. Restart right after — until then the engine answers announces with a tracker error that says a restart is needed.

### Linux

```bash
cd ~/NewGreedy                 # the folder you installed from
git pull                       # or extract the new release zip over it
sudo bash install.sh --update
```

The installer replaces the program files in `/opt/newgreedy`, merges `config.ini`, and restarts the service. Follow it with `journalctl -u newgreedy -f`.

v1.7.6 copied its `.git/` folder into `/opt/newgreedy`. It is not used; `sudo rm -rf /opt/newgreedy/.git` removes it.

### macOS

```bash
cd ~/NewGreedy
git pull
bash install-macos.sh --update
```

No `sudo`. Files in `~/Library/Application Support/NewGreedy` are updated and the launchd agent is reloaded.

### Windows

In PowerShell, from the folder you installed from:

```powershell
git pull                       # or download and extract the new release zip
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Update
```

Files in `%LOCALAPPDATA%\NewGreedy` are updated and the scheduled task is restarted.

### Docker

```bash
cd ~/NewGreedy
docker compose pull          # or: git pull && docker compose build
docker compose up -d
```

Your settings live in `./data/config.ini`, which Git does not track: `git pull` never conflicts with them. At start, the container merges it with the `config.ini` shipped in the image (a `pre-merge` backup is written first) and logs every change as `[CONFIG]`.

### Manual

Stop NewGreedy (`Ctrl+C`), then:

```bash
cd ~/NewGreedy
cp config.ini config.ini.mine
git checkout -- .
git pull
pip install -r requirements.txt
python3 config_merge.py --shipped config.ini --current config.ini.mine --out config.ini
python3 newgreedy.py
```

### Web interface

When a newer release exists, the Help page shows its **release notes** and **Update now**:

1. The release zip and its `.sha256` file are downloaded from GitHub. A release without the checksum file, or an archive that does not match it, is refused. The checksum sits in the same release, so it only catches a damaged download: when `UPDATE_SIGNING_KEY` is set in `_version.py`, the zip must also come with a valid Ed25519 signature (`<zip>.sig`). Create the key once with `python3 updater.py keygen <keyfile>`, then sign each zip with `python3 updater.py sign <zip> <keyfile>`.
2. A `pre-update` backup of the data is written, then program files are copied to `.backup/` and replaced.
3. `config.ini` is merged; data files are not touched. Changed dependencies are installed when NewGreedy runs in a venv; otherwise the status says to run `pip install -r requirements.txt`.
4. **Restart to apply** stops NewGreedy so the new code loads. Until then, the engine refuses to run mixed versions and answers announces with a tracker error asking for the restart.

A failure while files are being replaced puts the previous ones back and deletes the new ones; a failed download changes nothing. `POST /api/update/rollback` restores by hand. `update_channel = beta` also offers pre-releases.

What happens after **Restart to apply** depends on the install:

| Install | After the restart | Keep in mind |
|---|---|---|
| Linux | systemd starts NewGreedy again | — |
| macOS | launchd starts NewGreedy again | — |
| Windows | NewGreedy stops | If it is not back within a minute: `Start-ScheduledTask -TaskName NewGreedy`, or log off and on |
| Docker | — | The button is disabled: pull the new image instead |
| Manual | NewGreedy stops | Start it again with `python3 newgreedy.py`. The next `git pull` goes through the steps above |

### Options

- `--keep-defaults` (`-KeepDefaults` on Windows) — never apply a new default over one of your values. A new default is only ever applied once, to a `config.ini` written by an older version. `--keep-scrape` is still accepted as an alias.
- `--trust-ca` (Linux, macOS) / `-TrustCA` (Windows) — add the mitmproxy CA to the system trust store / login keychain / user store. Off by default; `--no-trust-ca` is still accepted and changes nothing.
- `python3 config_merge.py --shipped config.ini --current <your config.ini>` without `--out` shows what would change and writes nothing.

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

Set HTTP proxy `127.0.0.1:3456` in the network/proxy settings. The **Status** page gives the exact steps for Deluge, Transmission (environment variables), rTorrent and a client running in Docker, and shows a green line as soon as an announce reaches NewGreedy — force a reannounce in the client to test.

The proxy only accepts connections from this machine by default (`listen_host = 127.0.0.1`).

---

## Configuration reference

| Platform | Config path |
|---|---|
| Linux | `/opt/newgreedy/config.ini` |
| macOS | `~/Library/Application Support/NewGreedy/config.ini` |
| Windows | `%LOCALAPPDATA%\NewGreedy\config.ini` |
| Docker | `./data/config.ini` |
| Manual | `config.ini` next to `newgreedy.py` |

The data folder can be moved with the environment variable `NEWGREEDY_DATA_DIR`.

The running engine reloads `config.ini` within 5 seconds of any change — web UI, hand edit or restore — and applies it to every torrent without touching the counters. A restart is only needed for `listen_host`, `listen_port`, `ssl_verify_trackers`, `[upstream]`, `[web]`, `port_range` and the client identity keys; the log line `[CONFIG] ... restart needed for ...` lists them.

A value that cannot be read (`1,6` is read as `1.6`; `abc` is not) falls back to its default with a warning instead of stopping the engine. `python3 newgreedy.py --check-config` lists every problem.

---

### [proxy]

| Key | Default | Description |
|---|---|---|
| `listen_host` | `127.0.0.1` | Address the proxy listens on. `0.0.0.0` only when the torrent client runs on another host or container — anyone who reaches the port can then use the proxy. Overridden by `NEWGREEDY_LISTEN_HOST`. |
| `listen_port` | `3456` | Port NewGreedy listens on. Must match the proxy port in your torrent client. |

---

### [spoofing]

| Key | Default | Description |
|---|---|---|
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
| `seed_target_mb` | `500.0` | Upload total that fills the progress bar of a pure seeder in the web UI. |
| `anti_clustering` | `true` | Legacy master switch for announce de-correlation. Superseded by `desync_announces`, which falls back to this value. |

---

### [anti_detection]

| Key | Default | Description |
|---|---|---|
| `user_agent_mode` | `random` | `random`: send the User-Agent paired with the session `peer_id`. `fixed`: force `user_agent_value`, which breaks that pairing and is logged as a warning at startup. |
| `user_agent_value` | `qBittorrent/5.1.0` | Static UA, used only when `user_agent_mode = fixed`. |
| `spoof_user_agent` | `true` | Send the emulated client's User-Agent. `false` leaves your own client's header untouched. |
| `spoof_peer_id` | `true` | Use one stable `peer_id` and `key` for the whole session, like a real client. |
| `spoof_peers` | `true` | Send the `numwant` value of the emulated client instead of the real one. |
| `spoof_port` | `true` | Use one stable listening port for the whole session, taken from `port_range`. |
| `port_range` | `6881-6999` | Range for spoofed port selection. |
| `spoof_headers` | `true` | Inject realistic `Accept` and `Accept-Language` headers. |
| `echo_trackerid` | `true` | Send back the `trackerid` a tracker hands out, as BEP 3 requires. |
| `tracker_whitelist` | _(empty)_ | Comma-separated domains to process; a domain also covers its subdomains, never a longer name (`tracker.org` does not match `tracker.org.evil.net`). Empty = all. |
| `tracker_blacklist` | _(empty)_ | Comma-separated domains to skip, same matching. Takes precedence over whitelist. |
| `desync_announces` | `true` | Give every torrent its own announce period so torrents sharing a tracker never stay in lockstep. |
| `desync_spread_pct` | `0.18` | Maximum extra delay added to the tracker interval, as a fraction. Hard limit `0.45`. |
| `desync_min_gap` | `45` | Two announces to the same tracker closer than this count as a cluster and widen the spread. |
| `startup_burst` | `true` | Ramp the injected upload up over the first minutes instead of running at full speed from the first announce. |
| `startup_burst_window` | `300` | Length of that startup window, in seconds. |
| `startup_ramp_floor` | `0.15` | Upload multiplier at the very beginning of the window. |

The announce interval is only ever pushed **later**, never earlier, so NewGreedy can never
make your client announce more often than the tracker allows. A `min interval` larger than
`interval` is left untouched.

---

### [ssl]

| Key | Default | Description |
|---|---|---|
| `ssl_verify_trackers` | `true` | Check the certificate of HTTPS trackers. `false` accepts expired or self-signed ones (restart required). |

---

### [stats]

| Key | Default | Description |
|---|---|---|
| `persist_stats` | `true` | Save stats to `stats.json`. Flushed at most once per 60 s; forced on stop. |
| `auto_purge_stopped` | `true` | Remove torrent from stats on `event=stopped`. |
| `stopped_purge_delay` | `120` | Seconds before a torrent that sent `event=stopped` is dropped. A client that comes back in time keeps its counters. `0` = immediate. |
| `inactive_purge_hours` | `12` | Hours without any announce before a torrent is moved to the trash. Never checked during the first hour after a start, so torrents can announce first. `0` = never. |
| `tracker_cumul_ttl_days` | `30` | Days before an inactive tracker domain is dropped from the cumulative ratio. |
| `history_max_points` | `200` | History points kept per torrent for the charts. |
| `history_retention_days` | `0` | Days of history kept per torrent. `0` = limited by `history_max_points` only. |
| `trash_retention_days` | `7` | Days a purged torrent stays in the trash and can be restored. `0` = no trash, purges are final. |

---

### [web]

| Key | Default | Description |
|---|---|---|
| `web_enabled` | `true` | Enable the FastAPI web UI and REST API. |
| `web_host` | `127.0.0.1` | Bind address for the web server. Any other address requires a token. Overridden by `NEWGREEDY_WEB_HOST`. |
| `web_port` | `8080` | Port for the web UI. |
| `web_secret_token` | _(empty)_ | Token required to reach the UI/API. Generated at start — and printed on the console, never in the log file — when `web_host` is not local and this is empty. Sign in on the page shown, or open `?token=...` once: the token goes into an HttpOnly cookie and is removed from the URL. Scripts send it as the `X-Auth-Token` header. Rotate it from the Config page. |
| `web_path_prefix` | _(empty)_ | Serves the whole UI under `/<prefix>/`, WebSocket included, hiding it from casual scans. Letters, digits, `-` and `_`. |
| `web_rate_limit` | `240` | Max requests per minute per client IP. `0` disables the limit. Sign-in attempts are limited to 10 per minute. |
| `web_allowed_origins` | _(empty)_ | Extra origins allowed to send changes when a reverse proxy rewrites the `Host` header, e.g. `https://ng.example.org`. Without a token, the UI only answers to `localhost`, `127.x.x.x`, `[::1]` and the host names listed here (HTTP 421 otherwise), which blocks DNS-rebinding pages. |

> Every request that changes something must come from the web UI's own origin: another site open in the same browser cannot drive the API (CSRF), even without a token.

---

### [advanced]

| Key | Default | Description |
|---|---|---|
| `inject_hours` | `0-23` | Active injection window in 24h format. Supports wrap-around (`22-6`). |
| `min_announce_interval` | `1800` | Minimum seconds between announces for the same torrent. |
| `interval_jitter_pct` | `0.08` | ±Jitter on announce interval as a fraction. |
| `stall_announce_threshold` | `8` | Consecutive zero-DL announces before `[STALL_NET]` is flagged. |
| `min_announces_before_stagnation` | `10` | Minimum announces before stagnation can trigger. |
| `log_level` | `INFO` | Verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`. Applied live. |
| `corrupt_field_probability` | `0.05` | Probability of adding a `corrupt=` field. |
| `respect_tracker_interval` | `true` | Pace announces using the `interval` returned by the tracker when available. |
| `watch_folder` | `torrents` | Folder scanned every 15 s for new `.torrent` files, relative to the data folder. Empty disables auto-import. |

---

### [client]

| Key | Default | Description |
|---|---|---|
| `client_profile` | `random` | Emulated client: `qbittorrent`, `transmission`, `deluge`, `libtorrent`, `rtorrent` or `random`. Each profile pairs every peer_id prefix with the User-Agent of that exact version, and carries its own header set, default `numwant` and announce parameter order. |
| `rotate_profile_on_restart` | `true` | Legacy switch, read only when `session_identity` is empty (`true` = `rotate`, `false` = `stable`). |
| `session_identity` | `drift` | What survives a restart. `stable`: everything. `drift`: same client and port, fresh peer_id tail. `rotate`: a new client each time. |
| `version_bump_probability` | `0.10` | In `drift` mode, chance per start of moving up one release of the same client. |

---

### [bandwidth]

| Key | Default | Description |
|---|---|---|
| `global_max_speed_mbps` | `8.0` | Upload ceiling shared by every torrent, in MB/s. Models a single machine's uplink instead of letting each torrent inject independently. `0` disables. |
| `per_torrent_speed_cap_mbps` | `12.0` | Hard ceiling for the implicit speed of a single announce (`ΔUL / Δt`). |
| `max_download_speed_mbps` | `12.0` | Pace for the download NewGreedy reports beyond what your client reports, so `left` falls at a credible speed. `0` disables. |
| `circadian_smoothing` | `true` | Ramp injection up and down across `inject_hours` instead of switching on and off abruptly. |
| `circadian_night_factor` | `0.35` | Upload multiplier applied at the quietest point of the cycle. |

---

### [notifications]

| Key | Default | Description |
|---|---|---|
| `webhook_url` | _(empty)_ | Destination URL (`http://` or `https://`). Empty disables notifications. |
| `webhook_format` | `generic` | `generic` (plain JSON), `discord`, `slack`, `ntfy` (`https://ntfy.sh/<topic>`), `gotify` (`https://<server>/message?token=<app token>`) or `telegram` (`https://api.telegram.org/bot<token>/sendMessage`). |
| `webhook_events` | `target_reached,tracker_down,global_cap,engine_failed` | Subscribed events among `target_reached`, `tracker_down`, `tracker_up`, `global_cap`, `purged`, `engine_failed`. Identical alerts are de-duplicated within a 5-minute window. |
| `webhook_chat_id` | _(empty)_ | Telegram only: the chat that receives the messages. |

**Send a test notification** on the Config page checks the settings at once. `tracker_down` now also fires on timeouts, DNS failures and refused connections, not only on 5xx answers.

---

### [upstream]

| Key | Default | Description |
|---|---|---|
| `proxy_url` | _(empty)_ | Forward tracker announces through an external proxy, e.g. `http://gluetun:8888`. |
| `proxy_bypass` | _(empty)_ | Comma-separated domains that must not go through the upstream proxy. |

---

### [backup]

| Key | Default | Description |
|---|---|---|
| `auto_backup` | `true` | Write a backup archive of the data files on a schedule, into `backups/` in the data folder. |
| `backup_interval_hours` | `24` | Hours between two automatic backups. |
| `backup_keep` | `7` | Automatic backups kept; the oldest are deleted. The last 5 manual, `pre-update`, `pre-restore` and `pre-merge` archives are kept as well. |

---

### [update]

| Key | Default | Description |
|---|---|---|
| `update_channel` | `stable` | `stable`: releases only. `beta`: pre-releases too. |

---

### [meta]

| Key | Default | Description |
|---|---|---|
| `config_version` | `v1.7.8` | Version that wrote the file. Managed by the installers and the updater — do not edit. |

---

### [simulation]

| Key | Default | Description |
|---|---|---|
| `dry_run` | `false` | Compute and log everything without touching a single announce. Useful to validate a configuration before injecting for real. A banner appears on the dashboard while enabled. |

---

### Per-tracker overrides

Any `[tracker:domain]` section overrides the global settings for that domain only:

```ini
[tracker:private.example.org]
target_ratio                 = 1.05
max_global_ratio_per_tracker = 1.20
catch_up_factor              = 0.10

[tracker:open.example.com]
target_ratio                 = 2.50
```

Useful when a private tracker requires a modest ratio while another tolerates more. The overrides apply to torrents reloaded from `stats.json` at start too, and the per-tracker cap is the one shown in the web UI. Keys from `[spoofing]`, `[advanced]` and `[bandwidth]` can be overridden; anything else is ignored with a warning.

---

## Web UI

| Page | URL | Description |
|---|---|---|
| Dashboard | `/` | Summary cards, tracker cap warning banner, auto-refresh every 15 s |
| Torrents | `/torrents` | Sortable, searchable table with per-torrent pause, ratio override, tags and removal; trash with restore |
| Charts | `/charts` | Time-series per torrent, plus a compare mode overlaying several ratio curves |
| Config | `/config` | View and edit settings, access token, notification test, client profiles, backups, `.torrent` import |
| Logs | `/logs` | Live log stream, level filters, tracker ratio bar, GLOBAL_CAP counter |
| Status | `/status` | Self-check, engine state, file sizes, client setup guide, live "announce received" test, per-tracker state |
| Help | `/help` | Log reference, API docs, qBittorrent setup, release notes and one-click update |

When a token is set, pages without a valid session show a sign-in page.

---

## REST API

| Endpoint | Method | Description |
|---|---|---|
| `/api/stats` | GET | All torrent stats |
| `/api/stats/csv` | GET | Export stats as CSV |
| `/api/stats/purge` | GET | Dry-run preview — count and hashes that would be purged |
| `/api/stats/purge` | DELETE | Move torrents to the trash — params: `keep_active`, `inactive_hours` |
| `/api/stats/{ih}` | DELETE | Move a single torrent to the trash, by 8-char hash prefix |
| `/api/trash` | GET / DELETE | Purged torrents still restorable / empty the trash |
| `/api/trash/{ih}/restore` | POST | Bring a purged torrent back |
| `/api/trash/{ih}` | DELETE | Delete one trash entry for good |
| `/api/tracker_stats` | GET | Per-domain UL/DL cumul, ratio, and % of cap |
| `/api/udp_status` | GET | UDP trackers detected — these bypass NewGreedy |
| `/api/registry` | GET | Imported torrents: name, size, tracker list |
| `/api/config/editable` | GET | Settings that can be changed from the UI |
| `/api/config/set` | POST | Update one setting in `config.ini` |
| `/api/control` | GET | Paused torrents, ratio overrides and tags |
| `/api/control/pause` | POST | Pause or resume injection for one torrent |
| `/api/control/override` | POST | Force a target ratio or an upload total |
| `/api/control/tags` | POST | Assign tags to a torrent |
| `/api/profiles` | GET | Available emulated client profiles |
| `/api/backup` | GET | Download a zip with config, stats, registry, control file and trash |
| `/api/restore` | POST | Restore an uploaded backup archive (a `pre-restore` backup is written first) |
| `/api/backups` | GET / POST | List stored backups / create one now |
| `/api/backups/{name}` | GET / DELETE | Download / delete a stored backup |
| `/api/backups/{name}/restore` | POST | Restore a stored backup |
| `/api/status` | GET | Self-check, engine state, last announce, per-tracker state, file sizes |
| `/api/notify/test` | POST | Send a test notification |
| `/api/token` | GET | Whether a token is required |
| `/api/token/rotate` | POST | Generate a new token (returned once) |
| `/api/token/clear` | POST | Remove the token — only when `web_host` is local |
| `/api/login` · `/api/logout` | POST | Open / close a browser session (HttpOnly cookie) |
| `/api/update/release` | GET | Release notes of the latest version on the configured channel |
| `/api/update/status` | GET | Current version, update progress, pending restart |
| `/api/update/start` | POST | Download the latest release and replace program files |
| `/api/update/rollback` | POST | Restore the files saved before the last update |
| `/api/restart` | POST | Restart the service so new files take effect |
| `/metrics` | GET | Prometheus metrics |
| `/api/logs` | DELETE | Truncate `newgreedy.log` on the server |
| `/api/health` | GET | Engine state, always reachable (Docker healthcheck); stalled, anomalous and target-reached torrents when authenticated. `503` when the engine failed or stopped answering |
| `/api/history` | GET | Time-series snapshots for all torrents |
| `/api/history/{ih}` | GET | Time-series for one torrent |
| `/api/config` | GET | Current `config.ini` as JSON |
| `/api/config/reload` | POST | Reload `config.ini` without restarting (the engine also does it on its own) |
| `/api/version` | GET | Current version vs latest GitHub release |
| `/api/upload` | POST | Upload a `.torrent` to pre-register infohash + size |
| `/ws/logs` | WebSocket | Real-time log stream |

Requests that change something must come from the web UI's origin (or carry no browser `Origin` at all, like `curl`). With a token, send it as `X-Auth-Token`.

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
[SEED] 44556677 | UL:   41.8M +   6.2M #2 [BURST]
[DOWN] 11223344 | DL:  600.0M UL:  480.0M +  12.7M R:0.80 ETA:~21a #7 [DESYNC]
[GLOBAL_CAP] tracker.opentrackr.org | tracker_ratio capped → UL adjusted to 1280.0M
[TRACKER_DOWN] tracker.openbittorrent.com — 3 consecutive errors, entering backoff
[TRACKER_UP] tracker.openbittorrent.com — recovered
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
| `[AUTO-PURGE]` | No announce for `inactive_purge_hours` — moved to the trash (never during the first hour after a start) |
| `[GLOBAL_CAP]` | Global tracker ratio guard triggered — UL reduced |
| `[TRACKER_DOWN]` | Tracker entering exponential backoff after repeated 5xx errors, timeouts or connection failures |
| `[TRACKER_UP]` | Tracker recovered — announces resume |
| `[UDP_BYPASS]` | UDP tracker detected — its announces are **not** intercepted |
| `[WATCH]` | Torrent auto-imported from the watch folder |
| `[CUMUL_PURGE]` | Inactive tracker domain dropped from the cumulative ratio |
| `[CUMUL_RESET]` | Per-tracker totals written before v1.7.7 were dropped at startup — they were over-counted and cannot be corrected after the fact |
| `[PURGE_SYNC]` | Torrent removed from memory after a purge from the web UI |
| `[PAUSED]` | Injection suspended for this torrent |
| `[DRY-RUN]` | Simulation mode — the announce was left untouched |
| `[BURST]` | Startup window — upload is still ramping up |
| `[DESYNC]` | Another torrent announced to the same tracker within `desync_min_gap`; the spread was widened |
| `[IDENTITY]` | Client profile rotated at startup |
| `[CONTROL]` | Pause, override or tag file reloaded |
| `[STOPPED]` | The client sent `event=stopped`; the torrent is retired once `stopped_purge_delay` has passed |
| `[RESUMED]` | It announced again before that delay — the purge was cancelled and the counters kept |
| `[DL_CAP]` | The reported download was held back to keep `left` falling at a plausible speed |
| `[TRACKERID]` | A tracker issued a `trackerid`; it is echoed from the next announce on (debug level) |
| `[TARGET_REACHED] hash@tracker` | The torrent just reached its target — logged and notified once, when it happens |
| `[CONFIG]` | `config.ini` reloaded live, or merged after an upgrade; lists settings that still need a restart |
| `[REGISTRY]` | `torrent_registry.json` reloaded — sizes corrected when they changed |
| `[TRASH]` | Trash entries older than `trash_retention_days` deleted for good |
| `[RESTORE]` | Stats replaced from a backup, or torrents brought back from the trash |
| `[BACKUP]` | Backup archive written (`auto`, `manual`, `pre-update`, `pre-restore`, `pre-merge`) |
| `[TOKEN]` | Web UI token rotated or removed |
| `[ENGINE_FAILED]` | The engine could not start: announces get a tracker error that names the problem until it is fixed and NewGreedy restarted |

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| New settings missing from the Config page | `config.ini` was kept as is by an older installer | Run `sudo bash install.sh --update` again from v1.7.7 |
| `git pull` refuses: *local changes to config.ini* | Docker or manual install: your settings live in the tracked file | See [Updating](#updating) — set it aside, pull, merge it back |
| `peer_id` changes at every restart | `session_identity = drift` redraws its random part, as a real client does | Expected. `stable` freezes it |
| `[CUMUL_RESET]` once after upgrading, tracker totals at zero | Totals from v1.7.6 were over-counted and are discarded | Expected. They rebuild from real figures |
| Ratio on tracker exceeds `max_global_ratio` | Pre-v1.7.5: cap was computed after accumulation | Upgrade to v1.7.5 — fixed by correct ordering |
| Ratio cap resets after restart | Pre-v1.7.5: `_tracker_cumul` not persisted | Upgrade to v1.7.5 — now saved in `stats.json` |
| `[STAG][STALL_ALGO][TARGET_REACHED]` together | Pre-v1.7.5: stagnation ran after target check | Upgrade to v1.7.5 — `_calc_upload` short-circuits on target |
| `[TRACKER_DOWN]` in logs, announces skipped | Tracker returning 5xx, timing out or unreachable — backoff active | Wait for automatic recovery; the Status page shows the last answer per tracker |
| Client shows *NewGreedy engine not running* as tracker error | The engine failed to start, or an update is waiting for a restart | Read the reason in the message or on the Status page; `--check-config`; restart NewGreedy |
| Torrent client cannot reach the proxy after upgrading | `listen_host` is now `127.0.0.1` | Set `listen_host = 0.0.0.0` when the client runs on another host |
| Sign-in page appears after upgrading | `web_host` is not local: a token was generated | Read it on the console / `journalctl -u newgreedy` / `docker compose logs`, or in `config.ini` |
| One-click update refused: *no checksum file* | The release has no `.sha256` asset | Update from the command line |
| `Stats save failed` in Docker | v1.7.7 compose file: files mounted one by one | Switch to the `./data` folder layout — see [Coming from v1.7.7](#coming-from-v177) |
| Purge by inactivity does nothing | Entries have no timestamp | Use `GET /api/stats/purge?inactive_hours=12` to preview first |
| `mitmproxy crashed (password cannot be longer than 72 bytes)` | mitmproxy 10 or 11 with bcrypt 5.x: the passlib they use cannot handle it | `pip uninstall bcrypt`, or move to mitmproxy 12 (Python 3.12+); `--check-config` flags it |
| `AttributeError: module 'bcrypt' has no attribute '__about__'` | mitmproxy 10 or 11: bcrypt ≥ 4.1 next to passlib (harmless warning) | `pip uninstall bcrypt` |
| Service restarts in a loop after upgrade | The old systemd unit was not regenerated | `systemctl stop newgreedy && rm /etc/systemd/system/newgreedy.service && systemctl daemon-reload`, then run `install.sh` **without** `--update` |
| Ratio not rising on a tracker | The tracker is UDP — announces bypass the proxy | Check the dashboard banner / `GET /api/udp_status`. UDP interception is not supported |
| Identity changes after restart | Pre-v1.7.6: `peer_id`/`port` were not persisted | Upgrade to v1.7.6 — identity is now stored in `stats.json` |
| Web UI reachable by anyone | v1.7.7 and older: default bind `0.0.0.0` with no auth | v1.7.8 binds to `127.0.0.1`, and generates a token for any other address |
| Stats lost after `docker stop` | Pre-v1.7.6: no SIGTERM handler | Upgrade to v1.7.6 — a final flush runs on shutdown |
| `.torrent` in watch folder ignored | Folder disabled or file too large | Check `watch_folder` and the 10 MB limit; see `[WATCH]` lines in the log |
| Ratio not increasing | 0 leechers (swarm-aware block) | Wait for leechers; check Charts for history |
| `[STALL_NET]` on all torrents | Client reports 0 download consistently | Normal for pure seeders — lower `stall_announce_threshold` |
| HTTPS announces not intercepted | mitmproxy CA not trusted by the torrent client | Start the client with `SSL_CERT_FILE` (Linux), or re-run the installer with `--trust-ca` / `-TrustCA` |
| Web UI answers `421 unexpected Host header` | Reached by a name other than localhost, without a token | Set `web_secret_token`, or add the name to `web_allowed_origins` |
| Web UI not loading | Missing dependencies | `pip install fastapi uvicorn python-multipart` |
| Config changes not applied | The setting needs a restart, or the value is invalid | Check the `[CONFIG]` line in the log and `--check-config`; `systemctl restart newgreedy` |
| A torrent disappeared | Purged after `event=stopped`, inactivity or by hand | Torrents page → Trash → Restore, within `trash_retention_days` |

---

## Files

| File | Role |
|---|---|
| `newgreedy.py` | Launcher — watchdog, update check, web UI thread |
| `_version.py` | Shared constants: version check, bencode reader, webhook delivery |
| `client_profiles.py` | Emulated client definitions — peer_id, headers, parameter order |
| `updater.py` | Downloads a GitHub release, swaps program files, keeps a rollback copy |
| `config_merge.py` | Brings an existing `config.ini` up to the shipped one without losing a setting; used by every installer, the one-click update and the Docker start |
| `config_schema.py` | Every setting with its type, range and default: tolerant reading, validation, safe edits |
| `datastore.py` | Data folder paths and the files shared by the engine and the web UI (locks, trash, purge and restore queues, engine status) |
| `backups.py` | Rotating backup archives |
| `diagnostics.py` | Self-check behind `--check-config` and the Status page |
| `control.json` | Paused torrents, ratio overrides and tags — written by the web UI |
| `trash.json` | Purged torrents, restorable for `trash_retention_days` |
| `backups/` | Backup archives (`auto`, `manual`, `pre-update`, `pre-restore`, `pre-merge`) |
| `.addon_status.json` | Engine heartbeat read by the Status page and `/api/health` |
| `newgreedy_addon.py` | mitmproxy addon — intercepts announces and rewrites them, tracker backoff |
| `engine_model.py` | Per-torrent ratio model and session identity |
| `engine_state.py` | Engine state: purges, control file, registry, `stats.json` persistence |
| `newgreedy_web.py` | FastAPI app — web UI and REST API |
| `config.ini` | Main configuration file |
| `stats.json` | Persisted stats (schema v7) — includes `_tracker_cumul`, the session identity, reported progress and stored tracker ids |
| `torrent_registry.json` | Infohash, size, name and tracker list from `.torrent` imports |
| `torrents/` | Watch folder — drop `.torrent` files here for auto-import |
| `newgreedy.log` | Log file (streamed live in Logs page) |
| `static/` | Web UI assets — Chart.js 4.6.1 is shipped in it (`chart.umd.min.js`, MIT), no script is loaded from a CDN |
| `MANIFEST.txt` | Program files the installers copy — the one list to update when a file is added |
| `tests/` | Test suite — `python3 -m unittest discover -s tests -t .` (web and engine tests need `httpx` and `mitmproxy`) |
| `install.sh` | Installer — Linux / macOS |
| `install.ps1` | Installer — Windows |
| `install-macos.sh` | Installer — macOS (launchd) |
| `uninstall.sh` | Uninstaller — Linux / macOS |
| `requirements.txt` | Python dependencies |
| `LICENSE` | GNU General Public License v3.0 |
| `.gitignore` | Excludes runtime data (`stats.json`, logs, `.torrent` files) from Git |
| `Dockerfile` | Docker image — unprivileged user, no CA baked in |
| `docker-entrypoint.sh` | Fixes volume ownership, then drops root |
| `docker-compose.yml` | Docker Compose stack — one `./data` folder |
| `.github/workflows/` | Release zip + `.sha256`, multi-arch image on GHCR |

---

## License

NewGreedy is free software licensed under the **GNU General Public License v3.0**.

You may use, study, modify and redistribute it under the terms of the GPLv3. Any redistributed version — modified or not — must remain under the same license and ship its complete source code. See [LICENSE](LICENSE) for the full text.

```
NewGreedy — BitTorrent announce proxy
Copyright (C) 2026  Mrt0t0

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
```

---

## Changelog

### v1.7.8

| | v1.7.7 | v1.7.8 |
|---|---|---|
| Proxy bind | `0.0.0.0`, hard-coded | `listen_host`, `127.0.0.1` by default |
| Web UI access | `0.0.0.0`, no token, no CSRF check | `127.0.0.1`; token generated for any other address; sign-in page, HttpOnly cookie, origin check on every change and on the WebSocket; without a token, only loopback host names are answered (DNS rebinding) |
| Secrets in `/api/config` | Token masked | Token, webhook path and proxy password masked |
| Chart.js | Loaded from a CDN, floating `@4` | 4.6.1 shipped in `static/`; the CSP allows no third-party script |
| Web UI data | File names, tags and tracker domains inserted as HTML | Escaped everywhere |
| Torrent size | Multi-file torrents: size of the first file only | Real total |
| Invalid setting | Engine did not load, announces passed through unmodified | Default used with a warning; an engine that cannot start answers announces with an explicit tracker error |
| Stop / restart | mitmproxy relaunched on SIGTERM, exit code 1 | Clean stop in under a second |
| Failed update download | Restored the backup of the *previous* update, old `config.ini` included | Nothing touched; `.sha256` required, optional Ed25519 signature; every path checked to stay in the program folder; `pre-update` backup |
| Inactive purge | Fixed 12 h, run at the first announce after a start | `inactive_purge_hours`, never in the first hour, to the trash |
| Deleting torrents | Final | Trash, restorable for `trash_retention_days` |
| Settings | Read at start only | Reloaded live within 5 s |
| `[tracker:x]` / ratio override | Lost after a restart / removal ignored | Kept / applied at once |
| Registry | Read at start only | Reloaded when it changes |
| Backups | Manual download | Automatic rotation, one-click restore, backups before update and restore |
| Notifications | Discord, Slack, JSON; 5xx only; target reached late | + ntfy, Gotify, Telegram, test button; timeouts count; target reached notified at once |
| Status | — | Status page and `--check-config` |
| Docker | Root, files mounted one by one, update button | Unprivileged user, one `./data` folder, image on GHCR, update by `docker compose pull` |
| Linux service | root, system pip | `newgreedy` account, venv |
| mitmproxy CA | Trusted system-wide / in the keychain / in the user store at install | Only with `--trust-ca` / `-TrustCA`; Linux bundle for the torrent client alone; uninstall removes this CA only |
| Installers | Re-running without `--update` replaced `config.ini`; Windows task with administrator rights, JSON files rewritten with a BOM on update | `config.ini` always merged; data files never touched; Windows task as a normal user, Python installer signature checked |
| Torrent keys | First 8 hex digits of the info-hash: two torrents could share an entry | Full info-hash; 8-digit entries adopted on the next announce |
| `config.ini` | 12 sections, 73 keys, 8 unused | 15 sections, 77 keys, none unused |
| Python files | 7 | 13 — `config_schema.py`, `datastore.py`, `backups.py`, `diagnostics.py`, `engine_model.py`, `engine_state.py` added |
| Several trackers per torrent | Each tracker's announce credited a full interval: 3 trackers, 3× the configured speed | Upload credited on the torrent's own clock, whatever the number of trackers |
| `global_max_speed_mbps` | Checked over the last minute only: the real shared ceiling was about 1/30 of the setting | Checked over the announce interval |
| `max_global_ratio_per_tracker` | Raised the upload it was meant to cut | Caps the increment, the per-tracker total matches what was announced |
| Tracker in backoff | Announces passed through unmodified (real peer_id, lower upload) | Still rewritten |
| First announce of a new torrent | Up to one full interval of upload with `event=started` | `uploaded=0` |
| Resumed / completed torrent | Resumed at 70 %: reported at 0 %; completed: `left` > 0 | Reported where the client is; `left=0` once complete |
| mitmproxy | 10 and 11 | 10 to 12 (Python 3.14 supported through mitmproxy 12) |
| Tests | — | `tests/`, 63 tests |

- **Fixes** — `inf` or `1e400` in `config.ini` no longer crash the start; a torrent removed in the web UI can no longer come back at the next save; busy ports are detected on Windows; Logs page counters no longer slow down with the log size; whitelist/blacklist no longer match longer domain names; `config.ini` edits are atomic; purge requests can no longer be lost; torrent Actions column restored (pause, ratio, tags, delete); links keep the path prefix; `log_level`, `seed_target_mb` and `ssl_verify_trackers` now read; installers generate the CA again (the call failed silently since mitmproxy 10); `/metrics` labels escaped; stats saved off the proxy loop; restoring from the trash a torrent that came back no longer loses the trashed figures; the Status page reports an engine blocked by an unfinished update; an invalid `web_secret_token` locks the web UI instead of opening it; the bcrypt check no longer flags mitmproxy 12
- **Upgrading** — read [Coming from v1.7.7](#coming-from-v177): `listen_host` defaults to `127.0.0.1`, Docker moves to a `./data` folder, the Linux service runs as `newgreedy`; 8 unused settings, `intercept_scrape` included, are removed from `config.ini` by the installers' and Docker's merge (the v1.7.7 update button keeps them; they are ignored and listed on the Status page); HTTPS trackers need the torrent client to trust the CA — see [Linux step 4](#linux)

### v1.7.7

| | v1.7.6 | v1.7.7 |
|---|---|---|
| Client identity | One peer_id, port and client per torrent | One identity for the whole session; 5 client profiles, peer_id always matching the User-Agent |
| Across restarts | Frozen for ever | `drift`: same client and port, fresh peer_id, occasional version bump |
| Announce timing | Independent jitter, torrents stay in lockstep | Per-torrent offset, torrents restarted together fan out |
| Startup | Full rate from the first announce | Upload ramps up over the first minutes |
| `downloaded` / `left` | `left` passed through untouched | `downloaded + left = size`, credible download speed |
| `numwant` / `event` | Randomised / fake `started` injected | Stable profile value / never altered |
| `trackerid` | Not returned | Returned, as BEP 3 requires |
| `event=stopped` | Forwarded unmodified, torrent dropped at once | Rewritten; removal deferred and batched |
| Scrapes | Dropped by default | Forwarded |
| Aggregate tracker cap | Download over-counted, cap fading over time | Counts real download |
| Bandwidth | Per torrent, on/off active hours | Shared uplink budget, smooth day/night curve |
| Control | — | Pause, ratio override and tags per torrent; `[tracker:domain]` overrides |
| Integrations | — | Webhooks (Discord, Slack, JSON), upstream proxy, `dry_run` |
| Backup | — | Backup / restore archive from the Config page |
| Update | `install.sh --update`, `config.ini` kept as is | Same command, `config.ini` merged; one-click update from the web UI |
| `config.ini` | 7 sections, 50 keys | 12 sections, 73 keys |
| Python files | 4 | 7 — `client_profiles.py`, `updater.py`, `config_merge.py` added |

- **Fixes** — a `%` in a setting no longer stops the engine; the Real UL column no longer climbs on its own; a stopped torrent can no longer come back as an empty entry; installers copy a file list instead of the whole folder, `.git/` included
- **Upgrading** — per-tracker totals written by v1.7.6 are discarded once (`[CUMUL_RESET]`), and `intercept_scrape` moves to `false`; everything else in `config.ini` is kept

### v1.7.6
- **Anti-detection** — persistent `peer_id`/`port`/`key`, tracker-provided announce interval, `key` spoofing
- **UDP awareness** — `udp://` trackers detected, logged as `[UDP_BYPASS]` and flagged in the UI (they bypass the proxy)
- **Security** — optional path prefix + secret token, per-IP rate limiting, secret redaction, 10 MB upload limit, filename sanitisation
- **Robustness** — atomic `stats.json` writes, log rotation (10 MB × 3), graceful SIGTERM/SIGINT flush, hourly memory cleanup, config validation, tracker cumul retention, history compression
- **Features** — watch folder auto-import, settings editor, `/metrics` for Prometheus, per-torrent tracker breakdown, torrent names in UI
- **Performance** — config parsed once with `__slots__`, unified read cache, cached GitHub version check, shared `_version.py` module
- **Fixes** — log stream survives truncation/rotation, rate limiting restricted to API routes, WebSocket back-off, versioned static assets, `bcrypt<5` pinned
- **Platforms** — dedicated macOS installer (launchd), config and stats preserved on update for all installers
- **Dependencies** — version bounds pinned in `requirements.txt`
- **License** — project relicensed under GNU GPL v3.0

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
- Auto-purge after 12 h inactivity, full English web UI

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


---

## Detailed changelog

### v1.7.8

**Security**
- Proxy bound to `127.0.0.1` by default (`listen_host`); web UI too (`web_host`), with a token generated at start whenever it listens beyond this machine
- Stored XSS closed: every file name, tag, tracker domain and error message is escaped before display
- Origin check on every change and on the log WebSocket (CSRF); sign-in page; token kept in an HttpOnly cookie, removed from URLs, compared in constant time; rotation from the Config page
- Without a token, the web UI answers only to loopback host names and `web_allowed_origins` (DNS rebinding)
- `/api/config` masks the webhook path and the upstream proxy password
- Chart.js shipped in `static/`; no script loaded from a CDN
- One-click update needs the release zip and its `.sha256`, and an Ed25519 signature once `UPDATE_SIGNING_KEY` is set; archive paths confined to the program folder; `pre-update` backup; disabled in Docker
- Linux service under a dedicated `newgreedy` account, in a venv; CA key readable by that account only
- mitmproxy CA trusted only on request (`--trust-ca`, `-TrustCA`); uninstallers remove this CA only, by content or fingerprint
- Windows: scheduled task without administrator rights; Python installer run only with a valid PSF signature
- Docker image runs unprivileged and never contains a CA; proxy published on `127.0.0.1` of the host

**Reliability**
- Clean stop on SIGTERM/SIGINT: the watchdog restarts mitmproxy on failures only
- Tolerant settings: an unreadable value falls back to its default with a warning; an engine that cannot start answers announces with a tracker error that names the problem, and never runs mixed versions after an update
- Multi-file torrents registered with their real size; registry reloaded live and sizes corrected
- Stats written off the proxy loop; shared state behind one lock; purge and restore queues locked across threads
- A failed update download no longer rolls back to an older version; a failed apply removes the files it created
- Inactive purge configurable and never run right after a start
- Docker: one data folder, so atomic writes work; healthcheck reachable with a token or a path prefix
- Torrents keyed by their full info-hash; entries of older versions adopted on the next announce
- `stats.json` written under the file lock, pending purges applied first
- Installers merge an existing `config.ini` every time and never touch data files
- Engine split into `engine_model.py` and `engine_state.py`; one file list (`MANIFEST.txt`); version read from `_version.py`; test suite in `tests/`

**Features**
- Status page and `python3 newgreedy.py --check-config`
- Trash for purged torrents; automatic rotating backups with one-click restore
- `config.ini` reloaded live; per-tracker overrides kept across restarts; ratio override removal applied at once
- Notifications to ntfy, Gotify and Telegram, `engine_failed` event, test button; `tracker_down` on timeouts and connection errors; `target_reached` notified when it happens
- Release notes shown before updating; `stable` / `beta` channel
- Adjustable chart history (`history_max_points`, `history_retention_days`)
- Multi-arch image `ghcr.io/mrt0t0/newgreedy`; release workflow building the zip and its checksum

### v1.7.6

**Anti-detection**
- `peer_id`, port and `key` kept per torrent across restarts, instead of a new identity at every start
- Announces paced by the interval the tracker returns (`respect_tracker_interval`)
- UDP trackers detected at import and flagged: their announces bypass the proxy and report real figures

**Security**
- Optional access token and hidden path for the web UI (`web_secret_token`, `web_path_prefix`)
- Per-IP rate limiting on the API (`web_rate_limit`); secrets masked in API responses
- 10 MB upload limit and sanitised file names for `.torrent` imports

**Robustness**
- Stats flushed on `SIGTERM`/`SIGINT`, so `systemctl stop` and `docker stop` lose nothing
- Atomic writes for `stats.json`, log rotation (10 MB × 3), hourly memory cleanup
- History compressed beyond 24 h; inactive trackers dropped after `tracker_cumul_ttl_days`
- Numeric settings validated at startup with an explicit warning

**Features**
- Watch folder: `.torrent` files dropped in `torrents/` are imported automatically
- Settings editor on the Config page, Prometheus `/metrics`, per-tracker breakdown per torrent
- Torrent names shown instead of hashes; macOS installer with a launchd agent

**Fixes**
- Live log stream survives log truncation and rotation; WebSocket reconnects with back-off
- Rate limiting no longer blocks pages and static files; GitHub version check cached for an hour

### v1.7.5

**Engine**
- Global ratio cap checked before the upload is added, so it can no longer be exceeded
- Per-tracker totals persisted across restarts
- Target reached stops injection before any stagnation logic runs
- Exponential backoff after repeated tracker 5xx errors (`[TRACKER_DOWN]` / `[TRACKER_UP]`)
- Stats flushed at most once a minute instead of on every announce

**API**
- `GET /api/tracker_stats`, `GET /api/stats/purge` (dry-run preview), `DELETE /api/stats/{ih}`
- Purge by inactivity and CSV ratio fixed; `purge` route reachable again

**Web UI**
- Warning banner when a tracker passes 80 % of its cap
- *Last seen* column, log level filters, live tracker ratio bar
- Shared `style.css` and `app.js`: pages 62 % lighter

---

<div align="center">

**NewGreedy** — [Report an issue](https://github.com/Mrt0t0/NewGreedy/issues) · [Releases](https://github.com/Mrt0t0/NewGreedy/releases)

</div>
