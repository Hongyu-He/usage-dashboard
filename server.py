"""Single-user loopback web server + background, cached ccusage collection."""
from __future__ import annotations

import datetime as dt
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import secrets
import signal
import threading
import time
from urllib.parse import urlsplit

from collector import ROOT, atomic_json, collect, config, utcnow


class State:
    def __init__(self, cfg, collector=None):
        self.cfg = cfg
        self.guard = threading.Lock()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.collector = collector or (lambda cfg: collect(cfg, cancel_event=self.stop))
        self.refreshing = False
        self.requested = False
        self.last_requested = 0.0
        self.error = None
        self.next_at = time.time()
        self.csrf = secrets.token_urlsafe(24)
        self.started_at = utcnow()
        path = ROOT / "data/latest.json"
        try:
            self.data = json.loads(path.read_text()) if path.exists() else None
        except (OSError, ValueError):
            logging.exception("Cached snapshot unreadable; requesting a fresh scan")
            self.data = None
        if self.data:
            age = time.time() - dt.datetime.fromisoformat(self.data["collectedAt"]).timestamp()
            self.next_at = time.time() + max(0, cfg["refresh_seconds"] - age)

    def request(self):
        with self.guard:
            if self.refreshing or self.requested or time.monotonic() - self.last_requested < 10:
                return False
            self.requested = True
            self.last_requested = time.monotonic()
        self.wake.set()
        return True

    def public_status(self):
        with self.guard:
            return {"refreshing": self.refreshing or self.requested, "error": self.error,
                    "collectedAt": self.data["collectedAt"] if self.data else None,
                    "nextRefreshAt": dt.datetime.fromtimestamp(self.next_at, dt.timezone.utc).isoformat(),
                    "refreshSeconds": self.cfg["refresh_seconds"], "startedAt": self.started_at,
                    "csrfToken": self.csrf, "timezone": self.cfg["timezone"]}

    def loop(self):
        while not self.stop.is_set():
            wait = max(0, self.next_at - time.time())
            self.wake.wait(wait)
            if self.stop.is_set():
                return
            with self.guard:
                self.refreshing, self.requested = True, False
                self.wake.clear()
            try:
                data = self.collector(self.cfg)
                with self.guard:
                    self.data, self.error = data, None
                logging.info("collected rows=%s timings=%s", len(data["rows"]), [r["seconds"] for r in data["timings"]])
            except Exception as exc:
                with self.guard:
                    self.error = str(exc)
                logging.exception("collection failed; preserving last good snapshot")
            finally:
                with self.guard:
                    self.refreshing = False
                    self.next_at = time.time() + self.cfg["refresh_seconds"]


def handler_for(state):
    class Handler(BaseHTTPRequestHandler):
        def allowed(self):
            host = self.headers.get("Host", "")
            try:
                hostname = urlsplit("http://" + host).hostname
            except ValueError:
                return False
            return hostname in ("127.0.0.1", "localhost", "::1")

        def send(self, status, content, mime="application/json; charset=utf-8"):
            body = json.dumps(content, ensure_ascii=False).encode() if isinstance(content, dict) else content
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.allowed():
                return self.send(403, {"error": "loopback Host required"})
            path = urlsplit(self.path).path
            if path == "/healthz":
                return self.send(200, {"service": "usage-dashboard", "pid": os.getpid(), "root": str(ROOT)})
            if path == "/api/status":
                return self.send(200, state.public_status())
            if path == "/api/usage":
                with state.guard:
                    data = state.data
                return self.send(200 if data else 503, data or {"error": "First collection in progress"})
            files = {"/": ("index.html", "text/html; charset=utf-8"),
                     "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                     "/style.css": ("style.css", "text/css; charset=utf-8"),
                     "/favicon.ico": ("favicon.ico", "image/vnd.microsoft.icon"),
                     "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
                     "/app-icon.png": ("app-icon.png", "image/png"),
                     "/app-icon.icns": ("app-icon.icns", "image/icns")}
            if path not in files:
                return self.send(404, {"error": "not found"})
            file, mime = files[path]
            self.send(200, (ROOT / "dist" / file).read_bytes(), mime)

        def do_POST(self):
            origin = self.headers.get("Origin")
            expected_origin = "http://" + self.headers.get("Host", "")
            if not self.allowed() or (origin and origin != expected_origin) or not secrets.compare_digest(self.headers.get("X-Refresh-Token", ""), state.csrf):
                return self.send(403, {"error": "invalid refresh request"})
            if urlsplit(self.path).path != "/api/refresh":
                return self.send(404, {"error": "not found"})
            if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Length", "0") != "0":
                return self.send(400, {"error": "refresh takes no request body"})
            accepted = state.request()
            self.send(202 if accepted else 200, {"accepted": accepted, "refreshing": True})

        def log_message(self, fmt, *args):
            if not self.path.startswith(("/api/status", "/healthz")):
                logging.info("HTTP %s", fmt % args)
    return Handler


def main():
    os.umask(0o077)
    cfg = config()
    run = ROOT / "run"
    run.mkdir(exist_ok=True)
    lock = (run / "server.lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("usage-dashboard already running")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state = State(cfg)
    server = ThreadingHTTPServer(("127.0.0.1", cfg["port"]), handler_for(state))
    server.daemon_threads = True
    atomic_json(run / "server.json", {"pid": os.getpid(), "port": cfg["port"], "root": str(ROOT), "startedAt": state.started_at})
    def stop(signum, frame):
        state.stop.set()
        state.wake.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    worker = threading.Thread(target=state.loop, daemon=True, name="usage-collector")
    worker.start()
    logging.info("Usage dashboard ready: http://127.0.0.1:%s", cfg["port"])
    try:
        server.serve_forever(poll_interval=0.3)
    finally:
        state.stop.set()
        state.wake.set()
        worker.join(timeout=12)
        server.server_close()
        (run / "server.json").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
