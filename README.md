# Loghon Kiosk

A one-screen app for running [Loghon](https://www.loghon.com/) live translation
in church from a Linux Mint laptop.

![Running](docs/screen-running.png)

- **START** (green): opens Loghon in a hidden browser, logs in, switches on
  audio, starts the translation, allows the microphone and shows the
  translation's **QR code** full size. People in the room scan it with their
  phone camera to follow the translation.
- **STOP** (red): ends the translation on Loghon and closes the hidden browser.
  The QR code disappears, since it only works while a translation is running.

The microphone is the laptop's default input. Choose it in Mint's
*Sound* settings → *Input* (built-in mic or a plugged-in microphone/mixer feed).

## Install (once)

```bash
git clone https://github.com/1lostsheep/loghon.git ~/loghon-kiosk
```

```bash
cd ~/loghon-kiosk && ./install.sh
```

This installs Python, Tk and a private copy of Chromium, and adds a
**Loghon Translation** icon to the menu and the desktop.

## First run: log in once

1. Open **Loghon Translation**.
2. Click **Log in once…** (small button, bottom right). A normal browser
   window opens on loghon.com.
3. Log in with the church account (tick "remember me" if offered), then
   close that browser window.

The login is kept in the app's own browser profile
(`~/.local/share/loghon-kiosk/browser-profile`), so you won't have to type it again.
If you'd rather have the app type it, put `email` and `password` in
`~/.config/loghon-kiosk/config.ini` (that file is only readable by your user
and is never part of this repository).

## Every Sunday

Open the app → **START** → turn the screen to the audience → **STOP** after the talk.

Keys: `F11` toggles full screen, `Esc` leaves full screen.

## If a step doesn't work

The app finds Loghon's buttons by their visible text ("Start translation",
"Audio", "Stop", …). If Loghon uses different words, the status line at the
bottom says which step failed, and a screenshot plus the page's HTML are saved in
`~/.local/share/loghon-kiosk/logs/`.

To fix it:

1. Run the app with the browser visible, to watch what it does:

   ```bash
   ~/loghon-kiosk/run.sh --windowed --visible
   ```

2. Edit `~/.config/loghon-kiosk/config.ini`, section `[labels]`, and put
   the exact words Loghon shows on its buttons (e.g. `start_buttons = Begin translation`).
   If the start page isn't where translations are started, set `translation_url`.

Or send the screenshot from the `logs` folder to whoever maintains this repo.

## How it works

| File | Purpose |
|---|---|
| `loghon_kiosk/app.py` | Full-screen Tk window (QR code, START, STOP, status line) |
| `loghon_kiosk/driver.py` | Drives loghon.com with Playwright/Chromium on a worker thread |
| `loghon_kiosk/config.py` | Reads `~/.config/loghon-kiosk/config.ini` |
| `config.example.ini` | All settings with explanations (copied on first run) |
| `tests/test_driver.py` | End-to-end test against a mock Loghon page in `tests/mock_site/` |

The QR code is a screenshot of the code Loghon draws on its page, scaled up
with sharp edges, and refreshed every 10 seconds while running. The microphone
prompt is accepted automatically (`--use-fake-ui-for-media-stream` only
auto-accepts the prompt; it still uses the real microphone).

Run the test (needs `pip install playwright pillow` and `playwright install chromium`):

```bash
python -m tests.test_driver
```
