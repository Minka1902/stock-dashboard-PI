"""Suggestion history: recording, scope, and read-time outcome derivation."""
import json
from datetime import date, timedelta

from app import db, suggestion_history
from app.models import OHLCBar, OHLCSeries, SuggestionHistoryEntry


def _seed_bars(conn, ticker, points):
    """points = [(iso_date, close), ...]"""
    bars = [
        OHLCBar(date=d, open=c, high=c, low=c, close=c, volume=1000)
        for d, c in points
    ]
    db.upsert_ohlc(conn, [OHLCSeries(
        ticker=ticker, interval="daily",
        bars_json=json.dumps([b.model_dump() for b in bars]),
        fetched_at="2026-07-26T00:00:00+00:00",
    )])


def _digest(for_date="2026-07-01", holdings=(), opportunities=(), new_ideas=()):
    return {
        "for_date": for_date,
        "holdings_alerts": list(holdings),
        "opportunities": list(opportunities),
        "new_ideas": list(new_ideas),
        "seasonality": [],
    }


# ---- what gets recorded ----

def test_records_holdings_and_watchlist_only(conn):
    d = _digest(
        holdings=[{"ticker": "AAPL", "action": "Trim", "price": 100.0}],
        opportunities=[{"ticker": "PLTR", "recommendation": "buy", "conviction": 70,
                        "entry": 20.0}],
        new_ideas=[{"ticker": "GME", "recommendation": "buy", "conviction": 90,
                    "entry": 30.0}],
    )
    assert suggestion_history.record(conn, d, user_id=1) == 2
    rows = db.get_suggestion_history(conn, 1)
    by_ticker = {r.ticker: r for r in rows}
    assert set(by_ticker) == {"AAPL", "PLTR"}, "new ideas must not be tracked"
    assert by_ticker["AAPL"].kind == "holding"
    assert by_ticker["PLTR"].kind == "watchlist"
    assert by_ticker["PLTR"].action == "BUY · conv 70"


def test_first_write_for_a_day_wins(conn):
    first = _digest(holdings=[{"ticker": "AAPL", "action": "Trim", "price": 100.0}])
    later = _digest(holdings=[{"ticker": "AAPL", "action": "Hold", "price": 130.0}])
    suggestion_history.record(conn, first, 1)
    assert suggestion_history.record(conn, later, 1) == 0
    row = db.get_suggestion_history(conn, 1)[0]
    assert row.action == "Trim" and row.price == 100.0


def test_history_is_per_user(conn):
    d = _digest(holdings=[{"ticker": "AAPL", "action": "Hold", "price": 100.0}])
    suggestion_history.record(conn, d, 1)
    assert len(db.get_suggestion_history(conn, 1)) == 1
    assert db.get_suggestion_history(conn, 2) == []


def test_price_falls_back_to_the_last_close(conn):
    _seed_bars(conn, "AAPL", [("2026-06-30", 111.0)])
    d = _digest(opportunities=[{"ticker": "AAPL", "recommendation": "buy",
                                "conviction": 50, "entry": None}])
    suggestion_history.record(conn, d, 1)
    assert db.get_suggestion_history(conn, 1)[0].price == 111.0


def test_ta_pending_records_the_boom_read(conn):
    d = _digest(opportunities=[{"ticker": "AAPL", "ta_pending": True, "score": 61}])
    suggestion_history.record(conn, d, 1)
    assert db.get_suggestion_history(conn, 1)[0].action == "Boom 61 (TA pending)"


# ---- outcomes derived at read time ----

def test_outcomes_measure_the_move_after_the_call(conn):
    start = date.today() - timedelta(days=60)
    _seed_bars(conn, "AAPL", [
        (start.isoformat(), 100.0),
        ((start + timedelta(days=7)).isoformat(), 110.0),    # +10% at 7d
        ((start + timedelta(days=30)).isoformat(), 170.0),   # +70% at 30d
        ((start + timedelta(days=59)).isoformat(), 150.0),   # +50% to date
    ])
    db.record_suggestion_history(conn, [SuggestionHistoryEntry(
        user_id=1, ticker="AAPL", for_date=start.isoformat(), kind="watchlist",
        action="BUY", price=100.0, created_at="2026-07-01T00:00:00+00:00",
    )])
    rows = suggestion_history.with_outcomes(conn, db.get_suggestion_history(conn, 1))
    o = rows[0]["outcomes"]
    assert o["d7"] == 10.0
    assert o["d30"] == 70.0
    assert o["since"] == 50.0


def test_outcome_uses_the_first_close_on_or_after_the_horizon(conn):
    """Horizons land on weekends and holidays constantly; the next available
    close must be used rather than dropping the horizon."""
    start = date.today() - timedelta(days=40)
    _seed_bars(conn, "AAPL", [
        (start.isoformat(), 100.0),
        ((start + timedelta(days=9)).isoformat(), 120.0),  # no bar at exactly +7d
    ])
    db.record_suggestion_history(conn, [SuggestionHistoryEntry(
        user_id=1, ticker="AAPL", for_date=start.isoformat(), kind="watchlist",
        action="BUY", price=100.0, created_at="2026-07-01T00:00:00+00:00",
    )])
    rows = suggestion_history.with_outcomes(conn, db.get_suggestion_history(conn, 1))
    assert rows[0]["outcomes"]["d7"] == 20.0


def test_unelapsed_horizon_is_pending_not_zero(conn):
    today = date.today()
    _seed_bars(conn, "AAPL", [(today.isoformat(), 100.0)])
    db.record_suggestion_history(conn, [SuggestionHistoryEntry(
        user_id=1, ticker="AAPL", for_date=today.isoformat(), kind="watchlist",
        action="BUY", price=100.0, created_at="2026-07-01T00:00:00+00:00",
    )])
    rows = suggestion_history.with_outcomes(conn, db.get_suggestion_history(conn, 1))
    assert rows[0]["outcomes"]["d7"] is None
    assert rows[0]["outcomes"]["d30"] is None


def test_no_bars_means_no_invented_outcome(conn):
    db.record_suggestion_history(conn, [SuggestionHistoryEntry(
        user_id=1, ticker="ZZZZ", for_date="2026-01-01", kind="watchlist",
        action="BUY", price=100.0, created_at="2026-01-01T00:00:00+00:00",
    )])
    rows = suggestion_history.with_outcomes(conn, db.get_suggestion_history(conn, 1, months=24))
    assert rows[0]["outcomes"] == {"d7": None, "d30": None, "since": None}
    assert rows[0]["last_close"] is None


def test_filter_by_ticker(conn):
    for t in ("AAPL", "PLTR"):
        db.record_suggestion_history(conn, [SuggestionHistoryEntry(
            user_id=1, ticker=t, for_date="2026-07-01", kind="watchlist",
            action="BUY", price=1.0, created_at="2026-07-01T00:00:00+00:00",
        )])
    assert [r.ticker for r in db.get_suggestion_history(conn, 1, ticker="aapl")] == ["AAPL"]
