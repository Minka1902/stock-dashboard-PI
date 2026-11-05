"""Background analysis universe: portfolio ∪ watchlist ∪ capped signal candidates,
plus stalest-first OHLC rotation."""
import sqlite3

import pytest

from app import config, db
from app.models import (
    AnalystSignal, CongressTrade, InsiderTrade, OHLCSeries,
    SocialSentiment, WatchItem,
)
from tests.conftest import buy


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    db.init_schema(c)
    yield c
    c.close()


def _seed_signals(conn):
    db.upsert_congress_trades(conn, [CongressTrade(
        trade_hash="h1", representative="Rep", party="D", state="CA", ticker="GOOG",
        asset_description="Stock", transaction_date="2026-07-05", transaction_type="Purchase",
        amount_range="$1,001 - $15,000", filed_at="2026-07-06", chamber="house")])
    db.upsert_trades(conn, [InsiderTrade(
        accession="a1", ticker="TSLA", company="Tesla", owner="O", role="Dir",
        transaction_date="2026-07-04", transaction_type="Buy", shares=10, value=1000,
        filing_url="u", filed_at="2026-07-04")])
    db.upsert_analyst_signals(conn, [AnalystSignal(
        ticker="NFLX", fetched_at="2026-07-03", next_earnings=None, rec_strong_buy=1,
        rec_buy=1, rec_hold=0, rec_sell=0, recent_upgrades=2, recent_downgrades=0,
        latest_action="up", latest_firm="X", latest_to_grade="Buy")])
    db.upsert_social_sentiment(conn, [SocialSentiment(
        ticker="AMD", fetched_at="2026-07-02", mentions=100, upvotes=50, rank=3,
        rank_24h_ago=20, rank_change=17)])
    conn.execute(
        "INSERT INTO contracts (external_id, award_id, recipient_name, amount, "
        "awarding_agency, start_date, source, ticker) "
        "VALUES ('e1','a1','Lockheed',1,'DoD','2026-07-01','usaspending','LMT')")
    conn.commit()


def test_signal_candidates_recent_first(conn):
    _seed_signals(conn)
    cands = db.get_signal_candidate_tickers(conn, limit=20)
    assert set(cands) == {"GOOG", "TSLA", "NFLX", "AMD", "LMT"}
    # most-recent first (congress 07-05 leads)
    assert cands[0] == "GOOG"


def test_signal_candidates_capped(conn):
    _seed_signals(conn)
    assert len(db.get_signal_candidate_tickers(conn, limit=2)) == 2


def test_analysis_universe_is_union(conn):
    _seed_signals(conn)
    db.add_watch(conn, 0, WatchItem(ticker="AAPL", note="", added_at="t"))
    buy(conn, 0, "MSFT", 1, 1)
    universe = db.get_analysis_universe(conn, candidate_limit=20)
    assert {"AAPL", "MSFT", "GOOG", "TSLA", "NFLX", "AMD", "LMT"} <= set(universe)


def test_analysis_universe_empty(conn):
    assert db.get_analysis_universe(conn) == []


def test_ohlc_fetched_at_and_prune(conn):
    db.upsert_ohlc(conn, [
        OHLCSeries(ticker="AAA", interval="daily", bars_json="[]", fetched_at="2026-07-10"),
        OHLCSeries(ticker="BBB", interval="daily", bars_json="[]", fetched_at="2026-07-11"),
    ])
    fetched = db.get_ohlc_fetched_at(conn)
    assert fetched == {"AAA": "2026-07-10", "BBB": "2026-07-11"}


def test_prune_analyses_drops_out_of_universe(conn):
    from app.models import StockAnalysis
    db.upsert_analyses(conn, [
        StockAnalysis(ticker="KEEP", computed_at="t", price=1.0),
        StockAnalysis(ticker="DROP", computed_at="t", price=1.0),
    ])
    removed = db.prune_analyses(conn, ["KEEP"])
    assert removed == 1
    assert db.get_analysis(conn, "DROP") is None
    assert db.get_analysis(conn, "KEEP") is not None


def test_ohlc_fetch_is_stalest_first_and_capped(monkeypatch):
    import app.main as main
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    db.init_schema(c)
    for t in ("AAA", "BBB", "CCC", "DDD"):
        db.add_watch(c, 0, WatchItem(ticker=t, note="", added_at="t"))
    # AAA already has fresh OHLC; the others were never fetched → they come first.
    db.upsert_ohlc(c, [OHLCSeries(ticker="AAA", interval="daily", bars_json="[]",
                                  fetched_at="2026-07-12T00:00:00")])
    captured = {}

    def fake_fetch(tickers, series=None):
        captured["max" if series else "t"] = list(tickers)
        return []

    monkeypatch.setattr(main.ohlc_source, "fetch", fake_fetch)
    monkeypatch.setattr(config, "OHLC_MAX_TICKERS", 2)
    monkeypatch.setattr(config, "BENCHMARK_TICKERS", ["SPY"])
    main.ohlc_fetch(c)
    c.close()
    assert captured["t"][0] == "SPY"       # benchmarks always ride along, outside the cap
    captured["t"] = captured["t"][1:]
    assert len(captured["t"]) == 2
    assert "AAA" not in captured["t"]      # already fetched → not stalest
    # The full adjusted history: benchmarks + watchlist, none stored yet, capped per run.
    assert captured["max"] == ["SPY", "AAA", "BBB", "CCC", "DDD"][: config.DAILY_MAX_PER_RUN]
