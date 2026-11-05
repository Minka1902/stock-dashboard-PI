"""Earnings parsing and storage.

The flag that matters is is_estimate: Yahoo projects a forward date when the
company hasn't confirmed one, and rendering a projection as a fixture would be
exactly the false certainty this app is supposed to avoid.
"""
from datetime import datetime, timezone

from app import db
from app.models import EarningsEvent
from app.sources import earnings


def _ts(year, month, day, hour=21):
    return int(datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp())


def _payload(*, estimate, earnings_ts, history=()):
    return {
        "quoteSummary": {"result": [{
            "calendarEvents": {"earnings": {
                "earningsDate": [{"raw": earnings_ts}],
                "isEarningsDateEstimate": estimate,
                "earningsAverage": {"raw": 2.35},
                "revenueAverage": {"raw": 94_000_000_000},
            }},
            "earningsHistory": {"history": list(history)},
        }]},
    }


def test_estimated_forward_date_is_flagged():
    events = earnings.parse_response(
        _payload(estimate=True, earnings_ts=_ts(2026, 8, 14)), "AAPL", "now")
    assert len(events) == 1
    assert events[0].is_estimate is True
    assert events[0].eps_estimate == 2.35


def test_confirmed_forward_date_is_not_flagged():
    events = earnings.parse_response(
        _payload(estimate=False, earnings_ts=_ts(2026, 8, 14)), "AAPL", "now")
    assert events[0].is_estimate is False


def test_timing_derived_from_the_hour():
    amc = earnings.parse_response(
        _payload(estimate=False, earnings_ts=_ts(2026, 8, 14, 21)), "AAPL", "now")
    assert amc[0].timing == "amc"
    bmo = earnings.parse_response(
        _payload(estimate=False, earnings_ts=_ts(2026, 8, 14, 11)), "AAPL", "now")
    assert bmo[0].timing == "bmo"


def test_date_only_entry_does_not_guess_timing():
    """Midnight UTC means Yahoo gave a date, not a time — don't invent one."""
    events = earnings.parse_response(
        _payload(estimate=True, earnings_ts=_ts(2026, 8, 14, 0)), "AAPL", "now")
    assert events[0].timing == ""


def test_past_quarters_carry_actuals_and_are_never_estimates():
    history = [
        {"quarter": {"raw": _ts(2026, 5, 1)}, "epsActual": {"raw": 1.53},
         "epsEstimate": {"raw": 1.50}, "surprisePercent": {"raw": 0.02}, "period": "-1q"},
    ]
    events = earnings.parse_response(
        _payload(estimate=True, earnings_ts=_ts(2026, 8, 14), history=history),
        "AAPL", "now")
    past = [e for e in events if e.eps_actual is not None]
    assert len(past) == 1
    assert past[0].is_estimate is False   # it already happened
    assert past[0].eps_actual == 1.53
    assert past[0].quarter == "-1q"


def test_garbage_payload_yields_nothing_rather_than_raising():
    assert earnings.parse_response({}, "AAPL", "now") == []
    assert earnings.parse_response({"quoteSummary": {"result": []}}, "AAPL", "now") == []


def test_fetch_with_no_tickers_is_a_noop():
    assert earnings.fetch([]) == []


# ---- storage ----

def _event(ticker, date, **kw):
    base = dict(ticker=ticker, event_date=date, is_estimate=True, timing="amc",
                source="yahoo", fetched_at="2026-08-01T00:00:00+00:00")
    base.update(kw)
    return EarningsEvent(**base)


def test_upsert_and_range_query(conn):
    db.upsert_earnings(conn, [
        _event("AAPL", "2026-08-14"),
        _event("MSFT", "2026-07-29"),
        _event("NVDA", "2026-09-02"),
    ])
    rows = db.get_earnings(conn, date_from="2026-08-01", date_to="2026-08-31")
    assert [r.ticker for r in rows] == ["AAPL"]
    assert db.get_earnings(conn, tickers=["MSFT", "NVDA"]).__len__() == 2


def test_reported_figures_survive_a_forward_only_refetch(conn):
    """A later fetch carrying only the calendar must not wipe an actual."""
    db.upsert_earnings(conn, [
        _event("AAPL", "2026-05-01", is_estimate=False, eps_actual=1.53,
               eps_estimate=1.50, surprise_pct=0.02, quarter="-1q"),
    ])
    db.upsert_earnings(conn, [_event("AAPL", "2026-05-01", eps_estimate=1.50)])

    row = db.get_earnings_for(conn, "AAPL")[0]
    assert row.eps_actual == 1.53
    assert row.surprise_pct == 0.02
    assert row.quarter == "-1q"


def test_estimate_flag_round_trips_as_a_bool(conn):
    db.upsert_earnings(conn, [
        _event("AAPL", "2026-08-14", is_estimate=True),
        _event("MSFT", "2026-08-15", is_estimate=False),
    ])
    flags = {r.ticker: r.is_estimate for r in db.get_earnings(conn)}
    assert flags == {"AAPL": True, "MSFT": False}


def test_earnings_index_exists(conn):
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    assert "idx_earnings_date" in names
