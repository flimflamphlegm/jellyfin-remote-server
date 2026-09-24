import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jellyfin_remote.jellyfin_client import (  # noqa: E402
    TICKS_PER_SECOND,
    parse_jellyfin_date,
    pick_session,
    session_to_state,
)

ITEM_ID = "a" * 32
ALBUM_ID = "b" * 32


def session(sid="s1", paused=False, device="MacBook Pro", client="Jellyfin Web",
            last="2026-09-23T18:00:00.0000000Z", item_type="Audio", user="me", **extra):
    s = {
        "Id": sid,
        "DeviceName": device,
        "Client": client,
        "UserName": user,
        "SupportsRemoteControl": True,
        "LastActivityDate": last,
        "PlayState": {"PositionTicks": 30 * TICKS_PER_SECOND, "IsPaused": paused},
        "NowPlayingItem": {
            "Id": ITEM_ID,
            "Type": item_type,
            "Name": "Song",
            "Artists": ["A", "B"],
            "Album": "Record",
            "AlbumId": ALBUM_ID,
            "AlbumPrimaryImageTag": "albumtag",
            "RunTimeTicks": 200 * TICKS_PER_SECOND,
        },
    }
    s.update(extra)
    return s


class PickSessionTests(unittest.TestCase):
    def test_ignores_idle_and_video(self):
        idle = {"Id": "idle", "PlayState": {}}
        video = session("v", item_type="Episode")
        self.assertIsNone(pick_session([idle, video]))

    def test_prefers_playing_over_paused(self):
        paused = session("p", paused=True, last="2026-09-23T19:00:00Z")
        playing = session("q", paused=False, last="2026-09-23T18:00:00Z")
        self.assertEqual(pick_session([paused, playing])["Id"], "q")

    def test_device_filter(self):
        mac = session("mac", device="MacBook Pro")
        phone = session("phone", device="iPhone")
        self.assertEqual(pick_session([mac, phone], "iphone")["Id"], "phone")
        self.assertIsNone(pick_session([mac], "ipad"))

    def test_user_filter_ignores_other_peoples_music(self):
        mine = session("mine", paused=True, user="Flim")
        theirs = session("theirs", paused=False, user="Roommate")
        # Without a filter the other person's (playing) session wins...
        self.assertEqual(pick_session([mine, theirs])["Id"], "theirs")
        # ...with one, it sticks to yours.
        self.assertEqual(pick_session([mine, theirs], user_filter="flim")["Id"], "mine")


class StateTests(unittest.TestCase):
    def test_basic_fields_and_album_art_fallback(self):
        state = session_to_state(session(paused=True))
        self.assertTrue(state["playing"])
        self.assertEqual(state["artist"], "A, B")
        self.assertEqual(state["position"], 30)
        self.assertEqual(state["duration"], 200)
        self.assertEqual(state["art"], f"/art/{ALBUM_ID}?tag=albumtag")

    def test_track_art_wins(self):
        s = session()
        s["NowPlayingItem"]["ImageTags"] = {"Primary": "tracktag"}
        self.assertEqual(session_to_state(s)["art"], f"/art/{ITEM_ID}?tag=tracktag")

    def test_position_catches_up_since_last_checkin(self):
        now = datetime(2026, 9, 23, 18, 0, 10, tzinfo=timezone.utc)
        s = session(LastPlaybackCheckIn="2026-09-23T18:00:04.1234567Z")
        self.assertAlmostEqual(session_to_state(s, now=now)["position"], 35.88, places=1)

    def test_paused_position_does_not_move(self):
        now = datetime(2026, 9, 23, 18, 0, 10, tzinfo=timezone.utc)
        s = session(paused=True, LastPlaybackCheckIn="2026-09-23T18:00:00Z")
        self.assertEqual(session_to_state(s, now=now)["position"], 30)

    def test_nothing_playing(self):
        self.assertEqual(session_to_state(None), {"playing": False})

    def test_shuffle_and_repeat_state(self):
        s = session()
        state = session_to_state(s)
        self.assertFalse(state["shuffle"])
        self.assertEqual(state["repeat"], "RepeatNone")
        s["PlayState"].update(PlaybackOrder="Shuffle", RepeatMode="RepeatOne")
        state = session_to_state(s)
        self.assertTrue(state["shuffle"])
        self.assertEqual(state["repeat"], "RepeatOne")
        # Older servers report ShuffleMode instead of PlaybackOrder.
        s["PlayState"] = {"ShuffleMode": "Shuffle"}
        self.assertTrue(session_to_state(s)["shuffle"])

    def test_unsupported_toggles_are_disabled(self):
        s = session(Capabilities={"SupportedCommands": ["SetRepeatMode"]})
        state = session_to_state(s)
        self.assertFalse(state["can_shuffle"])
        self.assertTrue(state["can_repeat"])


class HelperTests(unittest.TestCase):
    def test_parse_seven_digit_fraction(self):
        parsed = parse_jellyfin_date("2026-09-23T18:04:11.1234567Z")
        self.assertEqual(parsed.microsecond, 123456)
        self.assertEqual(parsed.utcoffset(), timedelta(0))


if __name__ == "__main__":
    unittest.main()
