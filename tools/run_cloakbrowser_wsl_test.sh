#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/mnt/d/Software/linuxdo-v2ex-checkin"
VENV_DIR="/tmp/cloak-wsl-venv"
ENV_FILE="$REPO_DIR/linuxdo-cloakbrowser-test.env"
RUN_LOG="/tmp/linuxdo-cloakbrowser-run-wsl.log"
PIP_UPGRADE_LOG="/tmp/cloak-wsl-pip-upgrade.log"
PIP_INSTALL_LOG="/tmp/cloak-wsl-pip-install.log"
SCREENSHOT="/tmp/linuxdo-cloakbrowser-login-test-wsl.png"

cd "$REPO_DIR"

python3 -m venv "$VENV_DIR"
. "$VENV_DIR/bin/activate"

python -m pip install -U pip setuptools wheel >"$PIP_UPGRADE_LOG" 2>&1
python -m pip install -r "$REPO_DIR/requirements.txt" cloakbrowser >"$PIP_INSTALL_LOG" 2>&1

export LINUXDO_ENV_FILE="$ENV_FILE"

set +e
python linuxdo_cloak.py --login-only --headless --screenshot "$SCREENSHOT" >"$RUN_LOG" 2>&1
STATUS=$?
set -e

echo "=== run-status ==="
echo "$STATUS"
echo "=== run-log ==="
cat "$RUN_LOG"
echo "=== screenshot ==="
ls -l "$SCREENSHOT" 2>/dev/null || true
