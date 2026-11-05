"""The market-wide scanner: universes, the batched quote fetch, scans on the
rule engine, history, alert throttling and the server-sent events stream."""
import asyncio
import json
import threading
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, db, events, ingest, scanner
from app.models import IntradayQuote, OHLCBar, OHLCSeries, ScannerPrefs, WatchItem
from app.sources import intraday, universe as universe_source, yahoo
from tests.conftest import authenticate, buy

LIVE = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)       # Tue 11:00 New York
SATURDAY = datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)
DAY = "2026-10-27"


def _history(n=300, volume=1e6):
    """Closes alternating 99.5 / 100.5 (RSI ~50), ending on 100.5."""
    start = date(2025, 1, 1)
    closes = [100.5 if (n - i) % 2 else 99.5 for i in range(n)]
    return [OHLCBar(date=(start + timedelta(days=i)).isoformat(), open=c, high=c + 1, low=c - 1, close=c,
                    volume=volume) for i, c in enumerate(closes)]


def _store(conn, ticker, bars, interval="daily", fetched_at="2026-10-27T00:00:00+00:00"):
    db.upsert_ohlc(conn, [OHLCSeries(ticker=ticker, interval=interval, fetched_at=fetched_at,
                                     bars_json=json.dumps([b.model_dump() for b in bars]))])


def _quote(ticker, price=100.0, open_=100.0, high=101.0, low=99.0, volume=1e6, state="LIVE"):
    return IntradayQuote(ticker=ticker, day=DAY, price=price, open=open_, high=high, low=low, volume=volume,
                         previous_close=100.0, market_state=state, currency="USD", fetched_at=LIVE.isoformat())


@pytest.fixture
def mine_only(monkeypatch):
    monkeypatch.setattr(config, "SCANNER_UNIVERSES", ["mine"])


# ---------- universes (STC-70) ----------

SP500_HTML = """<table class="wikitable" id="constituents"><tr><th>Symbol</th><th>Security</th><th>Sector</th></tr>
<tr><td><a>MMM</a></td><td>3M</td><td>Industrials</td></tr>
<tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td></tr>
<tr><td>bad ticker!</td><td>x</td><td>y</td></tr></table>"""


def test_constituents_come_from_the_symbol_column_in_yahoo_spelling():
    assert universe_source.parse_constituents(SP500_HTML) == [("MMM", "3M"), ("BRK-B", "Berkshire Hathaway")]


def test_constituents_fall_back_to_the_first_table_with_a_ticker_column_and_add_the_suffix():
    html = ("<table><tr><th>Rank</th></tr><tr><td>1</td></tr></table>"
            "<table><tr><th>Company</th><th>Ticker</th></tr><tr><td>Teva</td><td>TEVA</td></tr>"
            "<tr><td>Nice</td><td>NICE.TA</td></tr></table>")
    assert universe_source.parse_constituents(html, ".TA") == [("TEVA.TA", "Teva"), ("NICE.TA", "Nice")]
    assert universe_source.parse_constituents("<p>moved</p>") == []


def test_a_truncated_constituents_list_raises_instead_of_shrinking_the_universe(monkeypatch):
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=SP500_HTML))
    real = httpx.Client
    monkeypatch.setattr(universe_source.httpx, "Client", lambda **kw: real(transport=transport, **kw))
    with pytest.raises(ValueError, match="expected at least 450"):
        universe_source.fetch(["sp500"])


def test_the_universe_is_the_configured_union_without_fx_and_capped(conn, monkeypatch):
    u = db.create_user(conn, "s@x.co", "h", "t")
    db.add_watch(conn, u.id, WatchItem(ticker="ZZZ", note="", added_at="t"))
    db.add_watch(conn, u.id, WatchItem(ticker="EURUSD=X", note="", added_at="t"))
    buy(conn, u.id, "AAPL", 1, 1)
    db.replace_universe(conn, [universe_source.UniverseMember(universe="ndx100", ticker="QQQX", refreshed_at="t")])
    monkeypatch.setattr(config, "SCANNER_UNIVERSES", ["mine", "ndx100", "nope"])
    tickers, counts = scanner.universe(conn)
    assert tickers == ["AAPL", "ZZZ", "QQQX"] and counts == {"mine": 3, "ndx100": 1, "nope": None}
    monkeypatch.setattr(config, "SCANNER_MAX_TICKERS", 2)
    assert scanner.universe(conn)[0] == ["AAPL", "ZZZ"]


