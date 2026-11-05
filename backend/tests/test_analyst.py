"""Unit tests for the analyst source module."""
import calendar
from datetime import datetime, timedelta, timezone

from app.sources.analyst import parse_response


_FETCHED = "2026-06-22T10:00:00+00:00"

# Timestamps are relative to today, because the parser filters on "is it in the
# future" and "was it in the last N days" — fixed calendar dates silently age
# out and the suite starts failing on a date rather than on a change.
#
# Anchored at 12:00 UTC and built with calendar.timegm (not time.mktime, which
# reads local time) so converting back to a date can't slip a day either way.
def _ts(days_from_today: int) -> int:
    d = datetime.now(timezone.utc).date() + timedelta(days=days_from_today)
    return calendar.timegm(datetime(d.year, d.month, d.day, 12, 0).timetuple())


def _iso(days_from_today: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=days_from_today)).isoformat()


_FUTURE_TS = _ts(20)    # upcoming earnings
_PAST_TS = _ts(-90)     # long past — outside the "recent" window
_RECENT_TS = _ts(-5)    # inside the recent-actions window


def _payload(
    earnings_dates=None,
    history=None,
    trends=None,
):
    result = {}
    if earnings_dates is not None:
        result["calendarEvents"] = {
            "earnings": {"earningsDate": [{"raw": ts} for ts in earnings_dates]}
        }
    if history is not None:
        result["upgradeDowngradeHistory"] = {"history": history}
    if trends is not None:
        result["recommendationTrend"] = {"trend": trends}
    return {"quoteSummary": {"result": [result], "error": None}}


def test_parse_next_earnings_future_date():
    payload = _payload(earnings_dates=[_FUTURE_TS])
    result = parse_response(payload, "AAPL", _FETCHED)
    assert result is not None
    assert result.next_earnings == _iso(20)


def test_parse_next_earnings_past_date_skipped():
    payload = _payload(earnings_dates=[_PAST_TS])
    result = parse_response(payload, "AAPL", _FETCHED)
    assert result is not None
    assert result.next_earnings is None


def test_parse_recent_upgrades_counted():
    history = [
        {"epochGradeDate": _RECENT_TS, "action": "up", "firm": "GS", "toGrade": "Buy"},
        {"epochGradeDate": _RECENT_TS, "action": "init", "firm": "MS", "toGrade": "Outperform"},
        {"epochGradeDate": _PAST_TS, "action": "up", "firm": "JPM", "toGrade": "Buy"},  # too old
    ]
    payload = _payload(history=history)
    result = parse_response(payload, "AAPL", _FETCHED)
    assert result is not None
    assert result.recent_upgrades == 2  # only recent ones
    assert result.recent_downgrades == 0


def test_parse_recent_downgrades_counted():
    history = [
        {"epochGradeDate": _RECENT_TS, "action": "down", "firm": "GS", "toGrade": "Sell"},
        {"epochGradeDate": _RECENT_TS, "action": "down", "firm": "MS", "toGrade": "Hold"},
    ]
    payload = _payload(history=history)
    result = parse_response(payload, "AAPL", _FETCHED)
    assert result is not None
    assert result.recent_downgrades == 2
    assert result.recent_upgrades == 0


def test_parse_latest_action_from_most_recent_entry():
    history = [
        {"epochGradeDate": _RECENT_TS, "action": "up", "firm": "GS", "toGrade": "Buy"},
        {"epochGradeDate": _PAST_TS, "action": "down", "firm": "JPM", "toGrade": "Sell"},
    ]
    payload = _payload(history=history)
    result = parse_response(payload, "AAPL", _FETCHED)
    assert result is not None
    assert result.latest_action == "up"
    assert result.latest_firm == "GS"
    assert result.latest_to_grade == "Buy"


def test_parse_recommendation_trend():
    trends = [
        {"period": "0m", "strongBuy": 10, "buy": 20, "hold": 5, "sell": 1},
        {"period": "-1m", "strongBuy": 8, "buy": 18, "hold": 6, "sell": 2},
    ]
    payload = _payload(trends=trends)
    result = parse_response(payload, "AAPL", _FETCHED)
    assert result is not None
    assert result.rec_strong_buy == 10
    assert result.rec_buy == 20
    assert result.rec_hold == 5
    assert result.rec_sell == 1


def test_parse_missing_result_returns_none():
    payload = {"quoteSummary": {"result": None, "error": "not found"}}
    assert parse_response(payload, "AAPL", _FETCHED) is None


def test_parse_empty_payload_gives_defaults():
    payload = _payload()  # no sections
    result = parse_response(payload, "AAPL", _FETCHED)
    assert result is not None
    assert result.next_earnings is None
    assert result.recent_upgrades == 0
    assert result.recent_downgrades == 0
    assert result.latest_action is None
