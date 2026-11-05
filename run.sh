#!/usr/bin/env bash
# Starts Loghon Kiosk. Extra options: --windowed (not full screen), --visible (show the browser).
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
# After a "git pull" that changed requirements.txt, install the new packages.
STAMP="$HERE/.venv/.requirements"
if ! cmp -s "$HERE/requirements.txt" "$STAMP"; then
  "$HERE/.venv/bin/pip" install -q -r "$HERE/requirements.txt" && cp "$HERE/requirements.txt" "$STAMP"
fi
exec "$HERE/.venv/bin/python" -m loghon_kiosk "$@"