# ---------- the batched quote fetch (STC-71) ----------

def _payload(*rows):
    return {"quoteResponse": {"result": list(rows), "error": None}}


ROW = {"symbol": "AAPL", "currency": "USD", "marketState": "REGULAR", "regularMarketPrice": 101.5,
       "regularMarketOpen": 100.0, "regularMarketDayHigh": 102.0, "regularMarketDayLow": 99.5,
       "regularMarketVolume": 3_000_000, "regularMarketPreviousClose": 99.0,
       "regularMarketTime": int(LIVE.timestamp()), "exchangeTimezoneName": "America/New_York"}


def test_quotes_parse_in_major_units_and_incomplete_rows_are_left_out():
    tase = {**ROW, "symbol": "TEVA.TA", "currency": "ILA", "regularMarketPrice": 6000, "regularMarketOpen": 5900,
            "regularMarketDayHigh": 6050, "regularMarketDayLow": 5880, "exchangeTimezoneName": "Asia/Jerusalem"}
    partial = {k: v for k, v in ROW.items() if k != "regularMarketOpen"} | {"symbol": "MSFT"}
    quotes = intraday.parse_quotes(_payload(ROW, tase, partial), "now")
    assert [(q.ticker, q.price, q.currency, q.market_state, q.day) for q in quotes] == [
        ("AAPL", 101.5, "USD", "LIVE", DAY), ("TEVA.TA", 60.0, "ILS", "LIVE", DAY)]


