#!/usr/bin/env bash
# NewGreedy v1.6.0 — Automated installer for Linux / macOS
# Usage:
#   sudo ./install.sh            → fresh install
#   sudo ./install.sh --update   → update existing installation

set -e

VERSION="1.6.0"
UPDATE=false
for arg in "$@"; do [ "$arg" = "--update" ] && UPDATE=true; done

DEST=/opt/newgreedy
SERVICE=/etc/systemd/system/newgreedy.service
CERT_DIR="$HOME/.mitmproxy"
CERT_PEM="$CERT_DIR/mitmproxy-ca-cert.pem"

# ── Colors ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "${GREEN}[✓]${NC} $1"; }
info() { echo -e "${CYAN}[→]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
fail() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

echo -e "${BOLD}NewGreedy v${VERSION} — Installer${NC}"
echo "────────────────────────────────────"

# ── 1. Detect OS ──────────────────────────────────────────────────────────
OS="unknown"
if   [[ "$OSTYPE" == "linux-gnu"* ]]; then OS="linux"
elif [[ "$OSTYPE" == "darwin"*    ]]; then OS="macos"
else fail "Unsupported OS: $OSTYPE"; fi
ok "Detected OS: $OS"

# ── 2. Check / install Python 3.9+ ───────────────────────────────────────
info "Checking Python..."
PYTHON=""
for cmd in python3 python3.12 python3.11 python3.10 python3.9; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" -c "import sys; print(sys.version_info[:2])")
        if "$cmd" -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" 2>/dev/null; then
            PYTHON="$cmd"; ok "Python found: $("$cmd" --version)"; break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    warn "Python 3.9+ not found — attempting automatic install..."
    if [ "$OS" = "linux" ]; then
        if   command -v apt-get &>/dev/null; then apt-get update -qq && apt-get install -y python3 python3-pip
        elif command -v dnf     &>/dev/null; then dnf install -y python3 python3-pip
        elif command -v pacman  &>/dev/null; then pacman -Sy --noconfirm python python-pip
        else fail "Cannot auto-install Python. Please install Python 3.9+ manually."; fi
    elif [ "$OS" = "macos" ]; then
        if command -v brew &>/dev/null; then brew install python
        else fail "Homebrew not found. Install it from https://brew.sh then re-run this script."; fi
    fi
    PYTHON=$(command -v python3) || fail "Python install failed."
    ok "Python installed: $($PYTHON --version)"
fi

# ── 3. Check / install pip ────────────────────────────────────────────────
info "Checking pip..."
if ! $PYTHON -m pip --version &>/dev/null; then
    warn "pip not found — installing..."
    if [ "$OS" = "linux" ]; then
        if   command -v apt-get &>/dev/null; then apt-get install -y python3-pip
        elif command -v dnf     &>/dev/null; then dnf install -y python3-pip
        else $PYTHON -m ensurepip --upgrade; fi
    elif [ "$OS" = "macos" ]; then
        $PYTHON -m ensurepip --upgrade
    fi
fi
ok "pip OK"

# ── 4. Install Python dependencies ───────────────────────────────────────
info "Installing Python packages..."
$PYTHON -m pip install -q --upgrade pip
$PYTHON -m pip install -q -r requirements.txt
ok "Python packages installed"

# ── 5. Copy files ─────────────────────────────────────────────────────────
if [ ! -d "$DEST" ] || $UPDATE; then
    info "Copying files to $DEST..."
    mkdir -p "$DEST"
    cp -r . "$DEST/"
    ok "Files copied to $DEST"
fi

# ── 6. Generate mitmproxy CA certificate ─────────────────────────────────
if [ ! -f "$CERT_PEM" ]; then
    info "Generating mitmproxy CA certificate (first run)..."
    # Run mitmdump for 2 seconds to generate the cert, then kill it
    timeout 3 $PYTHON -m mitmproxy.tools.main mitmdump --listen-port 18080 &>/dev/null || true
    sleep 2
    if [ ! -f "$CERT_PEM" ]; then
        # Alternative: use mitmproxy's cert generation directly
        $PYTHON -c "
from mitmproxy.certs import CertStore
import pathlib, tempfile
store = CertStore.from_store(pathlib.Path('$CERT_DIR'), 'mitmproxy', 2048)
" 2>/dev/null || true
    fi
    if [ -f "$CERT_PEM" ]; then
        ok "CA certificate generated: $CERT_PEM"
    else
        warn "Could not auto-generate certificate. Run 'python3 newgreedy.py' once, then re-run install.sh."
    fi
else
    ok "CA certificate already exists"
fi

# ── 7. Trust CA certificate ───────────────────────────────────────────────
if [ -f "$CERT_PEM" ]; then
    info "Installing CA certificate in system trust store..."
    if [ "$OS" = "linux" ]; then
        if command -v update-ca-certificates &>/dev/null; then
            # Debian / Ubuntu
            cp "$CERT_PEM" /usr/local/share/ca-certificates/mitmproxy-newgreedy.crt
            update-ca-certificates -f &>/dev/null
            ok "Certificate trusted (Debian/Ubuntu)"
        elif command -v update-ca-trust &>/dev/null; then
            # Fedora / RHEL / Arch
            cp "$CERT_PEM" /etc/pki/ca-trust/source/anchors/mitmproxy-newgreedy.crt 2>/dev/null || \
            cp "$CERT_PEM" /etc/ca-certificates/trust-source/anchors/mitmproxy-newgreedy.crt 2>/dev/null || true
            update-ca-trust &>/dev/null
            ok "Certificate trusted (Fedora/Arch)"
        else
            warn "Unknown distro — add $CERT_PEM to your trust store manually."
        fi
    elif [ "$OS" = "macos" ]; then
        security add-trusted-cert -d -r trustRoot \
            -k /Library/Keychains/System.keychain \
            "$CERT_PEM" 2>/dev/null && ok "Certificate trusted (macOS Keychain)" \
            || warn "Could not auto-trust cert on macOS — add it manually in Keychain Access."
    fi
else
    warn "Certificate not found — skipping trust step."
    warn "Run 'python3 newgreedy.py' once, then re-run: sudo ./install.sh --update"
fi

# ── 8. Create systemd service (Linux only) ────────────────────────────────
if [ "$OS" = "linux" ] && command -v systemctl &>/dev/null; then
    if ! $UPDATE; then
        info "Creating systemd service..."
        ACTUAL_USER="${SUDO_USER:-$USER}"
        PYTHON_PATH=$(command -v $PYTHON)
        cat > "$SERVICE" <<EOF
[Unit]
Description=NewGreedy BitTorrent announce proxy v${VERSION}
After=network.target

[Service]
Type=simple
User=${ACTUAL_USER}
WorkingDirectory=${DEST}
ExecStart=${PYTHON_PATH} ${DEST}/newgreedy.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
        systemctl daemon-reload
        systemctl enable newgreedy &>/dev/null
        ok "systemd service created and enabled"
    else
        systemctl daemon-reload
        systemctl restart newgreedy &>/dev/null || true
        ok "Service restarted"
    fi
fi

# ── 9. macOS launchd (optional auto-start) ────────────────────────────────
if [ "$OS" = "macos" ] && ! $UPDATE; then
    PLIST="$HOME/Library/LaunchAgents/com.newgreedy.plist"
    PYTHON_PATH=$(command -v $PYTHON)
    info "Creating launchd agent for auto-start at login..."
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.newgreedy</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_PATH}</string>
        <string>${DEST}/newgreedy.py</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>WorkingDirectory</key><string>${DEST}</string>
    <key>StandardOutPath</key><string>${DEST}/newgreedy.log</string>
    <key>StandardErrorPath</key><string>${DEST}/newgreedy.log</string>
