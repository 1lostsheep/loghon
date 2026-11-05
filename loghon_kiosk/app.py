"""Full-screen kiosk window: big QR code, green START, red STOP."""

from __future__ import annotations

import io
import queue
import sys
import tkinter as tk
from tkinter import messagebox

import qrcode
from PIL import Image, ImageTk

from . import config
from .driver import LoghonDriver

BG = "#ffffff"
FG = "#111111"
GREEN = "#1e9e46"
GREEN_DIM = "#9ccfac"
RED = "#d62828"
RED_DIM = "#e8a3a3"
QR_PAD = 16  # white space around the QR code, in pixels


class KioskApp:
    def __init__(self, root: tk.Tk, settings: config.Settings):
        self.root = root
        self.s = settings
        self.events: queue.Queue = queue.Queue()
        self.qr_png: bytes | None = None
        self.state = "idle"

        root.title("Loghon Übersetzung")
        root.configure(bg=BG)
        root.geometry("1280x800")
        if settings.fullscreen:
            root.attributes("-fullscreen", True)
        root.bind("<F11>", lambda e: root.attributes("-fullscreen", not root.attributes("-fullscreen")))
        root.bind("<Escape>", lambda e: root.attributes("-fullscreen", False))
        root.protocol("WM_DELETE_WINDOW", self.quit)

        # Left: the QR code, as tall as the window (people scan it from
        # several metres away). Right: texts for the audience and the buttons.
        root.columnconfigure(0, weight=0)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        self.qr_lbl = tk.Label(root, bg=BG, fg="#888", font=("DejaVu Sans", 30),
                               text="START drücken, um die\nÜbersetzung zu beginnen")
        self.qr_lbl.grid(row=0, column=0, sticky="nsew", padx=(QR_PAD, 10), pady=QR_PAD)
        root.bind("<Configure>", self._on_resize)
        self._qr_side = 0

        right = tk.Frame(root, bg=BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(10, 30), pady=30)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)
        right.rowconfigure(4, weight=1)

        self.title_lbl = tk.Label(right, text=settings.title, bg=BG, fg=FG, justify="center",
                                  font=("DejaVu Sans", 34, "bold"))
        self.title_lbl.grid(row=0, column=0, sticky="ew")
        self.sub_lbl = tk.Label(right, text=settings.subtitle, bg=BG, fg="#555", justify="center",
                                font=("DejaVu Sans", 20))
        self.sub_lbl.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        # The link in plain text, for phones that cannot scan.
        self.url_lbl = tk.Label(right, text=settings.viewer_url.split("://", 1)[-1],
                                bg=BG, fg=FG, font=("DejaVu Sans", 20, "bold"))
        self.url_lbl.grid(row=2, column=0, sticky="ew", pady=(14, 20))
        for lbl in (self.title_lbl, self.sub_lbl, self.url_lbl):
            lbl.bind("<Configure>", lambda e, l=lbl: l.config(wraplength=max(200, e.width - 10)))

        btn_font = ("DejaVu Sans", 34, "bold")
        self.start_btn = tk.Button(right, text="START", font=btn_font, bg=GREEN, fg="white",
                                   activebackground="#178a3b", activeforeground="white",
                                   disabledforeground="white", relief="flat", bd=0,
                                   command=self.on_start, cursor="hand2")
        self.start_btn.grid(row=3, column=0, sticky="nsew", pady=(0, 10))
        self.stop_btn = tk.Button(right, text="STOP", font=btn_font, bg=RED, fg="white",
                                  activebackground="#b51f1f", activeforeground="white",
                                  disabledforeground="white", relief="flat", bd=0,
                                  command=self.on_stop, cursor="hand2")
        self.stop_btn.grid(row=4, column=0, sticky="nsew", pady=(10, 0))

        self.status = tk.Label(right, text="Bereit.", bg=BG, fg="#444", anchor="w",
                               justify="left", font=("DejaVu Sans", 13))
        self.status.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        self.status.bind("<Configure>", lambda e: self.status.config(wraplength=max(200, e.width - 10)))
        bottom = tk.Frame(right, bg=BG)
        bottom.grid(row=6, column=0, sticky="e", pady=(6, 0))
        small = dict(font=("DejaVu Sans", 11), relief="flat", bg="#eee", fg="#333", bd=0, padx=10, pady=4)
        self.setup_btn = tk.Button(bottom, text="Einmalig anmelden…", command=self.on_setup, **small)
        self.setup_btn.pack(side="left", padx=4)
        tk.Button(bottom, text="Beenden", command=self.quit, **small).pack(side="left", padx=4)

        if settings.viewer_url:
            self.qr_png = make_qr(settings.viewer_url)  # fixed: shown all the time
        elif settings.show_cached_qr and settings.qr_cache.exists():
            self.qr_png = settings.qr_cache.read_bytes()
            self.sub_lbl.config(text=settings.subtitle + "  (läuft noch nicht)")

        self.driver = LoghonDriver(settings, self.events.put)
        self._apply_state("idle")
        root.after(100, self._poll)

    # ---- buttons -------------------------------------------------------
    def on_start(self):
        if self.state != "idle":
            return
        self._apply_state("starting")
        self.driver.submit("start")

    def on_stop(self):
        if self.state not in ("running", "starting"):
            return
        self.driver.submit("stop")

    def on_setup(self):
        if self.state != "idle":
            return
        self.driver.submit("setup")

    def quit(self):
        if self.state in ("running", "starting"):
            if not messagebox.askyesno("Beenden", "Eine Übersetzung läuft. Stoppen und beenden?"):
                return
        self.status.config(text="Wird geschlossen…")
        self.driver.submit("quit")

    # ---- events from the browser thread ----------------------------------
    def _poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.status.config(text=value[:200], fg="#444")
                elif kind == "error":
                    self.status.config(text=value[:200], fg=RED)
                elif kind == "qr":
                    self.qr_png = value
                    self.sub_lbl.config(text=self.s.subtitle)
                    self._render_qr()
                elif kind == "state":
                    if value == "quit":
                        self.root.destroy()
                        return
                    self._apply_state(value)
        except queue.Empty:
            pass
        self.root.after(150, self._poll)

    def _apply_state(self, state: str):
        self.state = state
        start_on = state == "idle"
        stop_on = state in ("running", "starting")
        self.start_btn.config(state="normal" if start_on else "disabled",
                              bg=GREEN if start_on else GREEN_DIM,
                              text={"starting": "STARTET…", "running": "LÄUFT"}.get(state, "START"))
        self.stop_btn.config(state="normal" if stop_on else "disabled",
                             bg=RED if stop_on else RED_DIM,
                             text="STOPPT…" if state == "stopping" else "STOP")
        self.setup_btn.config(state="normal" if start_on else "disabled")
        if state == "idle" and not self.s.viewer_url and not (self.s.show_cached_qr and self.qr_png):
            self.qr_png = None
            self._render_qr()

    def _on_resize(self, event):
        if event.widget is not self.root:
            return
        # A square as tall as the window, but leave the right column at least
        # a third of the width for the texts and buttons.
        side = min(event.height - 2 * QR_PAD, int(event.width * 0.64) - QR_PAD - 10)
        if side != self._qr_side and side > 100:
            self._qr_side = side
            self.root.columnconfigure(0, minsize=side + QR_PAD + 10)
            self._render_qr()

    def _render_qr(self):
        if not self.qr_png:
            self.qr_lbl.config(image="", text="START drücken, um die\nÜbersetzung zu beginnen")
            self.qr_lbl.image = None
            return
        side = self._qr_side or 400
        img = Image.open(io.BytesIO(self.qr_png)).convert("RGB")
        # Scale to fit, keeping the squares sharp so phones can read it.
        scale = side / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.NEAREST)
        photo = ImageTk.PhotoImage(img)
        self.qr_lbl.config(image=photo, text="")
        self.qr_lbl.image = photo


def make_qr(url: str) -> bytes:
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    q.add_data(url)
    q.make(fit=True)
    buf = io.BytesIO()
    q.make_image(fill_color="black", back_color="white").save(buf)
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    settings = config.load()
    if "--visible" in argv:
        settings.browser_mode = "visible"
    if "--windowed" in argv:
        settings.fullscreen = False
    root = tk.Tk()
    KioskApp(root, settings)
    root.mainloop()
    return 0