def _mock_yahoo(monkeypatch, handler):
    real = httpx.Client
    monkeypatch.setattr(yahoo, "client", lambda timeout=30.0: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(yahoo, "get_crumb", lambda client: "c")
    monkeypatch.setattr(intraday, "_PAUSE_SECONDS", 0)


def test_quotes_are_fetched_in_batches(monkeypatch):
    seen = []

    def handler(req):
        syms = req.url.params["symbols"].split(",")
        seen.append(syms)
        return httpx.Response(200, json=_payload(*({**ROW, "symbol": s} for s in syms)))

    _mock_yahoo(monkeypatch, handler)
    quotes, failed = intraday.fetch(["A", "B", "C"], batch=2)
    assert seen == [["A", "B"], ["C"]] and len(quotes) == 3 and failed == 0


def test_a_429_defers_with_yahoos_retry_after(monkeypatch):
    _mock_yahoo(monkeypatch, lambda req: httpx.Response(429, headers={"Retry-After": "900"}))
    with pytest.raises(ingest.SourceDeferred) as e:
        intraday.fetch(["A", "B"], batch=1)
    assert e.value.retry_after_seconds == 900 and "429" in e.value.reason


def test_every_batch_failing_is_an_error_and_some_failing_is_counted(monkeypatch):
    _mock_yahoo(monkeypatch, lambda req: httpx.Response(500))
    with pytest.raises(RuntimeError, match="every quote batch failed"):
        intraday.fetch(["A"], batch=1)
    _mock_yahoo(monkeypatch, lambda req: httpx.Response(500) if req.url.params["symbols"] == "B"
                else httpx.Response(200, json=_payload(ROW)))
    assert intraday.fetch(["A", "B"], batch=1)[1] == 1


# ---------- scans on the rule engine (STC-72) ----------

def _keys(hits):
    return sorted(h.scan for h in hits)


def test_a_gap_up_on_unusual_volume_matches_both_scans():
    hits = scanner.evaluate(_history(), _quote("X", price=104, open_=104, high=104.5, low=103.8, volume=3e6), "t")
    assert {"breakout_20d", "gap_up", "high_52w", "unusual_volume"} <= set(_keys(hits))
    gap = next(h for h in hits if h.scan == "gap_up")
    assert gap.metric == gap.change_pct == round((104 / 100.5 - 1) * 100, 2)


def test_a_quiet_day_matches_nothing_and_52_week_scans_need_a_years_history():
    assert scanner.evaluate(_history(), _quote("X", price=100.5, high=100.9), "t") == []
    short = scanner.evaluate(_history(n=100), _quote("X", price=104, open_=100.5, high=104.5, low=99.8), "t")
    assert "high_52w" not in _keys(short) and "breakout_20d" in _keys(short)


def test_the_scan_defers_while_every_exchange_is_closed(conn, mine_only):
    u = db.create_user(conn, "s@x.co", "h", "t")
    db.add_watch(conn, u.id, WatchItem(ticker="AAPL", note="", added_at="t"))
    with pytest.raises(ingest.SourceDeferred) as e:
        scanner.fetch(conn, SATURDAY, quote_fetch=lambda ts: pytest.fail("called Yahoo while closed"))
    opens = datetime(2026, 11, 2, 14, 30, tzinfo=timezone.utc)            # Monday 09:30 New York
    assert SATURDAY + timedelta(seconds=e.value.retry_after_seconds) == opens


def test_the_scan_runs_on_completed_bars_and_counts_tickers_without_history(conn, mine_only):
    u = db.create_user(conn, "s@x.co", "h", "t")
    for t in ("AAPL", "NEWCO"):
        db.add_watch(conn, u.id, WatchItem(ticker=t, note="", added_at="t"))
    _store(conn, "AAPL", [*_history(), OHLCBar(date=DAY, open=1, high=1, low=1, close=1, volume=1)])
    asked = []

    def quotes(ts):
        asked.extend(ts)
        return [_quote("AAPL", price=104, open_=104, high=104.5, low=103.8, volume=3e6), _quote("NEWCO")], 0

    result = scanner.fetch(conn, LIVE, quote_fetch=quotes)
    assert asked == ["AAPL", "NEWCO"] and {h.ticker for h in result} == {"AAPL"}
    assert "1 of 2 live tickers scanned, 1 waiting for history" in result.note


def test_history_is_fetched_stalest_first_trimmed_and_pruned(conn, mine_only, monkeypatch):
    u = db.create_user(conn, "s@x.co", "h", "t")
    for t in ("AAA", "BBB", "CCC"):
        db.add_watch(conn, u.id, WatchItem(ticker=t, note="", added_at="t"))
    _store(conn, "AAA", _history(), fetched_at=LIVE.isoformat())                     # fresh: not due
    _store(conn, "BBB", _history(), "scan_daily", fetched_at="2026-10-20T00:00:00+00:00")
    asked = []

    def fake(tickers, series):
        asked.extend(tickers)
        return [OHLCSeries(ticker=t, interval="scan_daily", fetched_at="now",
                           bars_json=json.dumps([b.model_dump() for b in _history(500)])) for t in tickers]

    monkeypatch.setattr(scanner.ohlc_source, "fetch", fake)
    monkeypatch.setattr(config, "SCANNER_HISTORY_PER_RUN", 1)
    out = scanner.history_fetch(conn, LIVE)
    assert asked == ["CCC"] and "1 more due" in out.note                                   # never fetched first
    assert len(json.loads(out[0].bars_json)) == scanner.HISTORY_BARS
    _store(conn, "GONE", _history(), "scan_daily")
    scanner.history_store(conn, out)
    assert db.get_ohlc(conn, "GONE", "scan_daily") == [] and db.get_ohlc(conn, "CCC", "scan_daily")
    monkeypatch.setattr(scanner.ohlc_source, "fetch", lambda tickers, series: [])
    with pytest.raises(RuntimeError, match="no daily bars"):
        scanner.history_fetch(conn, LIVE)


# ---------- alert throttling (STC-75) ----------

def _hits(*tickers, scan="unusual_volume"):
    return [scanner.ScanHit(scan=scan, ticker=t, day=DAY, price=10.0, change_pct=1.0, metric=float(i + 2),
                            first_seen_at="t", last_seen_at="t") for i, t in enumerate(tickers)]


def test_alerts_are_opt_in_capped_per_hour_one_per_ticker_a_day_and_the_rest_digested(conn):
    on = db.create_user(conn, "on@x.co", "h", "t")
    db.create_user(conn, "off@x.co", "h", "t")
    db.upsert_scanner_prefs(conn, ScannerPrefs(user_id=on.id, alerts_enabled=True, per_hour=2))
    first = scanner.alerts_for(conn, _hits("A", "B", "C", "D"), LIVE)
    assert {a.user_id for a in first} == {on.id}
    singles = [a for a in first if not a.dedup_key.startswith("scan-digest")]
    digest = [a for a in first if a.dedup_key.startswith("scan-digest")]
    assert [a.ticker for a in singles] == ["D", "C"]                        # strongest first
    assert len(digest) == 1 and "B (unusual volume)" in digest[0].message and "A" in digest[0].message

    later = LIVE + timedelta(minutes=30)
    second = scanner.alerts_for(conn, _hits("A", "B", "C", "D", "E"), later)
    assert second == []                                                    # A–D logged today; E waits for the hour
    after_hour = LIVE + timedelta(minutes=61)
    third = scanner.alerts_for(conn, _hits("E", "F"), after_hour)
    assert sorted(a.ticker for a in third if not a.dedup_key.startswith("scan-digest")) == ["F"]
    assert any(a.dedup_key.startswith("scan-digest") and "E" in a.message for a in third)


def test_the_throttle_ranks_by_scan_priority_then_strength():
    best = scanner.best_per_ticker(_hits("A", "B") + _hits("A", scan="gap_up"))
    assert [(h.ticker, h.scan, scans) for h, scans in best] == [
        ("B", "unusual_volume", ["unusual_volume"]), ("A", "unusual_volume", ["unusual_volume", "gap_up"])]
    send, hold = scanner.throttle(best, {"B"}, sent_last_hour=0, cap=5)
    assert [h.ticker for h, _ in send] == ["A"] and hold == []


# ---------- server-sent events (STC-73) ----------

def test_a_stream_gets_shared_events_and_only_its_own_users():
    async def run():
        async def connected():
            return False
        gen = events.stream(1, connected, max_seconds=2)
        assert (await gen.__anext__()).startswith("retry: 3000")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: (events.publish("order", {"n": 1}, user_id=2),
                                                  events.publish("order", {"n": 2}, user_id=1),
                                                  events.publish("scanner", {"matches": 3})))
        got = [await gen.__anext__(), await gen.__anext__()]
        await gen.aclose()
        return got

    got = asyncio.run(run())
    assert got == ['event: order\ndata: {"n":2}\n\n', 'event: scanner\ndata: {"matches":3}\n\n']
    assert events.subscriber_count() == 0


