"""Full-screen kiosk window: big QR code, green START, red STOP."""

from __future__ import annotations

import io
import queue
import sys
import tkinter as tk
from tkinter import messagebox

from PIL import Image, ImageTk

from . import config
from .driver import LoghonDriver

BG = "#ffffff"
FG = "#111111"
GREEN = "#1e9e46"
GREEN_DIM = "#9ccfac"
RED = "#d62828"
RED_DIM = "#e8a3a3"


class KioskApp:
    def __init__(self, root: tk.Tk, settings: config.Settings):
        self.root = root
        self.s = settings
        self.events: queue.Queue = queue.Queue()
        self.qr_png: bytes | None = None
        self.state = "idle"

        root.title("Loghon Translation")
        root.configure(bg=BG)
        root.geometry("1280x800")
        if settings.fullscreen:
            root.attributes("-fullscreen", True)
        root.bind("<F11>", lambda e: root.attributes("-fullscreen", not root.attributes("-fullscreen")))
        root.bind("<Escape>", lambda e: root.attributes("-fullscreen", False))
        root.protocol("WM_DELETE_WINDOW", self.quit)

        # Left: QR code area. Right: buttons.
        root.columnconfigure(0, weight=3)
        root.columnconfigure(1, weight=1, minsize=320)
        root.rowconfigure(0, weight=1)

        left = tk.Frame(root, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=20, pady=20)
        self.title_lbl = tk.Label(left, text=settings.title, bg=BG, fg=FG,
                                  font=("DejaVu Sans", 40, "bold"))
        self.title_lbl.pack(side="top", pady=(10, 0))
        self.sub_lbl = tk.Label(left, text=settings.subtitle, bg=BG, fg="#555",
                                font=("DejaVu Sans", 22))
        self.sub_lbl.pack(side="top", pady=(0, 10))
        self.qr_lbl = tk.Label(left, bg=BG, fg="#888", font=("DejaVu Sans", 30),
                               text="Press START to begin\nthe translation")
        self.qr_lbl.pack(side="top", fill="both", expand=True)
        self.qr_lbl.bind("<Configure>", lambda e: self._render_qr())

        right = tk.Frame(root, bg=BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(0, 30), pady=40)
        right.rowconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        btn_font = ("DejaVu Sans", 36, "bold")
        self.start_btn = tk.Button(right, text="START", font=btn_font, bg=GREEN, fg="white",
                                   activebackground="#178a3b", activeforeground="white",
                                   disabledforeground="white", relief="flat", bd=0,
                                   command=self.on_start, cursor="hand2")
        self.start_btn.grid(row=0, column=0, sticky="nsew", pady=(0, 15))
        self.stop_btn = tk.Button(right, text="STOP", font=btn_font, bg=RED, fg="white",
                                  activebackground="#b51f1f", activeforeground="white",
                                  disabledforeground="white", relief="flat", bd=0,
                                  command=self.on_stop, cursor="hand2")
        self.stop_btn.grid(row=1, column=0, sticky="nsew", pady=(15, 0))

        bottom = tk.Frame(root, bg=BG)
        bottom.grid(row=1, column=0, columnspan=2, sticky="ew", padx=20, pady=(0, 10))
        self.status = tk.Label(bottom, text="Ready.", bg=BG, fg="#444", anchor="w",
                               font=("DejaVu Sans", 14))
        self.status.pack(side="left", fill="x", expand=True)
        small = dict(font=("DejaVu Sans", 11), relief="flat", bg="#eee", fg="#333", bd=0, padx=10, pady=4)
        tk.Button(bottom, text="Quit", command=self.quit, **small).pack(side="right", padx=4)
        self.setup_btn = tk.Button(bottom, text="Log in once…", command=self.on_setup, **small)
        self.setup_btn.pack(side="right", padx=4)

        if settings.show_cached_qr and settings.qr_cache.exists():
            self.qr_png = settings.qr_cache.read_bytes()
            self.sub_lbl.config(text=settings.subtitle + "  (not running yet)")

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
            if not messagebox.askyesno("Quit", "A translation is running. Stop it and quit?"):
                return
        self.status.config(text="Closing…")
        self.driver.submit("quit")

    # ---- events from the browser thread ----------------------------------
    def _poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.status.config(text=value, fg="#444")
                elif kind == "error":
                    self.status.config(text=value, fg=RED)
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
                              text={"starting": "STARTING…", "running": "RUNNING"}.get(state, "START"))
        self.stop_btn.config(state="normal" if stop_on else "disabled",
                             bg=RED if stop_on else RED_DIM,
                             text="STOPPING…" if state == "stopping" else "STOP")
        self.setup_btn.config(state="normal" if start_on else "disabled")
        if state == "idle" and not (self.s.show_cached_qr and self.qr_png):
            self.qr_png = None
            self._render_qr()

    def _render_qr(self):
        if not self.qr_png:
            self.qr_lbl.config(image="", text="Press START to begin\nthe translation")
            self.qr_lbl.image = None
            return
        w, h = self.qr_lbl.winfo_width(), self.qr_lbl.winfo_height()
        side = max(100, min(w, h) - 10)
        img = Image.open(io.BytesIO(self.qr_png)).convert("RGB")
        # Scale to fit, keeping the squares sharp so phones can read it.
        scale = side / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.NEAREST)
        photo = ImageTk.PhotoImage(img)
        self.qr_lbl.config(image=photo, text="")
        self.qr_lbl.image = photo


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