</dict>
</plist>
EOF
    launchctl load "$PLIST" 2>/dev/null && ok "launchd agent created" || warn "launchd load failed — start manually"
fi

# ── 10. Summary ───────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}────────────────────────────────────${NC}"
echo -e "${GREEN}${BOLD}NewGreedy v${VERSION} installation complete!${NC}"
echo ""
echo -e "  Proxy:   ${CYAN}127.0.0.1:3456${NC}"
echo -e "  Web UI:  ${CYAN}http://localhost:8080${NC}"
echo -e "  Config:  ${CYAN}${DEST}/config.ini${NC}"
echo -e "  Logs:    ${CYAN}${DEST}/newgreedy.log${NC}"
echo ""
if [ "$OS" = "linux" ] && command -v systemctl &>/dev/null; then
    echo -e "  Start:   ${YELLOW}sudo systemctl start newgreedy${NC}"
    echo -e "  Status:  ${YELLOW}sudo systemctl status newgreedy${NC}"
    echo -e "  Stop:    ${YELLOW}sudo systemctl stop newgreedy${NC}"
else
    echo -e "  Start:   ${YELLOW}python3 ${DEST}/newgreedy.py${NC}"
fi
echo ""
echo -e "${YELLOW}[!] Don't forget: disable UDP trackers in your torrent client.${NC}"
echo -e "${YELLOW}[!] Set HTTP proxy to 127.0.0.1:3456 in your client settings.${NC}"
echo "────────────────────────────────────"