def test_a_stream_ends_after_its_maximum_life():
    async def run():
        async def connected():
            return False
        return [chunk async for chunk in events.stream(1, connected, max_seconds=0.05)]

    assert len(asyncio.run(run())) == 1


# ---------- API ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "scanner.db"))
    import importlib

    from app import main as main_module
    importlib.reload(config)
    importlib.reload(main_module)
    with TestClient(main_module.app) as c:
        authenticate(c)
        yield c, main_module


def test_api_view_and_prefs(client):
    c, m = client
    m.scanner.store(m.conn, _hits("AAPL"), LIVE)
    v = c.get("/api/scanner").json()
    assert v["hits"][0]["ticker"] == "AAPL" and v["hits"][0]["current"] is True
    assert {s["key"] for s in v["scans"]} >= {"unusual_volume", "gap_up", "high_52w", "rsi_oversold"}
    assert v["prefs"]["alerts_enabled"] is False and v["prefs"]["per_hour"] == config.SCANNER_ALERTS_PER_HOUR
    assert c.put("/api/scanner/prefs", json={"alerts_enabled": True, "per_hour": 0}).status_code == 422
    assert c.put("/api/scanner/prefs", json={"alerts_enabled": True, "per_hour": 5}).json()["per_hour"] == 5
    assert c.get("/api/scanner").json()["prefs"]["alerts_enabled"] is True


def test_api_stream_delivers_a_published_event_over_http(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(config, "STREAM_MAX_SECONDS", 1.5)
    timer = threading.Timer(0.5, lambda: events.publish("scanner", {"matches": 7}))
    timer.start()
    r = c.get("/api/stream")
    timer.join()
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.text.startswith("retry: 3000\n\nevent: hello\n")
    assert 'event: scanner\ndata: {"matches":7}\n\n' in r.text
