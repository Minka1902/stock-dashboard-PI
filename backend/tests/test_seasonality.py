import json
from datetime import date, timedelta

from app.sources.seasonality import (
    compute_seasonality,
    parse_response,
    _forward_return,
    _calendar_window_return,
)


def _daily_series(start: date, days: int, start_price: float = 100.0, step: float = 0.1):
    """A continuous daily (date, close) series — weekends included for simplicity."""
    series = []
    price = start_price
    for i in range(days):
        series.append((start + timedelta(days=i), round(price, 4)))
        price += step
    return series


def test_forward_return_uses_nearest_trading_day():
    # Rising 1/day from 2020-01-01.
    series = _daily_series(date(2020, 1, 1), 400, start_price=100.0, step=1.0)
    # Next-week (7 cal days) return anchored at Jan 10, 2020.
    r = _forward_return(series, 2020, (1, 10), 7)
    assert r is not None
    # close at day index 9 = 109, +7 days = 116 → 116/109 - 1 (rounded to 4 dp).
    assert r == round(116 / 109 - 1, 4)


def test_forward_return_none_before_ticker_existed():
    series = _daily_series(date(2022, 1, 1), 100)
    # Anchor in 2019 — no data that year.
    assert _forward_return(series, 2019, (3, 15), 7) is None


def test_calendar_month_return():
    series = _daily_series(date(2021, 1, 1), 365, start_price=100.0, step=1.0)
    anchor = date(2026, 3, 15)  # March
    r = _calendar_window_return(series, 2021, "month", anchor)
    assert r is not None
    # March 2021 spans index 59 (Mar 1) .. index 89 (Mar 31): prices 159..189.
    assert r > 0  # rising series → positive month


def test_compute_seasonality_builds_all_windows():
    series = _daily_series(date(2018, 1, 1), 365 * 6, start_price=100.0, step=0.05)
    today = date(2026, 6, 15)
    windows = compute_seasonality(series, today)
    keys = {w["key"] for w in windows}
    assert keys == {"fwd_day", "fwd_week", "fwd_month", "cal_week", "cal_month"}
    # Multiple prior years should be present for the monthly window.
    cal_month = next(w for w in windows if w["key"] == "cal_month")
    assert len(cal_month["per_year"]) >= 3
    assert all("year" in e and "return" in e for e in cal_month["per_year"])


def test_compute_seasonality_excludes_current_year():
    series = _daily_series(date(2018, 1, 1), 365 * 9, start_price=100.0, step=0.05)
    today = date(2026, 6, 15)
    windows = compute_seasonality(series, today)
    years = {e["year"] for w in windows for e in w["per_year"]}
    assert 2026 not in years
    assert max(years) <= 2025


def test_parse_response_from_yahoo_timestamps():
    series = _daily_series(date(2019, 1, 1), 365 * 6, start_price=50.0, step=0.05)
    # Build a Yahoo-shaped payload with unix timestamps.
    import calendar
    timestamps = [calendar.timegm(d.timetuple()) for d, _ in series]
    closes = [c for _, c in series]
    payload = {
        "chart": {"result": [{
            "timestamp": timestamps,
            "indicators": {"quote": [{"close": closes}]},
        }]}
    }
    season = parse_response(payload, "test", "2026-06-15T00:00:00+00:00", today=date(2026, 6, 15))
    assert season is not None
    assert season.ticker == "TEST"
    assert season.as_of == "06-15"
    windows = json.loads(season.windows_json)
    assert len(windows) == 5
    assert season.history_years >= 3


def test_history_params_asks_for_daily_bars_explicitly_for_max():
    """range=max makes Yahoo return monthly bars; max is sent as period1/period2."""
    from app.sources.seasonality import history_params
    assert history_params("max", now_ts=1_790_000_000) == {
        "interval": "1d", "period1": 0, "period2": 1_790_000_000}
    assert history_params("10y") == {"interval": "1d", "range": "10y"}


def test_parse_response_refuses_non_daily_bars():
    """Monthly bars stamped on the 1st would turn anchors into month-start
    dates with month-end closes — refuse them instead of mis-computing."""
    series = _daily_series(date(2019, 1, 1), 365 * 6, start_price=50.0, step=0.05)
    import calendar
    payload = {
        "chart": {"result": [{
            "meta": {"dataGranularity": "1mo"},
            "timestamp": [calendar.timegm(d.timetuple()) for d, _ in series],
            "indicators": {"quote": [{"close": [c for _, c in series]}]},
        }]}
    }
    assert parse_response(payload, "x", "2026-06-15T00:00:00+00:00", today=date(2026, 6, 15)) is None
    payload["chart"]["result"][0]["meta"]["dataGranularity"] = "1d"
    assert parse_response(payload, "x", "2026-06-15T00:00:00+00:00", today=date(2026, 6, 15)) is not None


