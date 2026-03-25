#!/usr/bin/env bash
# ============================================================
#  NewGreedy v1.3 -- install.sh
#  Single-file installer / updater
#  Usage:
#    sudo ./install.sh          # fresh install
#    sudo ./install.sh --update # pull latest from GitHub + restart
# ============================================================
set -euo pipefail

# -- Constants ------------------------------------------------
REPO_URL="https://github.com/Mrt0t0/NewGreedy.git"
INSTALL_DIR="/opt/newgreedy"
SERVICE_NAME="newgreedy"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
PYTHON="${PYTHON:-python3}"
CA_DIR="${HOME}/.mitmproxy"
CA_SRC="${CA_DIR}/mitmproxy-ca-cert.pem"
CA_DST_DEBIAN="/usr/local/share/ca-certificates/mitmproxy-newgreedy.crt"
CA_DST_RHEL="/etc/pki/ca-trust/source/anchors/mitmproxy-newgreedy.crt"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
section() { echo -e "\n${BLUE}==> $*${NC}"; }

# -- Root check -----------------------------------------------
require_root() {
    if [[ $EUID -ne 0 ]]; then
        error "This script must be run as root (sudo ./install.sh)"
        exit 1
    fi
}

# -- Detect OS ------------------------------------------------
detect_os() {
    if command -v apt-get &>/dev/null; then OS_FAMILY="debian"
    elif command -v dnf &>/dev/null;    then OS_FAMILY="rhel"
    elif command -v yum &>/dev/null;    then OS_FAMILY="rhel"
    elif [[ "$(uname)" == "Darwin" ]];  then OS_FAMILY="macos"
    else OS_FAMILY="unknown"; fi
    info "OS family: ${OS_FAMILY}"
}

# -- Python check ---------------------------------------------
check_python() {
    if ! command -v "${PYTHON}" &>/dev/null; then
        error "Python3 not found. Install it first."
        exit 1
    fi
    local ver
    ver="$("${PYTHON}" -c 'import sys; print(sys.version_info[:2])')"
    info "Python: ${PYTHON} (${ver})"
    if ! "${PYTHON}" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)'; then
        error "Python 3.9+ required."
        exit 1
    fi
}

# -- Install system packages -----------------------------------
install_system_deps() {
    section "Installing system dependencies"
    case "${OS_FAMILY}" in
        debian)
            apt-get update -qq
            apt-get install -y -qq git python3-pip ca-certificates curl
            ;;
        rhel)
            dnf install -y git python3-pip ca-certificates curl 2>/dev/null \
            || yum install -y git python3-pip ca-certificates curl
            ;;
        macos)
            if command -v brew &>/dev/null; then
                brew install python git ca-certificates 2>/dev/null || true
            fi
            ;;
        *)
            warn "Unknown OS -- skipping system package install."
            ;;
    esac
}

# -- Install Python packages -----------------------------------
install_python_deps() {
    section "Installing Python dependencies"
    "${PYTHON}" -m pip install --quiet --upgrade pip
    "${PYTHON}" -m pip install --quiet mitmproxy requests
    info "mitmproxy and requests installed."
}

# -- Clone or update repo --------------------------------------
install_files() {
    section "Installing NewGreedy files"
    if [[ -d "${INSTALL_DIR}/.git" ]]; then
        info "Existing installation found -- pulling latest..."
        git -C "${INSTALL_DIR}" pull --ff-only
    elif [[ -d "${INSTALL_DIR}" ]]; then
        warn "${INSTALL_DIR} exists but is not a git repo -- overwriting files."
        cp -f newgreedy.py         "${INSTALL_DIR}/"
        cp -f newgreedy_addon.py   "${INSTALL_DIR}/"
        cp -f config.ini           "${INSTALL_DIR}/config.ini.default"
    else
        info "Cloning from ${REPO_URL}..."
        git clone --depth 1 "${REPO_URL}" "${INSTALL_DIR}"
    fi

    # Preserve existing config.ini -- never overwrite
    if [[ ! -f "${INSTALL_DIR}/config.ini" ]]; then
        cp "${INSTALL_DIR}/config.ini.default" "${INSTALL_DIR}/config.ini" 2>/dev/null \
        || cp "${INSTALL_DIR}/config.ini"       "${INSTALL_DIR}/config.ini" 2>/dev/null \
        || true
        info "config.ini created from default."
    else
        info "config.ini already exists -- not overwritten."
    fi

    chmod 755 "${INSTALL_DIR}/newgreedy.py"
    chmod 755 "${INSTALL_DIR}/newgreedy_addon.py"
    info "Files installed to ${INSTALL_DIR}"
}

