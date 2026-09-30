import json
import re
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from .jellyfin_client import JellyfinError

CONTROL_RE = re.compile(r"^/api/control/([a-z]+)$")
ART_RE = re.compile(r"^/art/([0-9a-fA-F-]+)$")


def make_handler(client, page_html, news=None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "JellyfinRemote/0.1"
        timeout = 15  # drop connections that go silent, so stuck threads can't pile up

        def _send(self, status, body, content_type, extra_headers=None):
            try:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                for name, value in (extra_headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # The phone gave up on this request (5s timeout, Wi-Fi drop). Nothing to do.
                pass

        def _json(self, status, payload):
            self._send(status, json.dumps(payload).encode(), "application/json",
                       {"Cache-Control": "no-store"})

        def do_GET(self):
            url = urlparse(self.path)

            if url.path in ("/", "/index.html"):
                self._send(200, page_html.encode(), "text/html; charset=utf-8",
                           {"Cache-Control": "no-cache"})
                return

            if url.path == "/api/state":
                try:
                    self._json(200, client.now_playing())
                except JellyfinError as e:
                    self._json(200, {"playing": False, "error": str(e)})
                return

            if url.path == "/api/news":
                if news is None:
                    self._json(200, {"categories": [], "updated": None, "loading": False})
                else:
                    self._json(200, news.get())
                return

            art = ART_RE.match(url.path)
            if art:
                tag = parse_qs(url.query).get("tag", [""])[0]
                try:
                    body, content_type = client.image(art.group(1), tag)
                except JellyfinError as e:
                    self._send(e.status, b"", "text/plain")
                    return
                # The tag changes when the image changes, so this can be cached hard.
                self._send(200, body, content_type,
                           {"Cache-Control": "public, max-age=604800, immutable"})
                return

            self._send(404, b"Not found", "text/plain")

        def do_POST(self):
            match = CONTROL_RE.match(urlparse(self.path).path)
            if not match:
                self._send(404, b"Not found", "text/plain")
                return
            try:
                client.control(match.group(1))
            except JellyfinError as e:
                self._json(e.status, {"ok": False, "error": str(e)})
                return
            self._json(200, {"ok": True})

        def log_message(self, fmt, *args):
            # Polling every second would flood the log; only record problems and controls.
            line = fmt % args
            quiet = "/api/state" in line or "/api/news" in line or ("/art/" in line and '" 200 ' in line)
            if quiet:
                return
            sys.stderr.write(f"{self.log_date_time_string()} {line}\n")

    return Handler
