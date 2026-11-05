"""Settings, read from ~/.config/loghon-kiosk/config.ini (created on first run)."""

from __future__ import annotations

import configparser
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "loghon-kiosk"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "loghon-kiosk"
CONFIG_FILE = CONFIG_DIR / "config.ini"
EXAMPLE_FILE = Path(__file__).resolve().parent.parent / "config.example.ini"


def _list(value: str) -> list[str]:
    """Comma- or newline-separated list; blanks dropped."""
    parts = [p.strip() for chunk in value.splitlines() for p in chunk.split(",")]
    return [p for p in parts if p]


@dataclass
class Settings:
    start_url: str = "https://app.loghon.com/"
    translation_url: str = ""
    email: str = ""
    password: str = ""

    title: str = "Live-Übersetzung"
    subtitle: str = "QR-Code mit der Handykamera scannen"
    fullscreen: bool = True
    show_cached_qr: bool = True

    browser_mode: str = "offscreen"  # offscreen | minimized | headless | visible
    chrome_path: str = ""
    chrome_channel: str = ""
    extra_args: list[str] = field(default_factory=list)
    step_timeout_seconds: float = 30
    qr_refresh_seconds: float = 10

    login_buttons: list[str] = field(default_factory=list)
    login_submit_buttons: list[str] = field(default_factory=list)
    pre_start_buttons: list[str] = field(default_factory=list)
    audio_labels: list[str] = field(default_factory=list)
    start_buttons: list[str] = field(default_factory=list)
    after_start_buttons: list[str] = field(default_factory=list)
    show_qr_buttons: list[str] = field(default_factory=list)
    qr_selectors: list[str] = field(default_factory=list)
    stop_buttons: list[str] = field(default_factory=list)
    confirm_stop_buttons: list[str] = field(default_factory=list)

    profile_dir: Path = DATA_DIR / "browser-profile"
    log_dir: Path = DATA_DIR / "logs"
    qr_cache: Path = DATA_DIR / "last-qr.png"

    @property
    def show_browser(self) -> bool:
        return self.browser_mode == "visible"


def load(path: Path | None = None) -> Settings:
    path = path or CONFIG_FILE
    if not path.exists() and path == CONFIG_FILE:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy(EXAMPLE_FILE, CONFIG_FILE)
        os.chmod(CONFIG_FILE, 0o600)  # it can hold the password

    cp = configparser.ConfigParser(interpolation=None)
    cp.read(EXAMPLE_FILE, encoding="utf-8")  # defaults
    cp.read(path, encoding="utf-8")

    acc, scr, br, lab = cp["account"], cp["screen"], cp["browser"], cp["labels"]
    s = Settings(
        start_url=acc.get("start_url", Settings.start_url).strip(),
        translation_url=acc.get("translation_url", "").strip(),
        email=(os.environ.get("LOGHON_USERNAME") or os.environ.get("LOGHON_EMAIL")
               or acc.get("username", "") or acc.get("email", "")).strip(),
        password=os.environ.get("LOGHON_PASSWORD", acc.get("password", "")),
        title=scr.get("title", Settings.title),
        subtitle=scr.get("subtitle", Settings.subtitle),
        fullscreen=scr.getboolean("fullscreen", True),
        show_cached_qr=scr.getboolean("show_last_qr_at_startup", True),
        browser_mode=br.get("mode", "offscreen").strip().lower(),
        chrome_path=br.get("chrome_path", "").strip(),
        chrome_channel=br.get("chrome_channel", "").strip(),
        extra_args=br.get("extra_args", "").split(),
        step_timeout_seconds=br.getfloat("step_timeout_seconds", 30),
        qr_refresh_seconds=br.getfloat("qr_refresh_seconds", 10),
        login_buttons=_list(lab.get("login_buttons", "")),
        login_submit_buttons=_list(lab.get("login_submit_buttons", "")),
        pre_start_buttons=_list(lab.get("pre_start_buttons", "")),
        audio_labels=_list(lab.get("audio_labels", "")),
        start_buttons=_list(lab.get("start_buttons", "")),
        after_start_buttons=_list(lab.get("after_start_buttons", "")),
        show_qr_buttons=_list(lab.get("show_qr_buttons", "")),
        qr_selectors=_list(lab.get("qr_selectors", "")),
        stop_buttons=_list(lab.get("stop_buttons", "")),
        confirm_stop_buttons=_list(lab.get("confirm_stop_buttons", "")),
    )
    for d in (s.profile_dir, s.log_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s
