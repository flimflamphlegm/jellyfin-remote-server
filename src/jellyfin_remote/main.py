import argparse
import errno
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

from .config import CONFIG_PATH, load_config
from .handler import make_handler
from .jellyfin_client import JellyfinClient, JellyfinError
from .net_utils import lan_ip
from .news import NewsCache


def render_page(rotate_seconds=10):
    html = (Path(__file__).resolve().parent / "index.html").read_text()
    return html.replace("__NEWS_ROTATE_SECONDS__", str(int(rotate_seconds)))


def check_feeds(news, categories):
    data = news.refresh_now()
    counts = {c["name"]: len(c["items"]) for c in data["categories"]}
    for cat in categories:
        print(f"{cat.get('name')}: {counts.get(cat.get('name'), 0)} headlines")
        for url in cat.get("feeds", []):
            error = news.errors.get(url)
            print(f"  {'FAILED' if error else 'ok    '} {url}" + (f"\n         {error}" if error else ""))
    if any("CERTIFICATE_VERIFY_FAILED" in e for e in news.errors.values()):
        print("\nSome feeds still failed certificate checks. As a last resort, run "
              "'Install Certificates.command' from /Applications/Python 3.x/ "
              "(if you installed Python from python.org).")


def main():
    parser = argparse.ArgumentParser(description="Now-playing dashboard and remote for Jellyfin.")
    parser.add_argument("--check", action="store_true",
                        help="Test the connection to Jellyfin and exit.")
    parser.add_argument("--check-feeds", action="store_true",
                        help="Fetch every news feed once, report which work, and exit.")
    args = parser.parse_args()

    cfg = load_config()
    news = NewsCache(cfg.news.get("categories", []),
                     refresh_minutes=cfg.news.get("refresh_minutes", 15),
                     per_category=cfg.news.get("per_category", 6))

    if args.check_feeds:
        check_feeds(news, cfg.news.get("categories", []))
        return

    if not cfg.api_key:
        sys.exit(f"No Jellyfin API key. Add api_key to {CONFIG_PATH} or run ./install.sh.")

    client = JellyfinClient(cfg.jellyfin_url, cfg.api_key,
                            device_filter=cfg.device_filter, user_filter=cfg.user_filter,
                            no_progress_clients=cfg.no_progress_clients)

    if args.check:
        try:
            sessions = client.sessions()
        except JellyfinError as e:
            sys.exit(f"Connection failed: {e}")
        state = client.now_playing()
        print(f"Connected to {cfg.jellyfin_url} ({len(sessions)} active session(s)).")
        if state["playing"]:
            print(f"Now playing: {state['title']} by {state['artist']} "
                  f"on {state['device']} ({state['client']}, user {state['user']})")
            if not state["reports_progress"]:
                print(f"Note: {state['client']} doesn't report position or pause, "
                      "so the timer is hidden for it.")
            if not state["can_control"]:
                print(f"Note: {state['client']} doesn't accept remote commands, "
                      "so the buttons will be disabled for it.")
        return

    try:
        server = ThreadingHTTPServer((cfg.host, cfg.port),
                                     make_handler(client, render_page(cfg.news.get("rotate_seconds", 10)), news))
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            # Usually another copy of this remote (desktop + SSH session). Exit cleanly
            # so launchd doesn't keep restarting this one.
            print(f"Port {cfg.port} is already in use; another copy is probably running. "
                  "Exiting.", flush=True)
            sys.exit(0)
        raise
    print(f"Jellyfin remote: http://{lan_ip()}:{cfg.port}", flush=True)
    print(f"Reading from Jellyfin at {cfg.jellyfin_url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
