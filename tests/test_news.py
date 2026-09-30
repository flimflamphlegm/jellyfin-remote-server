import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jellyfin_remote.config import load_config  # noqa: E402
from jellyfin_remote.news import NewsCache, merge_items, parse_date, parse_feed, short_source  # noqa: E402

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>CBC | British Columbia News</title>
  <item><title><![CDATA[Transit strike vote &amp; what it means]]></title>
    <link>https://example.com/a</link><pubDate>Wed, 27 May 2026 11:10:30 EDT</pubDate></item>
  <item><title>Second  story <b>bold</b></title><link>https://example.com/b</link>
    <pubDate>Wed, 27 May 2026 09:00:00 EDT</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Ars Technica - AI</title>
  <entry><title>Model launch</title><link rel="alternate" href="https://example.com/c"/>
    <updated>2026-09-24T06:47:10.1234567Z</updated></entry>
</feed>"""


class ParseTests(unittest.TestCase):
    def test_rss(self):
        title, items = parse_feed(RSS)
        self.assertEqual(title, "CBC | British Columbia News")
        self.assertEqual(items[0]["title"], "Transit strike vote & what it means")
        self.assertEqual(items[1]["title"], "Second story bold")
        self.assertEqual(items[0]["link"], "https://example.com/a")
        self.assertGreater(items[0]["published"], items[1]["published"])

    def test_atom(self):
        title, items = parse_feed(ATOM)
        self.assertEqual(items[0]["title"], "Model launch")
        self.assertEqual(items[0]["link"], "https://example.com/c")
        self.assertIsNotNone(items[0]["published"])

    def test_dates(self):
        self.assertIsNotNone(parse_date("Wed, 27 May 2026 15:33:31 EDT"))
        self.assertIsNotNone(parse_date("2026-09-24T06:47:10Z"))
        self.assertIsNone(parse_date("not a date"))

    def test_short_source(self):
        self.assertEqual(short_source("CBC | British Columbia News", "https://cbc.ca/x"), "CBC")
        self.assertEqual(short_source("", "https://www.japantimes.co.jp/feed/"), "japantimes.co.jp")


class MergeTests(unittest.TestCase):
    def test_newest_first_deduped_and_limited(self):
        now = time.time()
        a = [{"title": "Same", "published": now - 60, "source": "A"},
             {"title": "Old", "published": now - 3600, "source": "A"}]
        b = [{"title": "same", "published": now - 30, "source": "B"},
             {"title": "Newest", "published": now - 10, "source": "B"},
             {"title": "Ancient", "published": now - 10 * 86400, "source": "B"}]
        merged = merge_items([a, b], per_category=3, now=now)
        self.assertEqual([m["title"] for m in merged], ["Newest", "same", "Old"])


class CacheTests(unittest.TestCase):
    def test_one_bad_feed_does_not_break_the_category(self):
        def fetch(url):
            if "bad" in url:
                raise OSError("down")
            return [{"title": f"story from {url}", "published": time.time(), "source": "S"}]
        cache = NewsCache([{"name": "AI", "feeds": ["good", "bad"]},
                           {"name": "Dead", "feeds": ["bad2"]}], fetch=fetch)
        data = cache.refresh_now()
        self.assertEqual([c["name"] for c in data["categories"]], ["AI"])
        self.assertIn("bad", cache.errors)

    def test_keeps_old_headlines_when_everything_fails(self):
        ok = {"on": True}
        def fetch(url):
            if not ok["on"]:
                raise OSError("offline")
            return [{"title": "kept", "published": time.time(), "source": "S"}]
        cache = NewsCache([{"name": "AI", "feeds": ["u"]}], fetch=fetch)
        cache.refresh_now()
        ok["on"] = False
        data = cache.refresh_now()
        self.assertEqual(data["categories"][0]["items"][0]["title"], "kept")

    def test_get_refreshes_in_background_only_when_stale(self):
        calls = []
        def fetch(url):
            calls.append(url)
            return [{"title": "x", "published": time.time(), "source": "S"}]
        cache = NewsCache([{"name": "AI", "feeds": ["u"]}], fetch=fetch)
        first = cache.get()
        self.assertTrue(first["loading"])
        for _ in range(50):
            if cache.get()["categories"]:
                break
            time.sleep(0.02)
        cache.get(); cache.get()
        self.assertEqual(len(calls), 1)  # cached, not refetched on every request


class SslTests(unittest.TestCase):
    def test_context_builds_and_is_reused(self):
        from jellyfin_remote import news
        news._ssl_context = None
        ctx = news.ssl_context()
        self.assertIs(ctx, news.ssl_context())
        self.assertEqual(ctx.verify_mode, __import__("ssl").CERT_REQUIRED)  # never turns checks off

    def test_falls_back_to_macos_roots_when_python_has_none(self):
        import ssl
        from unittest import mock
        from jellyfin_remote import news
        news._ssl_context = None
        loaded = {}
        real = ssl.create_default_context
        def empty_context():
            ctx = real()
            ctx.cert_store_stats = lambda: {"x509_ca": 0}
            ctx.load_verify_locations = lambda **kw: loaded.update(kw)
            return ctx
        with mock.patch.object(news.ssl, "create_default_context", empty_context), \
             mock.patch.dict("sys.modules", {"certifi": None}), \
             mock.patch.object(news, "macos_root_certificates", lambda: "-----BEGIN CERTIFICATE-----x"):
            news.ssl_context()
        news._ssl_context = None
        self.assertIn("cadata", loaded)


class ConfigTests(unittest.TestCase):
    def test_default_news_and_partial_override(self):
        import json, tempfile, os
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"news": {"refresh_minutes": 30}}, f)
        try:
            cfg = load_config(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(cfg.news["refresh_minutes"], 30)
        self.assertEqual([c["name"] for c in cfg.news["categories"]],
                         ["Tech", "World", "Finance", "Vancouver", "Japan", "Hong Kong"])


if __name__ == "__main__":
    unittest.main()
