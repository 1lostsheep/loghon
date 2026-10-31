#!/usr/bin/env bash
# Records one walk-through of the real Loghon site, so the app can be taught
# the exact buttons. A browser opens next to a "Playwright Inspector" window;
# click through a normal start and stop, then close the browser.
# The steps are saved to ~/loghon-recording.txt.
#
# Do "Log in once…" in the app first, so the recording starts logged in and
# never sees the password. Anything typed into a password field is blanked
# out anyway before the file is saved.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/loghon-kiosk"
OUT="$HOME/loghon-recording.txt"
RAW="$(mktemp)"
mkdir -p "$DATA"

"$HERE/.venv/bin/python" -m playwright codegen \
  --target python \
  --user-data-dir "$DATA/browser-profile" \
  --output "$RAW" \
  https://www.loghon.com/

# Blank any value typed into something that looks like a password field.
sed -E '/[Pp]ass/ s/\.fill\("([^"\\]|\\.)*"\)/.fill("***")/' "$RAW" > "$OUT"
rm -f "$RAW"
echo
echo "Saved: $OUT"
echo "Check it contains no password, then send it over."
