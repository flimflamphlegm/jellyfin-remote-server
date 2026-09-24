"""End-to-end: the dashboard server talking to a fake Jellyfin on localhost."""
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jellyfin_remote.handler import make_handler  # noqa: E402
from jellyfin_remote.jellyfin_client import JellyfinClient  # noqa: E402
from jellyfin_remote.main import render_page  # noqa: E402
from test_jellyfin_client import ALBUM_ID, session  # noqa: E402

API_KEY = "testkey"


class FakeJellyfin(BaseHTTPRequestHandler):
    calls = []
    sessions = []

    def _ok_auth(self):
        if self.headers.get("Authorization") != f'MediaBrowser Token="{API_KEY}"':
            self.send_response(401)
            self.end_headers()
            return False
        return True

    def do_GET(self):
        if not self._ok_auth():
            return
        if self.path.startswith("/Sessions"):
            body = json.dumps(self.sessions).encode()
            ctype = "application/json"
        elif self.path.startswith(f"/Items/{ALBUM_ID}/Images/Primary"):
            body, ctype = b"\x89PNGfake", "image/png"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self._ok_auth():
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if body:
            assert self.headers.get("Content-Type") == "application/json"
            FakeJellyfin.calls.append((self.path, json.loads(body)))
        else:
            FakeJellyfin.calls.append(self.path)
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


def start(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jf = start(FakeJellyfin)
        jf_url = f"http://127.0.0.1:{cls.jf.server_port}"
        cls.client = JellyfinClient(jf_url, API_KEY)
        handler = make_handler(cls.client, render_page())
        handler.log_message = lambda *a: None
        cls.app = start(handler)
        cls.base = f"http://127.0.0.1:{cls.app.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.app.shutdown()
        cls.jf.shutdown()

    def setUp(self):
        FakeJellyfin.calls = []
        FakeJellyfin.sessions = [session("abc", paused=True)]

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.status, r.read(), r.headers

    def post(self, path):
        req = urllib.request.Request(self.base + path, data=b"", method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_page_has_the_five_buttons(self):
        status, body, _ = self.get("/")
        self.assertEqual(status, 200)
        for action in [b"shuffle", b"prev", b"playpause", b"next", b"repeat"]:
            self.assertIn(b'data-action="' + action + b'"', body)

    def test_state(self):
        _, body, _ = self.get("/api/state")
        state = json.loads(body)
        self.assertEqual(state["title"], "Song")
        self.assertTrue(state["paused"])

    def test_art_proxy(self):
        status, body, headers = self.get(f"/art/{ALBUM_ID}?tag=albumtag")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"\x89PNGfake")
        self.assertIn("immutable", headers["Cache-Control"])

    def test_transport_controls(self):
        for action in ["prev", "playpause", "next"]:
            self.assertEqual(self.post(f"/api/control/{action}"), (200, {"ok": True}))
        self.assertEqual(FakeJellyfin.calls, [
            "/Sessions/abc/Playing/PreviousTrack",
            "/Sessions/abc/Playing/PlayPause",
            "/Sessions/abc/Playing/NextTrack",
        ])

    def test_shuffle_toggles(self):
        self.post("/api/control/shuffle")
        FakeJellyfin.sessions[0]["PlayState"]["PlaybackOrder"] = "Shuffle"
        self.post("/api/control/shuffle")
        cmd = "/Sessions/abc/Command"
        self.assertEqual(FakeJellyfin.calls, [
            (cmd, {"Name": "SetShuffleQueue", "Arguments": {"ShuffleMode": "Shuffle"}}),
            (cmd, {"Name": "SetShuffleQueue", "Arguments": {"ShuffleMode": "Sorted"}}),
        ])

    def test_repeat_cycles_off_once_all(self):
        sent = []
        current = "RepeatNone"
        for _ in range(3):
            FakeJellyfin.sessions[0]["PlayState"]["RepeatMode"] = current
            FakeJellyfin.calls = []
            self.assertEqual(self.post("/api/control/repeat"), (200, {"ok": True}))
            path, body = FakeJellyfin.calls[0]
            self.assertEqual(path, "/Sessions/abc/Command")
            self.assertEqual(body["Name"], "SetRepeatMode")
            current = body["Arguments"]["RepeatMode"]
            sent.append(current)
        self.assertEqual(sent, ["RepeatOne", "RepeatAll", "RepeatNone"])

    def test_unsupported_shuffle_is_refused(self):
        FakeJellyfin.sessions = [session("abc", Capabilities={"SupportedCommands": []})]
        status, body = self.post("/api/control/shuffle")
        self.assertEqual(status, 409)
        self.assertEqual(FakeJellyfin.calls, [])

    def test_nothing_playing(self):
        FakeJellyfin.sessions = []
        status, body = self.post("/api/control/next")
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(self.get("/api/state")[1]), {"playing": False})

    def test_player_without_remote_control(self):
        FakeJellyfin.sessions = [session("abc", SupportsRemoteControl=False)]
        status, body = self.post("/api/control/playpause")
        self.assertEqual(status, 409)
        self.assertIn("doesn't accept remote commands", body["error"])
        self.assertEqual(FakeJellyfin.calls, [])

    def test_bad_api_key_is_reported(self):
        bad = JellyfinClient(self.client.base_url, "wrong")
        with self.assertRaisesRegex(Exception, "rejected the API key"):
            bad.sessions()

    def test_unknown_action(self):
        self.assertEqual(self.post("/api/control/forward")[0], 404)


if __name__ == "__main__":
    unittest.main()
