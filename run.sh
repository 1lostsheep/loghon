#!/usr/bin/env bash
# Starts Loghon Kiosk. Extra options: --windowed (not full screen), --visible (show the browser).
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
exec "$HERE/.venv/bin/python" -m loghon_kiosk "$@"
