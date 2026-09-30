"""Headlines from RSS/Atom feeds, cached in memory. Standard library only.

Feeds are only fetched while someone is looking at the news: a request for the
headlines starts a background refresh when the cache is stale, and returns what
is cached right away. With the page closed, nothing is fetched.
"""
import html
import re
import ssl
import subprocess
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

MAX_FEED_BYTES = 3 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 (Macintosh) JellyfinRemote/0.1 (+RSS reader)"
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def clean_text(value):
    """Titles sometimes contain HTML tags or entities; show plain text."""
    if not value:
        return ""
    value = html.unescape(TAG_RE.sub("", value))
    return SPACE_RE.sub(" ", value).strip()


def parse_date(value):
    if not value:
        return None
    value = value.strip()
    try:  # RSS: "Wed, 27 May 2026 15:33:31 EDT"
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        parsed = None
    if parsed is None:  # Atom: "2026-09-24T06:47:10Z" (Python 3.9 can't read "Z" or 7 digits)
        iso = value.replace("Z", "+00:00")
        iso = re.sub(r"\.(\d{6})\d+", r".\1", iso)
        try:
            parsed = datetime.fromisoformat(iso)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def local_name(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def child_text(element, name):
    # itertext() also picks up text inside stray tags like <b>, which some feeds put in titles.
    for child in element:
        if local_name(child.tag) == name:
            return "".join(child.itertext())
    return ""


def parse_feed(data):
    """Returns (feed_title, [{"title", "link", "published"}]) for RSS 2.0, RSS 1.0 or Atom."""
    root = ET.fromstring(data)
    kind = local_name(root.tag)
    items = []

    if kind == "feed":  # Atom
        feed_title = clean_text(child_text(root, "title"))
        for entry in root:
            if local_name(entry.tag) != "entry":
                continue
            link = ""
            for child in entry:
                if local_name(child.tag) == "link" and child.get("rel", "alternate") == "alternate":
                    link = child.get("href", "")
                    break
            items.append({
                "title": clean_text(child_text(entry, "title")),
                "link": link,
                "published": parse_date(child_text(entry, "published") or child_text(entry, "updated")),
            })
        return feed_title, items

    # RSS 2.0 (<rss><channel><item>) and RSS 1.0 (<rdf:RDF><item>)
    channel = next((c for c in root if local_name(c.tag) == "channel"), root)
    feed_title = clean_text(child_text(channel, "title"))
    for element in root.iter():
        if local_name(element.tag) != "item":
            continue
        items.append({
            "title": clean_text(child_text(element, "title")),
            "link": (child_text(element, "link") or "").strip(),
            "published": parse_date(child_text(element, "pubDate") or child_text(element, "date")),
        })
    return feed_title, items


def short_source(feed_title, url):
    """A short label for the source: 'CBC | British Columbia News' -> 'CBC'."""
    title = re.split(r"\s[|\-–—:»]\s", feed_title or "")[0].strip()
    if title and len(title) <= 24:
        return title
    host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    return host


MACOS_ROOT_KEYCHAIN = "/System/Library/Keychains/SystemRootCertificates.keychain"
_ssl_context = None
_ssl_lock = threading.Lock()


def macos_root_certificates():
    """macOS's built-in trusted root certificates, as PEM text ('' if unavailable)."""
    try:
        result = subprocess.run(
            ["/usr/bin/security", "find-certificate", "-a", "-p", MACOS_ROOT_KEYCHAIN],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def ssl_context():
    """Python from python.org ships without trusted certificates, so every HTTPS
    request fails. When Python has none of its own, borrow macOS's instead."""
    global _ssl_context
    with _ssl_lock:
        if _ssl_context is None:
            ctx = ssl.create_default_context()
            if not ctx.cert_store_stats().get("x509_ca"):
                try:
                    import certifi  # use it if it happens to be installed
                    ctx.load_verify_locations(cafile=certifi.where())
                except Exception:
                    pem = macos_root_certificates()
                    if pem.strip():
                        try:
                            ctx.load_verify_locations(cadata=pem)
                        except ssl.SSLError:
                            pass
            _ssl_context = ctx
        return _ssl_context


def fetch_feed(url, timeout=8):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    })
    with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
        data = resp.read(MAX_FEED_BYTES + 1)
    if len(data) > MAX_FEED_BYTES:
        raise ValueError("feed is too large")
    feed_title, items = parse_feed(data)
    source = short_source(feed_title, url)
    for item in items:
        item["source"] = source
    return items


def merge_items(item_lists, per_category, now=None):
    """Newest first, one copy of each headline, only the last few days."""
    now = now or time.time()
    seen = set()
    merged = []
    for items in item_lists:
        merged.extend(i for i in items if i.get("title"))
    merged.sort(key=lambda i: i.get("published") or 0, reverse=True)
    result = []
    for item in merged:
        key = item["title"].lower()
        if key in seen:
            continue
        if item.get("published") and now - item["published"] > 4 * 86400:
            continue
        seen.add(key)
        result.append(item)
        if len(result) >= per_category:
            break
    return result


class NewsCache:
    def __init__(self, categories, refresh_minutes=15, per_category=6, fetch=fetch_feed):
        self.categories = categories
        self.refresh_seconds = max(60, int(refresh_minutes * 60))
        self.per_category = per_category
        self.fetch = fetch
        self._lock = threading.Lock()
        self._data = {"categories": [], "updated": None}
        self._last_attempt = 0
        self._refreshing = False
        self.errors = {}

    def get(self):
        """Current headlines; kicks off a refresh in the background if they're stale."""
        with self._lock:
            stale = time.time() - self._last_attempt > self.refresh_seconds
            if stale and not self._refreshing and self.categories:
                self._refreshing = True
                self._last_attempt = time.time()
                threading.Thread(target=self._refresh, daemon=True).start()
            data = dict(self._data)
            data["loading"] = self._refreshing and not self._data["categories"]
            return data

    def refresh_now(self):
        """Fetch everything and wait (used by --check-feeds)."""
        self._refresh()
        return self._data

    def _refresh(self):
        urls = [url for cat in self.categories for url in cat.get("feeds", [])]
        results, errors = {}, {}

        def one(url):
            try:
                results[url] = self.fetch(url)
            except Exception as e:  # one bad feed shouldn't take down the rest
                errors[url] = f"{type(e).__name__}: {e}"

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(one, urls))

        categories = []
        for cat in self.categories:
            items = merge_items([results.get(u, []) for u in cat.get("feeds", [])],
                                self.per_category)
            if items:
                categories.append({"name": cat.get("name", "News"), "items": [
                    {"title": i["title"], "source": i["source"], "published": i.get("published")}
                    for i in items
                ]})

        with self._lock:
            # Keep the previous headlines if every feed failed (e.g. the internet is down).
            if categories:
                self._data = {"categories": categories, "updated": time.time()}
            self.errors = errors
            self._refreshing = False
