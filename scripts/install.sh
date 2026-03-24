#!/usr/bin/env bash
set -euo pipefail

# --- Configuration ---
APP_NAME="hypercore-power-manager"
SERVICE_USER="hcpowermgr"
INSTALL_DIR="/opt/${APP_NAME}"
CONFIG_DIR="/etc/${APP_NAME}"
CONFIG_FILE="${CONFIG_DIR}/config.yaml"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# --- Preflight checks ---
if [[ $EUID -ne 0 ]]; then
    echo "Error: This script must be run as root (use sudo)." >&2
    exit 1
fi

# Find uv: check system PATH first, then the invoking user's .local/bin
UV_BIN=""
if command -v uv &>/dev/null; then
    UV_BIN="$(command -v uv)"
elif [[ -n "${SUDO_USER:-}" ]] && [[ -x "/home/${SUDO_USER}/.local/bin/uv" ]]; then
    UV_BIN="/home/${SUDO_USER}/.local/bin/uv"
fi

if [[ -z "${UV_BIN}" ]]; then
    echo "Error: uv is required but not found." >&2
    echo "See https://docs.astral.sh/uv/getting-started/installation/ for install instructions." >&2
    exit 1
fi

if ! command -v rsync &>/dev/null; then
    echo "Error: rsync is required but not installed." >&2
    echo "Install it with: sudo apt install rsync" >&2
    exit 1
fi

# --- Create service user ---
if id "${SERVICE_USER}" &>/dev/null; then
    echo "User ${SERVICE_USER} already exists, skipping."
else
    useradd --system --no-create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
    echo "Created system user: ${SERVICE_USER}"
fi

# --- Install application ---
echo "Installing ${APP_NAME} to ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
rsync -a --delete \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --include='src/***' \
    --include='pyproject.toml' \
    --include='uv.lock' \
    --include='.python-version' \
    --include='README.md' \
    --exclude='*' \
    "${PROJECT_DIR}/" "${INSTALL_DIR}/"

# --- Install dependencies ---
echo "Installing dependencies..."
"${UV_BIN}" sync --frozen --no-dev --directory "${INSTALL_DIR}"

# --- Set ownership ---
chown -R root:${SERVICE_USER} "${INSTALL_DIR}"
chmod -R g+rX "${INSTALL_DIR}"

# --- Set up configuration ---
echo "Setting up configuration directory..."
install -d -m 750 -o root -g "${SERVICE_USER}" "${CONFIG_DIR}"

FRESH_INSTALL=false
if [[ -f "${CONFIG_FILE}" ]]; then
    echo "Config file already exists at ${CONFIG_FILE}, skipping."
else
    install -m 640 -o root -g "${SERVICE_USER}" \
        "${PROJECT_DIR}/config.example.yaml" "${CONFIG_FILE}"
    echo "Installed example config to ${CONFIG_FILE}"
    FRESH_INSTALL=true
fi

# --- Install service file ---
echo "Installing systemd service file..."
install -m 644 -o root -g root \
    "${PROJECT_DIR}/${APP_NAME}.service" "${SERVICE_FILE}"
systemctl daemon-reload
echo "Service file installed and systemd reloaded."

# --- Done ---
echo ""
echo "============================================"
if [[ "${FRESH_INSTALL}" == "true" ]]; then
    echo " ${APP_NAME} installed successfully"
else
    echo " ${APP_NAME} upgraded successfully"
fi
echo "============================================"
echo ""

if [[ "${FRESH_INSTALL}" == "true" ]]; then
    echo "WARNING: Config file contains placeholder values."
    echo "The service will not work until you edit it."
    echo ""
    echo "Next steps:"
    echo "  1. Edit the config file (requires root):"
    echo "       ${CONFIG_FILE}"
    echo "  2. Enable the service to start on boot:"
    echo "       sudo systemctl enable ${APP_NAME}"
    echo "  3. Start the service:"
    echo "       sudo systemctl start ${APP_NAME}"
    echo "  4. Check status:"
    echo "       sudo systemctl status ${APP_NAME}"
    echo "       journalctl -u ${APP_NAME} -f"
else
    echo "The service file and application have been updated."
    echo ""
    echo "If the service is running, restart it to pick up changes:"
    echo "    sudo systemctl restart ${APP_NAME}"
    echo ""
    echo "Check status:"
    echo "    sudo systemctl status ${APP_NAME}"
    echo "    journalctl -u ${APP_NAME} -f"
fi
