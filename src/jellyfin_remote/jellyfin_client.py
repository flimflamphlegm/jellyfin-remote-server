"""Talks to the Jellyfin REST API. Standard library only."""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

TICKS_PER_SECOND = 10_000_000
ITEM_ID_RE = re.compile(r"^[0-9a-fA-F-]{32,36}$")

# Dashboard action -> Jellyfin /Sessions/{id}/Playing/{command}
PLAYING_COMMANDS = {
    "prev": "PreviousTrack",
    "playpause": "PlayPause",
    "next": "NextTrack",
}
TOGGLE_ACTIONS = {"shuffle", "repeat"}
ACTIONS = set(PLAYING_COMMANDS) | TOGGLE_ACTIONS

# Repeat button cycles off -> once -> all -> off.
REPEAT_CYCLE = {"RepeatNone": "RepeatOne", "RepeatOne": "RepeatAll", "RepeatAll": "RepeatNone"}


class JellyfinError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


def parse_jellyfin_date(value):
    """Jellyfin sends e.g. 2026-09-23T18:04:11.1234567Z; Python 3.9 wants 6 digits and +00:00."""
    if not value:
        return None
    value = value.replace("Z", "+00:00")
    value = re.sub(r"\.(\d{6})\d+", r".\1", value)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def pick_session(sessions, device_filter="", user_filter=""):
    """The music session to show: playing beats paused, then most recent activity."""
    device_needle = device_filter.lower().strip()
    user_needle = user_filter.lower().strip()
    candidates = []
    for session in sessions or []:
        item = session.get("NowPlayingItem")
        if not item or item.get("Type") != "Audio":
            continue
        where = f"{session.get('DeviceName', '')} {session.get('Client', '')}".lower()
        if device_needle and device_needle not in where:
            continue
        if user_needle and user_needle != session.get("UserName", "").lower():
            continue
        candidates.append(session)
    if not candidates:
        return None
    candidates.sort(
        key=lambda s: (
            not s.get("PlayState", {}).get("IsPaused", False),
            s.get("LastActivityDate", ""),
        ),
        reverse=True,
    )
    return candidates[0]


def art_for(item):
    """(item_id, tag) for the track's own image, else its album's, else None."""
    tag = (item.get("ImageTags") or {}).get("Primary")
    if tag:
        return item["Id"], tag
    if item.get("AlbumId") and item.get("AlbumPrimaryImageTag"):
        return item["AlbumId"], item["AlbumPrimaryImageTag"]
    return None


def is_shuffled(play_state):
    # Newer servers report PlaybackOrder; older ones ShuffleMode. Accept either.
    return (play_state.get("PlaybackOrder") == "Shuffle"
            or play_state.get("ShuffleMode") == "Shuffle")


def supports(session, command):
    """Whether the player advertised this general command. Unknown means assume yes."""
    supported = (session.get("Capabilities") or {}).get("SupportedCommands")
    if supported is None:
        return True
    return command in supported


def session_to_state(session, now=None):
    if session is None:
        return {"playing": False}

    item = session["NowPlayingItem"]
    play_state = session.get("PlayState", {})
    paused = bool(play_state.get("IsPaused"))
    duration = (item.get("RunTimeTicks") or 0) / TICKS_PER_SECOND
    position = (play_state.get("PositionTicks") or 0) / TICKS_PER_SECOND

    # PositionTicks is as of the player's last report; catch it up to "now".
    if not paused:
        checked_in = parse_jellyfin_date(session.get("LastPlaybackCheckIn"))
        if checked_in:
            now = now or datetime.now(timezone.utc)
            elapsed = (now - checked_in).total_seconds()
            if 0 < elapsed < 120:
                position += elapsed
    if duration:
        position = min(position, duration)

    art = art_for(item)
    artists = item.get("Artists") or []
    can_control = bool(session.get("SupportsRemoteControl"))
    return {
        "playing": True,
        "paused": paused,
        "title": item.get("Name", ""),
        "artist": ", ".join(artists) or item.get("AlbumArtist", ""),
        "album": item.get("Album", ""),
        "position": round(position, 2),
        "duration": round(duration, 2),
        "art": f"/art/{art[0]}?tag={urllib.parse.quote(art[1])}" if art else None,
        "device": session.get("DeviceName", ""),
        "client": session.get("Client", ""),
        "user": session.get("UserName", ""),
        "shuffle": is_shuffled(play_state),
        "repeat": play_state.get("RepeatMode") or "RepeatNone",
        "can_control": can_control,
        "can_shuffle": can_control and supports(session, "SetShuffleQueue"),
        "can_repeat": can_control and supports(session, "SetRepeatMode"),
    }


