"""End-to-end check of LoghonDriver against tests/mock_site (no real Loghon needed).

    python -m tests.test_driver            # uses Playwright's Chromium
    CHROME=/path/to/chrome python -m tests.test_driver
"""
import functools
import http.server
import os
import queue
import tempfile
import threading
import urllib.parse
from pathlib import Path

from loghon_kiosk import config
from loghon_kiosk.driver import LoghonDriver

SITE = Path(__file__).parent / "mock_site"


PAGE_LOG = []  # messages the mock page reports via GET /log?m=...


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/log?m="):
            PAGE_LOG.append(urllib.parse.unquote(self.path[7:]))
            self.send_response(204)
            self.end_headers()
            return
        super().do_GET()


def serve():
    handler = functools.partial(Handler, directory=str(SITE))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def wait_state(events, want, timeout=60):
    seen = []
    while True:
        kind, value = events.get(timeout=timeout)
        seen.append((kind, value if kind != "qr" else f"<{len(value)} bytes>"))
        print("  event:", seen[-1])
        if kind == "state" and value == want:
            return seen
        if kind == "error":
            raise AssertionError(value)


def main():
    srv = serve()
    tmp = Path(tempfile.mkdtemp())
    s = config.load(Path(config.EXAMPLE_FILE))
    s.start_url = f"http://127.0.0.1:{srv.server_address[1]}/index.html"
    s.email, s.password = "church@example.org", "secret"
    s.browser_mode = "headless"
    s.chrome_path = os.environ.get("CHROME", "")
    s.extra_args = ["--use-fake-device-for-media-stream"]  # sandbox has no mic
    s.step_timeout_seconds = 10
    s.qr_refresh_seconds = 2
    s.profile_dir, s.log_dir, s.qr_cache = tmp / "profile", tmp / "logs", tmp / "qr.png"
    s.log_dir.mkdir()

    events = queue.Queue()
    d = LoghonDriver(s, lambda e: events.put(e))
    for round_ in (1, 2):  # round 2 checks the saved login is reused
        print(f"round {round_}: start")
        d.submit("start")
        seen = wait_state(events, "running")
        assert any(k == "qr" for k, _ in seen), "no QR code captured"
        print("  page log:", PAGE_LOG)
        assert PAGE_LOG[-3:] == ["audio=true", "mic ok", "started"], PAGE_LOG
        assert not any("WRONG" in m for m in PAGE_LOG)
        print(f"round {round_}: stop")
        d.submit("stop")
        wait_state(events, "idle")
        assert PAGE_LOG[-1] == "stopped", PAGE_LOG
    d.submit("quit")
    wait_state(events, "quit")
    print("PASS")


if __name__ == "__main__":
    main()