# -- Generate mitmproxy CA -------------------------------------
generate_ca() {
    section "Generating mitmproxy CA certificate"
    # Run mitmdump briefly just to generate the CA
    if [[ ! -f "${CA_SRC}" ]]; then
        info "Running mitmdump to generate CA (will stop after 3s)..."
        mitmdump --quiet &
        MITM_PID=$!
        sleep 3
        kill "${MITM_PID}" 2>/dev/null || true
        wait "${MITM_PID}" 2>/dev/null || true
    fi

    if [[ ! -f "${CA_SRC}" ]]; then
        error "CA file not generated at ${CA_SRC}. Run 'mitmdump' once manually."
        exit 1
    fi
    info "CA found at ${CA_SRC}"
}

# -- Install CA into system trust store ------------------------
install_ca() {
    section "Installing mitmproxy CA into system trust store"
    case "${OS_FAMILY}" in
        debian)
            cp "${CA_SRC}" "${CA_DST_DEBIAN}"
            update-ca-certificates
            info "CA installed (Debian/Ubuntu)."
            ;;
        rhel)
            cp "${CA_SRC}" "${CA_DST_RHEL}"
            update-ca-trust extract
            info "CA installed (RHEL/CentOS)."
            ;;
        macos)
            security add-trusted-cert -d -r trustRoot \
                -k /Library/Keychains/System.keychain "${CA_SRC}" || true
            info "CA installed (macOS keychain)."
            ;;
        *)
            warn "Unknown OS -- CA not added automatically."
            warn "Add ${CA_SRC} to your system trust store manually."
            ;;
    esac
}

# -- Create systemd service ------------------------------------
create_service() {
    section "Creating systemd service"

    if [[ "${OS_FAMILY}" == "macos" ]]; then
        warn "systemd not available on macOS -- use launchd or run manually."
        info "Manual start: cd ${INSTALL_DIR} && ${PYTHON} newgreedy.py"
        return
    fi

    if ! command -v systemctl &>/dev/null; then
        warn "systemctl not found -- skipping service creation."
        info "Manual start: cd ${INSTALL_DIR} && ${PYTHON} newgreedy.py"
        return
    fi

    # Read port from config.ini
    PORT=$(grep -E "^\s*listen_port\s*=" "${INSTALL_DIR}/config.ini" 2>/dev/null \
           | head -1 | awk -F= '{print $2}' | tr -d ' ' || echo "3456")

    cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=NewGreedy v1.3 - BitTorrent announce proxy
Documentation=https://github.com/Mrt0t0/NewGreedy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SUDO_USER:-root}
WorkingDirectory=${INSTALL_DIR}
ExecStart=${PYTHON} ${INSTALL_DIR}/newgreedy.py
Restart=on-failure
RestartSec=10
StandardOutput=append:${INSTALL_DIR}/newgreedy.log
StandardError=append:${INSTALL_DIR}/newgreedy.log
KillMode=mixed
TimeoutStopSec=10

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable  "${SERVICE_NAME}.service"
    systemctl restart "${SERVICE_NAME}.service"

    sleep 2
    if systemctl is-active --quiet "${SERVICE_NAME}.service"; then
        info "Service started on port ${PORT} (HTTP + HTTPS)."
    else
        warn "Service may have failed -- check: journalctl -u ${SERVICE_NAME}.service -n 50"
    fi
}

