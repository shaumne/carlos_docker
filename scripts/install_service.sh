#!/usr/bin/env bash
set -euo pipefail

# This script installs a systemd service to run the trading stack (yf.py + trade_executor.py)
# It will:
#  - detect repo directory
#  - create/update a Python venv and install requirements
#  - run migration to SQLite
#  - create and start a systemd unit that runs scripts/run_all.py

REPO_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )"/.. && pwd -P )"
SERVICE_NAME="crypto-trading-bot.service"
LOG_DIR="$REPO_DIR/logs"

PYTHON3_BIN="$(command -v python3 || true)"
if [[ -z "${PYTHON3_BIN}" ]]; then
  echo "python3 not found. Please install Python 3.10+" >&2
  exit 1
fi

SVC_USER="${SUDO_USER:-$(whoami)}"

echo "[1/5] Ensuring virtualenv and dependencies..."
"${PYTHON3_BIN}" -m venv "$REPO_DIR/.venv"
"$REPO_DIR/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"$REPO_DIR/.venv/bin/python" -m pip install -r "$REPO_DIR/requirements.txt"

echo "[2/5] Running migration (pending Excel -> SQLite) ..."
"$REPO_DIR/.venv/bin/python" "$REPO_DIR/scripts/migrate_pending_to_db.py" || true

mkdir -p "$LOG_DIR"

echo "[3/5] Writing systemd unit: /etc/systemd/system/${SERVICE_NAME}"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}"
sudo tee "$UNIT_FILE" >/dev/null <<UNIT
[Unit]
Description=Crypto Trading Bot (signals + executor)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SVC_USER}
Group=${SVC_USER}
WorkingDirectory=${REPO_DIR}
# Load environment variables if .env exists
EnvironmentFile=-${REPO_DIR}/.env
ExecStart=/bin/bash -c "cd ${REPO_DIR} && source .venv/bin/activate && exec python scripts/run_all.py"
Restart=always
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=30
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=false
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
UNIT

echo "[4/5] Reloading systemd and enabling service ..."
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

echo "[5/5] Starting service ..."
sudo systemctl restart "$SERVICE_NAME"

echo "\nService status:" 
sudo systemctl --no-pager status "$SERVICE_NAME" || true

echo "\nFollow logs:"
echo "  tail -f ${LOG_DIR}/executor_stdout.log"
echo "  tail -f ${LOG_DIR}/yf_stdout.log"

