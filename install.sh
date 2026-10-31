#!/usr/bin/env bash
# Installs Loghon Kiosk on Linux Mint (or Ubuntu). Run from the repo folder:
#   ./install.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "==> Installing system packages (asks for your password)"
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-tk python3-pip

echo "==> Creating Python environment in $HERE/.venv"
python3 -m venv "$HERE/.venv"
"$HERE/.venv/bin/pip" install --upgrade pip
"$HERE/.venv/bin/pip" install -r "$HERE/requirements.txt"

echo "==> Downloading Chromium for Playwright"
"$HERE/.venv/bin/python" -m playwright install --with-deps chromium

echo "==> Adding menu and desktop shortcuts"
APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
sed "s#@HERE@#$HERE#g" "$HERE/loghon-kiosk.desktop.in" > "$APPS/loghon-kiosk.desktop"
chmod +x "$APPS/loghon-kiosk.desktop" "$HERE/run.sh"
DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
if [ -d "$DESK" ]; then
  cp "$APPS/loghon-kiosk.desktop" "$DESK/"
  chmod +x "$DESK/loghon-kiosk.desktop"
  gio set "$DESK/loghon-kiosk.desktop" metadata::trusted true 2>/dev/null || true
fi

echo
echo "Done. Start it from the menu (\"Loghon Translation\") or the desktop icon."
echo "First time: click \"Log in once…\" at the bottom of the window and log in."
