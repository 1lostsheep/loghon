"""Drives the Loghon website in a Chromium window controlled by Playwright.

All Playwright calls happen on one worker thread (Playwright's sync API is
not thread-safe). The UI talks to it through `LoghonDriver.submit()` and
receives events through the `on_event` callback.

The Loghon page layout is not known in advance, so every button is found by
its visible text (configurable in config.ini). When a step fails, a
screenshot and the page HTML are saved to the log folder so the labels can
be adjusted.
"""

from __future__ import annotations

import queue
import subprocess
import re
import threading
import time
import traceback
from pathlib import Path
from typing import Callable

from playwright.sync_api import (
    BrowserContext,
    Locator,
    Page,
    TimeoutError as PWTimeout,
    sync_playwright,
)

from .config import Settings

Event = tuple  # ("status", text) | ("state", name) | ("qr", png_bytes) | ("error", text)


CMD_DE = {"start": "Start", "stop": "Stopp", "setup": "Anmeldung", "quit": "Beenden"}


class StepFailed(Exception):
    pass


class LoghonDriver:
    def __init__(self, settings: Settings, on_event: Callable[[Event], None]):
        self.s = settings
        self.emit = on_event
        self._cmds: queue.Queue[str] = queue.Queue()
        self._pw = None
        self._ctx: BrowserContext | None = None
        self._page: Page | None = None
        self._running = False  # a translation is live
        self._last_qr: bytes | None = None
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    # ---- public API (any thread) -------------------------------------
    def submit(self, cmd: str) -> None:
        """cmd is one of: start, stop, setup, quit"""
        self._cmds.put(cmd)

    # ---- worker thread -----------------------------------------------
    def _loop(self) -> None:
        while True:
            try:
                cmd = self._cmds.get(timeout=self.s.qr_refresh_seconds)
            except queue.Empty:
                if self._running:
                    self._watch()
                continue
            try:
                if cmd == "start":
                    self._start()
                elif cmd == "stop":
                    self._stop()
                elif cmd == "setup":
                    self._setup()
                elif cmd == "quit":
                    if not self._safe_stop_on_quit():
                        continue  # keep the app open until Loghon is stopped
                    self._close_browser()
                    self.emit(("state", "quit"))
                    return
            except Exception as e:  # report everything to the UI
                self._dump_debug(f"{cmd}-failed")
                self.emit(("error", f"{CMD_DE.get(cmd, cmd)} fehlgeschlagen: {e}"))
                traceback.print_exc()
                if cmd == "start":
                    self._running = False
                    self.emit(("state", "idle"))

    # ---- browser lifecycle -------------------------------------------
    def _open_browser(self, visible: bool) -> Page:
        if self._ctx is not None:
            try:
                if self._page and not self._page.is_closed():
                    return self._page
            except Exception:
                pass
            self._close_browser()

        self._pw = sync_playwright().start()
        args = [
            # Accept the microphone prompt automatically (uses the system
            # default input device, chosen in Linux Mint's Sound settings).
            "--use-fake-ui-for-media-stream",
            "--autoplay-policy=no-user-gesture-required",
            "--disable-features=Translate",
        ]
        args += self.s.extra_args
        headless = False
        if not visible:
            if self.s.browser_mode == "headless":
                headless = True
            elif self.s.browser_mode == "minimized":
                args.append("--start-minimized")
            elif self.s.browser_mode == "offscreen":
                args += ["--window-position=-3000,0", "--window-size=1280,900"]

        launch = dict(
            user_data_dir=str(self.s.profile_dir),
            headless=headless,
            args=args,
            permissions=["microphone"],
            viewport={"width": 1280, "height": 900},
            no_viewport=False,
        )
        if self.s.chrome_path:
            launch["executable_path"] = self.s.chrome_path
        elif self.s.chrome_channel:
            launch["channel"] = self.s.chrome_channel
        self._ctx = self._pw.chromium.launch_persistent_context(**launch)
        self._ctx.set_default_timeout(self.s.step_timeout_seconds * 1000)
        # Accept "Are you sure you want to stop?" style JS dialogs.
        self._ctx.on("page", lambda p: p.on("dialog", lambda d: d.accept()))
        page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        page.on("dialog", lambda d: d.accept())
        self._page = page
        return page

    @staticmethod
    def _playwright_chromium_path() -> str:
        with sync_playwright() as pw:
            return pw.chromium.executable_path

    def _close_browser(self) -> None:
        try:
            if self._ctx:
                self._ctx.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._ctx = self._pw = self._page = None

    # ---- setup: let a person log in by hand once ---------------------
    def _setup(self) -> None:
        """Open a plain browser window (not remote-controlled) on the app's
        own profile, so a person can log in and pass Loghon's Cloudflare
        "I'm human" check. A browser under Playwright control fails that
        check every time. The saved login is reused by START afterwards."""
        self._close_browser()
        self.emit(("state", "setup"))
        self.emit(("status", "Browser ist offen. Bei Loghon anmelden, dann das Browserfenster schließen."))
        self._run_plain_browser()
        self.emit(("status", "Anmeldung gespeichert. START drücken, wenn es losgeht."))
        self.emit(("state", "idle"))

    def _run_plain_browser(self) -> None:
        exe = self.s.chrome_path or self._playwright_chromium_path()
        proc = subprocess.Popen([
            exe,
            f"--user-data-dir={self.s.profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            # Same cookie storage as Playwright uses, so START can read the
            # login saved here (otherwise it is encrypted with the keyring).
            "--password-store=basic",
            "--use-mock-keychain",
            self.s.start_url,
        ])
        proc.wait()  # until the person closes the window

    # ---- start --------------------------------------------------------
    def _start(self) -> None:
        if self._running:
            self.emit(("status", "Die Übersetzung läuft bereits."))
            return
        self.emit(("state", "starting"))
        self.emit(("status", "Loghon wird geöffnet…"))
        self._last_qr = None  # the window cleared it after the last stop
        page = self._open_browser(visible=self.s.show_browser)
        page.goto(self.s.start_url, wait_until="domcontentloaded")
        self._settle(page)

        self.emit(("status", "Anmeldung…"))
        page = self._login_if_needed(page)

        if self.s.translation_url:
            page.goto(self.s.translation_url, wait_until="domcontentloaded")
            self._settle(page)

        # Some sites put "New translation" / "Go to dashboard" before the settings.
        for text in self.s.pre_start_buttons:
            loc = self._find_clickable(page, [text], timeout=2)
            if loc:
                loc.click()
                self._settle(page)

        self.emit(("status", "Audio wird eingeschaltet…"))
        self._enable_audio(page)

        self.emit(("status", "Übersetzung wird gestartet…"))
        btn = self._find_clickable(page, self.s.start_buttons, timeout=self.s.step_timeout_seconds)
        if not btn:
            raise StepFailed("Start-Knopf nicht gefunden "
                             f"(gesucht: {', '.join(self.s.start_buttons)})")
        btn.click()
        self._settle(page)

        # Loghon then shows "Session Setup" (microphone choice) with a
        # "Start Recording" button; it can take a few seconds to appear.
        if self.s.after_start_buttons:
            self.emit(("status", "Aufnahme wird gestartet…"))
            loc = self._find_clickable(page, self.s.after_start_buttons,
                                       timeout=self.s.step_timeout_seconds)
            if loc:
                loc.click()
                self._settle(page)
            else:
                self._dump_debug("no-start-recording")

        self.emit(("status", "Warte auf den QR-Code…"))
        png = self._grab_qr(page, timeout=self.s.step_timeout_seconds)
        if png is None:
            self._dump_debug("no-qr")
            self.emit(("status", "Übersetzung gestartet, aber der QR-Code wurde auf der Seite nicht gefunden."))
        else:
            self._publish_qr(png)
            self.emit(("status", "Die Übersetzung läuft. QR-Code scannen."))
        self._running = True
        self.emit(("state", "running"))

    def _login_if_needed(self, page: Page) -> Page:
        pw = page.locator("input[type=password]")
        if not self._visible(pw):
            # Maybe a "Log in" link must be clicked first; skip if we are
            # already logged in (persistent profile).
            if self._find_clickable(page, self.s.start_buttons, timeout=1):
                return page
            link = self._find_clickable(page, self.s.login_buttons, timeout=3)
            if link:
                link.click()
                self._settle(page)
        if not self._visible(pw, wait=3):
            return page  # already logged in
        if self.s.email and self.s.password:
            self._fill_login(page, pw)
            if self._gone(pw, timeout=10):
                self._settle(page)
                return page
        # Loghon's login has a Cloudflare "I'm human" check (Turnstile), which
        # always fails in a remote-controlled browser. Close it, let the
        # person log in in a plain window, then carry on with the saved login.
        self._close_browser()
        self.emit(("status", "Loghon verlangt eine neue Anmeldung. Im Fenster, das sich öffnet, anmelden "
                             "und es dann schließen. START macht danach von selbst weiter."))
        self._run_plain_browser()
        page = self._open_browser(visible=self.s.show_browser)
        page.goto(self.s.start_url, wait_until="domcontentloaded")
        self._settle(page)
        if self._visible(page.locator("input[type=password]"), wait=3):
            raise StepFailed("immer noch nicht bei Loghon angemeldet")
        return page

    def _fill_login(self, page: Page, pw: Locator) -> None:
        user = page.locator(
            "input[type=email], input[name*=mail i], input[id*=mail i], "
            "input[name*=user i], input[autocomplete=username]"
        ).first
        if not self._visible(user):
            user = page.locator("input:not([type=password]):not([type=hidden])"
                                ":not([type=checkbox]):not([type=submit])").first
        user.fill(self.s.email)
        pw.first.fill(self.s.password)
        page.wait_for_timeout(3000)  # give the "I'm human" check a moment to pass
        # Prefer the submit button inside the login form, so a "Log in" link
        # in the page header is not clicked instead.
        form = page.locator("form").filter(has=page.locator("input[type=password]"))
        submit = None
        if form.count():
            inner = form.first.locator("button[type=submit], input[type=submit], button:not([type])")
            if self._visible(inner):
                submit = inner.first
        if submit is None:
            submit = self._find_clickable(page, self.s.login_submit_buttons, timeout=2)
        if submit:
            submit.click()
        else:
            pw.first.press("Enter")

    @staticmethod
    def _gone(loc: Locator, timeout: float) -> bool:
        try:
            loc.first.wait_for(state="hidden", timeout=timeout * 1000)
            return True
        except PWTimeout:
            return False

    def _enable_audio(self, page: Page) -> None:
        if not self.s.audio_labels:
            return
        for label in self.s.audio_labels:
            rx = re.compile(re.escape(label), re.I)
            candidates = [
                page.get_by_role("switch", name=rx),
                page.get_by_role("checkbox", name=rx),
                page.get_by_label(rx),
            ]
            for loc in candidates:
                if not self._visible(loc, wait=1):
                    continue
                loc = loc.first
                try:
                    checked = loc.is_checked()
                except Exception:
                    checked = (loc.get_attribute("aria-checked") or "").lower() == "true"
                if not checked:
                    try:
                        loc.check()
                    except Exception:
                        loc.click(force=True)
                return
        # Fallback: a plain button/text like "Audio" that toggles.
        loc = self._find_clickable(page, self.s.audio_labels, timeout=2)
        if loc:
            pressed = (loc.get_attribute("aria-pressed") or "").lower()
            if pressed != "true":
                loc.click()
            return
        self._dump_debug("no-audio-toggle")
        self.emit(("status", "Audio-Schalter nicht gefunden; es geht ohne ihn weiter."))

    # ---- QR code --------------------------------------------------------
    def _grab_qr(self, page: Page, timeout: float) -> bytes | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            loc = self._find_qr(page)
            if loc is not None:
                try:
                    loc.scroll_into_view_if_needed(timeout=2000)
                    page.wait_for_timeout(300)  # let the code finish drawing
                    return loc.screenshot()
                except Exception:
                    pass
            # A late "Session Setup" box still needs its "Start Recording".
            late = self._find_clickable(page, self.s.after_start_buttons, timeout=0)
            if late:
                try:
                    late.click()
                except Exception:
                    pass
            # Some sites hide the QR behind a "Share" / "Show QR" button.
            btn = self._find_clickable(page, self.s.show_qr_buttons, timeout=1)
            if btn:
                try:
                    btn.click()
                except Exception:
                    pass
            page.wait_for_timeout(700)
        return None

    def _find_qr(self, page: Page) -> Locator | None:
        selectors = list(self.s.qr_selectors) + [
            "img[alt*=qr i]", "img[src*=qr i]", "img[class*=qr i]", "img[id*=qr i]",
            "[class*=qr i] canvas", "[id*=qr i] canvas", "canvas[class*=qr i]",
            "[class*=qr i] svg", "[id*=qr i] svg", "svg[class*=qr i]",
            "[class*=qr i] img", "[id*=qr i] img",
        ]
        for frame in page.frames:
            for sel in selectors:
                try:
                    loc = frame.locator(sel)
                    n = loc.count()
                except Exception:
                    continue
                for i in range(min(n, 5)):
                    item = loc.nth(i)
                    try:
                        box = item.bounding_box()
                    except Exception:
                        box = None
                    # A real QR code is square-ish and not tiny.
                    if box and box["width"] >= 60 and abs(box["width"] - box["height"]) < box["width"] * 0.25:
                        return item
        return None

    def _publish_qr(self, png: bytes) -> None:
        if png != self._last_qr:
            self._last_qr = png
            try:
                self.s.qr_cache.write_bytes(png)
            except Exception:
                pass
            self.emit(("qr", png))

    def _watch(self) -> None:
        """While running: keep the QR current and notice if the page died."""
        page = self._page
        if page is None or page.is_closed():
            self._running = False
            self.emit(("error", "Das Loghon-Browserfenster wurde geschlossen."))
            self.emit(("state", "idle"))
            return
        try:
            loc = self._find_qr(page)
            if loc is not None:
                self._publish_qr(loc.screenshot())
        except Exception:
            pass

    # ---- stop -------------------------------------------------------------
    def _stop(self) -> bool:
        """Press Loghon's own stop button. Returns True once the translation
        has ended. The browser is only closed after that: closing it alone
        may leave the translation running on Loghon's side."""
        page = self._page
        if page is None or page.is_closed():
            self._running = False
            self.emit(("state", "idle"))
            self.emit(("status", "Es läuft nichts."))
            return True
        self.emit(("state", "stopping"))
        self.emit(("status", "Übersetzung wird gestoppt…"))
        btn = self._find_clickable(page, self.s.stop_buttons, timeout=self.s.step_timeout_seconds)
        if btn:
            btn.click()
            self._settle(page)
            for text in self.s.confirm_stop_buttons:
                loc = self._find_clickable(page, [text], timeout=2)
                if loc:
                    loc.click()
                    self._settle(page)
                    break
        if not btn or not self._stop_button_gone(page):
            self._dump_debug("stop-not-confirmed")
            self._show_browser_window(page)
            self.emit(("state", "running"))
            self.emit(("error", "Konnte nicht bestätigen, dass die Übersetzung gestoppt ist. Das Loghon-Fenster "
                                "ist jetzt sichtbar: dort Stopp drücken, dann hier noch einmal STOPP."))
            return False
        self._close_browser()  # also releases the microphone
        self._running = False
        self.emit(("state", "idle"))
        self.emit(("status", "Übersetzung gestoppt. START drücken für die nächste."))
        return True

    def _stop_button_gone(self, page: Page, timeout: float = 10) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._find_clickable(page, self.s.stop_buttons, timeout=0) is None:
                return True
            page.wait_for_timeout(500)
        return False

    def _show_browser_window(self, page: Page) -> None:
        """Move the off-screen/minimized Loghon window onto the screen."""
        try:
            cdp = self._ctx.new_cdp_session(page)
            win = cdp.send("Browser.getWindowForTarget")["windowId"]
            cdp.send("Browser.setWindowBounds", {"windowId": win, "bounds": {"windowState": "normal"}})
            cdp.send("Browser.setWindowBounds", {"windowId": win, "bounds": {
                "left": 40, "top": 40, "width": 1280, "height": 900}})
            page.bring_to_front()
        except Exception:
            pass  # headless mode has no window to show

    def _safe_stop_on_quit(self) -> bool:
        if not self._running:
            return True
        try:
            return self._stop()
        except Exception:
            return False

    # ---- helpers ------------------------------------------------------------
    def _settle(self, page: Page) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        page.wait_for_timeout(500)

    @staticmethod
    def _visible(loc: Locator, wait: float = 0) -> bool:
        try:
            if wait:
                loc.first.wait_for(state="visible", timeout=wait * 1000)
            return loc.first.is_visible()
        except Exception:
            return False

    def _find_clickable(self, page: Page, texts: list[str], timeout: float) -> Locator | None:
        """First visible, enabled button/link whose text matches one of `texts`."""
        deadline = time.time() + timeout
        while True:
            for text in texts:
                exact = re.compile(rf"^\s*{re.escape(text)}\s*$", re.I)
                loose = re.compile(re.escape(text), re.I)
                for frame in page.frames:
                    locs = [
                        frame.get_by_role("button", name=exact),
                        frame.get_by_role("link", name=exact),
                    ]
                    # Single words like "Start" only match exactly, so a
                    # "Get started" banner is never clicked by mistake.
                    if len(text.split()) > 1:
                        locs += [
                            frame.get_by_role("button", name=loose),
                            frame.get_by_role("link", name=loose),
                        ]
                    locs += [
                        frame.locator(f"input[type=submit][value='{text}' i], input[type=button][value='{text}' i]"),
                        frame.get_by_text(exact),
                    ]
                    for loc in locs:
                        try:
                            n = loc.count()
                        except Exception:
                            continue
                        for i in range(min(n, 5)):
                            item = loc.nth(i)
                            try:
                                if item.is_visible() and item.is_enabled():
                                    return item
                            except Exception:
                                continue
            if time.time() >= deadline:
                return None
            page.wait_for_timeout(400)

    def _dump_debug(self, tag: str) -> None:
        page = self._page
        if page is None:
            return
        try:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            base: Path = self.s.log_dir / f"{stamp}-{tag}"
            page.screenshot(path=str(base) + ".png", full_page=True)
            Path(str(base) + ".html").write_text(page.content(), encoding="utf-8")
            self.emit(("status", f"Bildschirmfoto zur Fehlersuche gespeichert: {base}.png"))
        except Exception:
            pass