class JellyfinClient:
    def __init__(self, base_url, api_key, device_filter="", user_filter="", timeout=4):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.device_filter = device_filter
        self.user_filter = user_filter
        self.timeout = timeout

    def _request(self, method, path, body=None, raw=False):
        req = urllib.request.Request(self.base_url + path, method=method)
        req.add_header("Authorization", f'MediaBrowser Token="{self.api_key}"')
        if body is not None:
            req.data = json.dumps(body).encode()
            req.add_header("Content-Type", "application/json")
        elif method == "POST":
            req.data = b""
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = resp.read()
                content_type = resp.headers.get("Content-Type", "application/octet-stream")
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise JellyfinError(
                    "Jellyfin rejected the API key. Check api_key in config.json.", 502
                ) from e
            if e.code == 404:
                raise JellyfinError(f"Jellyfin has nothing at {path}", 404) from e
            raise JellyfinError(f"Jellyfin returned HTTP {e.code} for {path}") from e
        except (urllib.error.URLError, OSError) as e:
            raise JellyfinError(
                f"Can't reach Jellyfin at {self.base_url}. Is the server running?"
            ) from e
        if raw:
            return payload, content_type
        return json.loads(payload) if payload else None

    def sessions(self):
        return self._request("GET", "/Sessions?activeWithinSeconds=960") or []

    def current_session(self):
        return pick_session(self.sessions(), self.device_filter, self.user_filter)

    def now_playing(self):
        return session_to_state(self.current_session())

    def control(self, action):
        if action not in ACTIONS:
            raise JellyfinError(f"Unknown control: {action}", 404)
        session = self.current_session()
        if session is None:
            raise JellyfinError("Nothing is playing.", 409)
        player = session.get("Client") or "This player"
        if not session.get("SupportsRemoteControl"):
            raise JellyfinError(f"{player} doesn't accept remote commands.", 409)

        sid = urllib.parse.quote(session["Id"], safe="")
        play_state = session.get("PlayState", {})

        if action in PLAYING_COMMANDS:
            self._request("POST", f"/Sessions/{sid}/Playing/{PLAYING_COMMANDS[action]}")
        elif action == "shuffle":
            if not supports(session, "SetShuffleQueue"):
                raise JellyfinError(f"{player} doesn't support remote shuffle.", 409)
            mode = "Sorted" if is_shuffled(play_state) else "Shuffle"
            self._request("POST", f"/Sessions/{sid}/Command",
                          {"Name": "SetShuffleQueue", "Arguments": {"ShuffleMode": mode}})
        elif action == "repeat":
            if not supports(session, "SetRepeatMode"):
                raise JellyfinError(f"{player} doesn't support remote repeat.", 409)
            mode = REPEAT_CYCLE.get(play_state.get("RepeatMode") or "RepeatNone", "RepeatNone")
            self._request("POST", f"/Sessions/{sid}/Command",
                          {"Name": "SetRepeatMode", "Arguments": {"RepeatMode": mode}})

    def image(self, item_id, tag=""):
        if not ITEM_ID_RE.match(item_id):
            raise JellyfinError("Bad image id", 404)
        query = urllib.parse.urlencode({"maxHeight": 720, "quality": 90, "tag": tag})
        return self._request("GET", f"/Items/{item_id}/Images/Primary?{query}", raw=True)