# -- Update only -----------------------------------------------
do_update() {
    require_root
    section "Updating NewGreedy"

    if [[ ! -d "${INSTALL_DIR}/.git" ]]; then
        error "${INSTALL_DIR} is not a git repository. Run install first."
        exit 1
    fi

    info "Pulling latest from GitHub..."
    git -C "${INSTALL_DIR}" fetch origin
    git -C "${INSTALL_DIR}" pull --ff-only

    info "Upgrading Python dependencies..."
    "${PYTHON}" -m pip install --quiet --upgrade mitmproxy requests

    if command -v systemctl &>/dev/null \
       && systemctl is-enabled --quiet "${SERVICE_NAME}.service" 2>/dev/null; then
        info "Restarting service..."
        systemctl restart "${SERVICE_NAME}.service"
        sleep 2
        if systemctl is-active --quiet "${SERVICE_NAME}.service"; then
            info "Service restarted OK."
        else
            warn "Service restart failed -- check: journalctl -u ${SERVICE_NAME}.service -n 50"
        fi
    else
        warn "Service not managed by systemd -- restart manually."
    fi

    CURRENT_VERSION=$("${PYTHON}" -c \
        "import sys; sys.path.insert(0,'${INSTALL_DIR}'); \
         import importlib.util; \
         spec=importlib.util.spec_from_file_location('m','${INSTALL_DIR}/newgreedy_addon.py'); \
         m=importlib.util.module_from_spec(spec); \
         print(getattr(m,'VERSION','unknown'))" 2>/dev/null || echo "unknown")
    info "Update complete -- NewGreedy v${CURRENT_VERSION}"
}

# -- Print final instructions ----------------------------------
print_summary() {
    PORT=$(grep -E "^\s*listen_port\s*=" "${INSTALL_DIR}/config.ini" 2>/dev/null \
           | head -1 | awk -F= '{print $2}' | tr -d ' ' || echo "3456")

    echo
    echo -e "${GREEN}+==========================================================+${NC}"
    echo -e "${GREEN}|         NewGreedy v1.3 -- Installation complete          |${NC}"
    echo -e "${GREEN}+==========================================================+${NC}"
    echo
    echo -e "  Proxy port (HTTP + HTTPS) : ${YELLOW}127.0.0.1:${PORT}${NC}"
    echo -e "  Install directory         : ${INSTALL_DIR}"
    echo -e "  Config file               : ${INSTALL_DIR}/config.ini"
    echo -e "  Log file                  : ${INSTALL_DIR}/newgreedy.log"
    echo
    echo -e "${BLUE}Configure your torrent client:${NC}"
    echo -e "  qBittorrent: Settings -> Connection -> Proxy"
    echo -e "    Type : HTTP"
    echo -e "    Host : 127.0.0.1"
    echo -e "    Port : ${PORT}"
    echo -e "    [x] Use proxy for tracker communication"
    echo
    echo -e "${BLUE}Useful commands:${NC}"
    echo -e "  View logs       : tail -f ${INSTALL_DIR}/newgreedy.log"
    echo -e "  Service status  : systemctl status ${SERVICE_NAME}.service"
    echo -e "  Reload config   : sudo kill -HUP \$(systemctl show --property MainPID ${SERVICE_NAME}.service | cut -d= -f2)"
    echo -e "  Update          : sudo ./install.sh --update"
    echo
}

# -- Entry point -----------------------------------------------
main() {
    if [[ "${1:-}" == "--update" ]]; then
        do_update
        exit 0
    fi

    require_root
    detect_os
    check_python
    install_system_deps
    install_python_deps
    install_files
    generate_ca
    install_ca
    create_service
    print_summary
}

main "$@"
