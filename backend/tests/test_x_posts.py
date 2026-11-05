"""Unit tests for the X (Twitter) watcher source (Task 10)."""
import pytest

from app import config, db, ingest
from app.models import XPost
from app.sources import x_posts


def _post(account, post_id, text, posted_at, tickers=""):
    return XPost(account=account, post_id=post_id, text=text, url="",
                 posted_at=posted_at, tickers=tickers, fetched_at=posted_at)

_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Donald J. Trump / @realDonaldTrump</title>
    <item>
      <title>Huge day for $TSLA and NVDA — economy is BOOMING!</title>
      <description>Huge day for $TSLA and NVDA — economy is BOOMING!</description>
      <link>https://nitter.net/realDonaldTrump/status/1810000000000000001#m</link>
      <guid>https://nitter.net/realDonaldTrump/status/1810000000000000001#m</guid>
      <pubDate>Tue, 08 Jul 2026 14:30:00 GMT</pubDate>
    </item>
    <item>
      <title>Nothing to see here</title>
      <description>Nothing to see here</description>
      <link>https://nitter.net/realDonaldTrump/status/1810000000000000002#m</link>
      <guid>https://nitter.net/realDonaldTrump/status/1810000000000000002#m</guid>
      <pubDate>Tue, 08 Jul 2026 13:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>"""


# ---- extract_tickers ----

def test_extract_tickers_cashtags():
    assert x_posts.extract_tickers("buy $TSLA and $nvda now", set()) == ["TSLA", "NVDA"]


def test_extract_tickers_word_match_of_known():
    out = x_posts.extract_tickers("I think AAPL will run", {"AAPL", "MSFT"})
    assert out == ["AAPL"]


def test_extract_tickers_no_substring_false_positive():
    # "CAT" must not match inside "category"; word boundaries only.
    assert x_posts.extract_tickers("a new category of products", {"CAT"}) == []


def test_extract_tickers_dedup_cashtag_and_known():
    out = x_posts.extract_tickers("$TSLA is up, TSLA to the moon", {"TSLA"})
    assert out == ["TSLA"]


# ---- parse_rss ----

def test_parse_rss_extracts_items():
    posts = x_posts.parse_rss(_RSS, "realDonaldTrump", {"NVDA"})
    assert len(posts) == 2
    p = posts[0]
    assert p.account == "realDonaldTrump"
    assert p.post_id == "1810000000000000001"
    assert "BOOMING" in p.text
    assert p.url.startswith("https://nitter.net/")
    assert p.posted_at.startswith("2026-07-08T14:30:00")
    assert set(p.tickers.split(",")) == {"TSLA", "NVDA"}


def test_parse_rss_second_item_has_no_tickers():
    posts = x_posts.parse_rss(_RSS, "realDonaldTrump", set())
    assert posts[1].tickers == ""


# ---- pubDate normalization (Task 19: keep the column sortable) ----

_RSS_BAD_DATE = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>$AAPL breakout</title>
    <link>https://nitter.net/x/status/1810000000000000009#m</link>
    <guid>https://nitter.net/x/status/1810000000000000009#m</guid>
    <pubDate>not a real date</pubDate>
  </item>
</channel></rss>"""


def test_iso_from_pubdate_drops_unparseable_raw_string():
    # Never leaks the raw RFC-822 / garbage string back into the column.
    assert x_posts._iso_from_pubdate("garbage") == ""
    assert x_posts._iso_from_pubdate("Tue, 08 Jul 2026 14:30:00 GMT").startswith(
        "2026-07-08T14:30:00")


def test_parse_rss_falls_back_to_fetched_at_when_pubdate_unparseable():
    from datetime import datetime

    posts = x_posts.parse_rss(_RSS_BAD_DATE, "x", set())
    assert len(posts) == 1
    p = posts[0]
    assert p.posted_at == p.fetched_at        # strict ISO, not the garbage string
    datetime.fromisoformat(p.posted_at)        # parseable → string-sorts by date


# ---- fetch: mirror fallback ----

class _Resp:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("err", request=None, response=None)


def _client_factory(url_map):
    class _Client:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None):
            return url_map(url)
    return _Client


