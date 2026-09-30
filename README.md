# Jellyfin Remote Server

A small now-playing dashboard and remote for Jellyfin music. Run it on the machine
that hosts Jellyfin, then open it on another device (like an old phone) to see
what's playing and control it.

It reads playback state from Jellyfin's `/Sessions` API, so it follows whatever
device is playing music, and nothing extra has to run on that device. Python 3.9+,
standard library only.

## Look and compatibility

The page uses the same styling as pear-desktop-remote-server's minimal mode. It's
written for old Android browsers: plain ES5 JavaScript with XMLHttpRequest, no CSS
variables or flex `gap`, and fallbacks before any `dvh`, `env()` or `calc()` value.

## News

**Music** and **News** buttons at the top of the card switch views, and the phone
remembers which one you left open. News shows headlines in large text, one
category at a time: as many as fit on the screen completely (up to four), so
nothing is cut off and nothing scrolls. It moves on every 10 seconds, and tapping
the headlines skips ahead.

The Mac mini fetches the feeds only while the News tab is open and caches them for
15 minutes, so the phone only ever receives a short list of titles. Out of the
box it shows Tech (The Verge, Ars Technica), World (BBC, CBC), Finance (CNBC),
Vancouver (CBC British Columbia, The Province), Japan (The Japan Times, Japan
Today) and Hong Kong (Hong Kong Free Press, The Guardian).

To change the feeds, add a `news` block to `config.json` (see
`config.example.json`). Any key you leave out keeps its default, and
`"categories": []` turns news off. Check that every feed works with:

```
python3 server.py --check-feeds
```

## Controls

Shuffle, previous, play/pause, next, repeat. Repeat cycles off, this song, all.

Now-playing info works with any Jellyfin client. The controls need a client that
accepts remote commands (the Jellyfin web player does). If yours doesn't, the page
says so and disables the buttons. Shuffle and repeat are also disabled if the
player doesn't advertise support for them. If the player doesn't accept remote
commands at all, the buttons are hidden. Some players (cliamp, for example) don't
report position or pause to Jellyfin either; for those, list them in
`no_progress_clients` and the timer is hidden instead of guessing.

## Install at login

```
chmod +x install.sh
./install.sh
```

The first run asks for a Jellyfin API key (create one under Dashboard > API Keys),
writes `config.json`, checks the connection, and creates a LaunchAgent at
`~/Library/LaunchAgents/com.user.jellyfinremote.plist`. Logs go to
`jellyfinremote.log` and `jellyfinremote.err` in the project directory.

Run `./install.sh` again after changing `config.json` to restart it.
`./uninstall.sh` removes the LaunchAgent.

## Run manually

```
cp config.example.json config.json   # then add your API key
python3 server.py --check            # test the connection
python3 server.py
```

Open the printed URL on a device on the same network.

## Configuration

`config.json` (environment variables override it):

| Key | Default | Env var | What it does |
|---|---|---|---|
| `jellyfin_url` | `http://localhost:8096` | `JELLYFIN_URL` | Where Jellyfin is |
| `api_key` | | `JELLYFIN_API_KEY` | Jellyfin API key |
| `port` | `8097` | `JELLYFIN_REMOTE_PORT` | Port the dashboard listens on |
| `device_filter` | empty | `JELLYFIN_DEVICE_FILTER` | Only follow sessions whose device name or client contains this text |
| `user_filter` | empty | `JELLYFIN_USER_FILTER` | Only follow sessions for this Jellyfin user name |
| `no_progress_clients` | `["cliamp"]` | | Players that don't report position or pause; the timer is hidden for them |
| `news` | 6 categories | | `categories` (name + feed URLs), `refresh_minutes`, `per_category`, `rotate_seconds` |

## Which session it shows

The API key can see every session on the server. The dashboard picks sessions that
are on a music track, prefers playing over paused, then takes the most recently
active one. If other people use your Jellyfin, set `user_filter` to your user name
so it never switches to their music. Set `device_filter` to pin it to one device.

`python3 server.py --check` prints which session it would show right now.

## Project structure

```
.
├── install.sh
├── uninstall.sh
├── server.py
├── config.example.json
├── src/jellyfin_remote/
│   ├── config.py
│   ├── handler.py
│   ├── index.html
│   ├── jellyfin_client.py
│   ├── main.py
│   └── net_utils.py
└── tests/
```

## Tests

```
python3 -m unittest discover -s tests -v
```

The server tests run against a fake Jellyfin on localhost, so they don't need a
real server.

## Troubleshooting

- **"Jellyfin rejected the API key"**: the key in `config.json` is wrong or was revoked.
- **Page loads on the Mac mini but not the phone**: macOS may be blocking incoming
  connections for Python. Allow it under System Settings > Network > Firewall.
- **Progress bar jumps a little every few seconds**: Jellyfin only knows the position
  as of the player's last report. The page fills in between polls, and small
  corrections are normal.