def test_parse_response_none_on_empty():
    payload = {"chart": {"result": []}}
    assert parse_response(payload, "x", "2026-06-15T00:00:00+00:00") is None


def test_parse_response_none_on_insufficient_history():
    # Only one prior year of data → no window reaches 2 years.
    series = _daily_series(date(2025, 1, 1), 200, start_price=100.0, step=0.1)
    import calendar
    timestamps = [calendar.timegm(d.timetuple()) for d, _ in series]
    closes = [c for _, c in series]
    payload = {
        "chart": {"result": [{
            "timestamp": timestamps,
            "indicators": {"quote": [{"close": closes}]},
        }]}
    }
    season = parse_response(payload, "x", "2026-06-15T00:00:00+00:00", today=date(2026, 6, 15))
    assert season is None


# ---------- "this day N years ago" anchors ----------

def test_compute_anchors_picks_nearest_on_or_before():
    from app.sources.seasonality import compute_anchors
    # Weekday-only series: skip Saturdays/Sundays so weekend targets must fall
    # back to the prior Friday.
    series = []
    price = 100.0
    d = date(2015, 1, 1)
    while d <= date(2026, 7, 7):
        if d.weekday() < 5:
            series.append((d, round(price, 4)))
            price += 0.1
        d += timedelta(days=1)

    today = date(2026, 7, 7)
    anchors = compute_anchors(series, today)
    by_key = {a["years_ago"]: a for a in anchors}
    assert set(by_key) == {1, 2, 5, "max"}
    # 2025-07-07 was a Monday (trading day) — exact hit.
    assert by_key[1]["date"] == "2025-07-07"
    # 2024-07-07 was a Sunday — nearest on-or-before is Friday 2024-07-05.
    assert by_key[2]["date"] == "2024-07-05"
    # 2021-07-07 was a Wednesday — exact hit.
    assert by_key[5]["date"] == "2021-07-07"
    # max = earliest trading day on record.
    assert by_key["max"]["date"] == "2015-01-01"
    assert all(isinstance(a["close"], float) for a in anchors)


def test_compute_anchors_short_history_skips_missing_years():
    from app.sources.seasonality import compute_anchors
    series = _daily_series(date(2024, 9, 1), 500)  # ~1.4 years of data
    anchors = compute_anchors(series, date(2026, 1, 10))
    keys = [a["years_ago"] for a in anchors]
    assert keys == [1, "max"]  # 2y and 5y unavailable; never fabricated
    assert anchors[-1]["date"] == "2024-09-01"


def test_compute_anchors_leap_day_clamps():
    from app.sources.seasonality import compute_anchors
    series = _daily_series(date(2020, 1, 1), 365 * 7)
    anchors = compute_anchors(series, date(2024, 2, 29))
    by_key = {a["years_ago"]: a for a in anchors}
    # 2023 has no Feb 29 — clamp to Feb 28.
    assert by_key[1]["date"] == "2023-02-28"


def test_compute_anchors_empty_series():
    from app.sources.seasonality import compute_anchors
    assert compute_anchors([], date(2026, 7, 7)) == []


def test_parse_response_includes_anchors_and_roundtrips(conn):
    from app import db
    from datetime import datetime, timezone

    start = date(2019, 1, 6)
    days = (date(2026, 7, 7) - start).days
    ts, closes = [], []
    price = 50.0
    for i in range(days):
        d = start + timedelta(days=i)
        ts.append(int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()))
        closes.append(round(price, 2))
        price += 0.05
    payload = {"chart": {"result": [{
        "timestamp": ts,
        "indicators": {"quote": [{"close": closes}]},
    }]}}

    season = parse_response(payload, "aapl", "2026-07-07T00:00:00+00:00",
                            today=date(2026, 7, 7))
    assert season is not None
    anchors = json.loads(season.anchors_json)
    assert [a["years_ago"] for a in anchors] == [1, 2, 5, "max"]

    db.upsert_seasonality(conn, [season])
    stored = db.get_seasonality_for(conn, "AAPL")
    assert json.loads(stored.anchors_json) == anchors