def test_fetch_first_mirror_fails_second_succeeds(monkeypatch):
    monkeypatch.setattr(config, "X_BEARER", "")
    monkeypatch.setattr(config, "X_MIRRORS", ["https://bad.mirror", "https://good.mirror"])
    monkeypatch.setattr(x_posts.time, "sleep", lambda s: None)

    def url_map(url):
        if url.startswith("https://bad.mirror"):
            return _Resp(500)
        return _Resp(200, _RSS)

    monkeypatch.setattr(x_posts.httpx, "Client", _client_factory(url_map))

    result = x_posts.fetch(["realDonaldTrump"], {"NVDA"})
    assert len(result) == 2
    assert result.warning  # degraded provenance flagged
    assert "STOCKS_X_BEARER" in result.warning


def test_fetch_all_mirrors_fail_raises(monkeypatch):
    monkeypatch.setattr(config, "X_BEARER", "")
    monkeypatch.setattr(config, "X_MIRRORS", ["https://bad1", "https://bad2"])
    monkeypatch.setattr(x_posts.time, "sleep", lambda s: None)
    monkeypatch.setattr(x_posts.httpx, "Client", _client_factory(lambda url: _Resp(500)))

    with pytest.raises(RuntimeError):
        x_posts.fetch(["realDonaldTrump"], set())


def test_fetch_official_path_builds_api_urls(monkeypatch):
    monkeypatch.setattr(config, "X_BEARER", "TOKEN123")
    calls = []

    class _R:
        def __init__(self, data): self._data = data
        def raise_for_status(self): pass
        def json(self): return self._data

    class _Client:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None):
            calls.append(url)
            if "by/username" in url:
                return _R({"data": {"id": "42"}})
            return _R({"data": [{"id": "999", "text": "buy $AAPL", "created_at": "2026-07-08T00:00:00Z"}]})

    monkeypatch.setattr(x_posts.httpx, "Client", _Client)
    result = x_posts.fetch(["realDonaldTrump"], {"AAPL"})
    assert not result.warning
    assert any("users/by/username/realDonaldTrump" in u for u in calls)
    assert any("users/42/tweets" in u for u in calls)
    assert result[0].tickers == "AAPL"
    assert result[0].post_id == "999"


# ---- get_x_posts_for (Task 18: ticker-related posts for analysis) ----

def test_get_x_posts_for_matches_tag_and_text(conn):
    db.upsert_x_posts(conn, [
        _post("a", "1", "love $TSLA today", "2026-07-08T10:00:00+00:00", tickers="TSLA"),
        _post("a", "2", "AAPL looks strong", "2026-07-08T09:00:00+00:00"),  # untagged
        _post("a", "3", "a new CATEGORY launches", "2026-07-08T08:00:00+00:00"),
    ])
    # tagged at ingest
    assert [p.post_id for p in db.get_x_posts_for(conn, "TSLA")] == ["1"]
    # untagged, matched by word-boundary text scan
    assert [p.post_id for p in db.get_x_posts_for(conn, "AAPL")] == ["2"]
    # no substring false positive: CAT must not match "CATEGORY"
    assert db.get_x_posts_for(conn, "CAT") == []


def test_get_x_posts_for_orders_newest_first(conn):
    db.upsert_x_posts(conn, [
        _post("a", "1", "$NVDA early", "2026-07-08T08:00:00+00:00", tickers="NVDA"),
        _post("b", "2", "$NVDA later", "2026-07-08T12:00:00+00:00", tickers="NVDA"),
    ])
    assert [p.post_id for p in db.get_x_posts_for(conn, "NVDA")] == ["2", "1"]


# ---- hourly refresh guarantee (Task: X accounts checked once per hour) ----

def test_x_min_interval_is_one_hour():
    assert config.X_MIN_INTERVAL_SECONDS == 3600


def test_x_posts_registered_with_hourly_interval():
    """The SOURCES registry wires x_posts to the hourly gate + the x_posts store,
    so a stray edit can't silently make it poll every 180s refresh cycle."""
    from app import main as main_module

    sources = main_module.build_sources(main_module.conn)
    assert "x_posts" in sources
    spec = sources["x_posts"]
    assert spec.min_interval == 3600
    assert spec.store is db.upsert_x_posts


def test_x_posts_schedule_is_seeded_hourly():
    """The hourly cadence now lives in the per-source schedule row seeded from
    the registry, so a stray edit can't make it poll every 180s."""
    from app import main as main_module, schedules

    rows = {r.source: r for r in schedules.defaults_from_specs(
        main_module.build_sources(main_module.conn), config.REFRESH_INTERVAL_SECONDS, "UTC")}
    assert rows["x_posts"].interval_seconds == 3600
