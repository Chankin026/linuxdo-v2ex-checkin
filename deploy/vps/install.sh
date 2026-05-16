#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${1:-}"
INSTALL_DIR="${INSTALL_DIR:-/opt/linuxdo-v2ex-checkin}"
ENV_FILE="${ENV_FILE:-/etc/linuxdo-v2ex-checkin.env}"

if [[ -z "$REPO_URL" ]]; then
  echo "Usage: bash deploy/vps/install.sh <git-repo-url>"
  exit 1
fi

echo "[1/6] Install system packages..."
apt update
apt install -y \
  git python3 python3-venv python3-pip wget unzip xvfb fonts-liberation \
  libasound2 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 libc6 libcairo2 \
  libcups2 libdbus-1-3 libdrm2 libgbm1 libglib2.0-0 libgtk-3-0 libnspr4 \
  libnss3 libpango-1.0-0 libu2f-udev libvulkan1 libx11-6 libx11-xcb1 libxcb1 \
  libxcomposite1 libxdamage1 libxext6 libxfixes3 libxkbcommon0 libxrandr2 xdg-utils

echo "[2/6] Install Google Chrome..."
cd /tmp
wget -O google-chrome-stable_current_amd64.deb \
  https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
apt install -y ./google-chrome-stable_current_amd64.deb

echo "[3/6] Clone or update repo..."
if [[ -d "$INSTALL_DIR/.git" ]]; then
  git -C "$INSTALL_DIR" pull
else
  rm -rf "$INSTALL_DIR"
  git clone "$REPO_URL" "$INSTALL_DIR"
fi

echo "[4/6] Create virtualenv and install Python deps..."
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install -U pip
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

echo "[5/6] Install env file and systemd units..."
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$INSTALL_DIR/deploy/vps/linuxdo-v2ex-checkin.env.example" "$ENV_FILE"
  echo "Created $ENV_FILE"
  echo "Edit it now and set LINUXDO_COOKIES before starting the service."
fi

cp "$INSTALL_DIR/deploy/vps/linuxdo-v2ex-checkin.service" /etc/systemd/system/
cp "$INSTALL_DIR/deploy/vps/linuxdo-v2ex-checkin.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable linuxdo-v2ex-checkin.timer

echo "[6/6] Done."
echo
echo "Next steps:"
echo "1. Edit $ENV_FILE"
echo "2. Run: systemctl start linuxdo-v2ex-checkin.service"
echo "3. Check: journalctl -u linuxdo-v2ex-checkin.service -n 100 --no-pager"
echo "4. Enable timer now: systemctl enable --now linuxdo-v2ex-checkin.timer"
