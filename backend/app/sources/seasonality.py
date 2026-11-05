"""Seasonality — "what happened at this time of year in past years" per watchlist ticker.

Pulls deep daily history from Yahoo Finance (keyless) and, for the current
calendar anchor (today's month/day), computes per-year returns over several
windows:

  forward windows (return over the COMING day / week / month from the anchor):
    fwd_day, fwd_week, fwd_month
  calendar windows (return DURING the anchor's calendar week / month that year):
    cal_week, cal_month

Each window stores one entry per prior year, so the frontend can re-slice to any
lookback (5 / 10 / all years) and compute aggregates client-side without a refetch.
"""
import json
from datetime import date, datetime, timedelta, timezone

import httpx

from app import currency
from app.models import Seasonality

_YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
_YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

_DEFAULT_RANGE = "max"


def history_params(range_str: str, now_ts: int | None = None) -> dict:
    """Yahoo chart query params for daily bars over `range_str`.

    Yahoo silently downgrades ``range=max&interval=1d`` to MONTHLY bars for any
    ticker with a long history (the response's meta.dataGranularity says
    "1mo"), which turned every forward-week window and every "this day N years
    ago" anchor into month-bar arithmetic. An explicit period1=0 → now span
    keeps the daily granularity, so "max" is asked for that way.
    """
    if range_str == "max":
        if now_ts is None:
            now_ts = int(datetime.now(timezone.utc).timestamp())
        return {"interval": "1d", "period1": 0, "period2": now_ts}
    return {"interval": "1d", "range": range_str}

# Window definitions: (key, label, kind, calendar_days_for_forward)
_FORWARD_WINDOWS = [
    ("fwd_day", "Next Day", 1),
    ("fwd_week", "Next Week", 7),
    ("fwd_month", "Next Month", 30),
]
_CALENDAR_WINDOWS = [
    ("cal_week", "This Calendar Week", "week"),
    ("cal_month", "This Calendar Month", "month"),
]


# ---------- helpers (pure, unit-testable) ----------

def _nearest_on_or_after(series: list[tuple[date, float]], target: date,
                         tolerance_days: int = 5) -> int | None:
    """Index of the first trading day on/after `target` within tolerance, else None.

    `series` must be sorted ascending by date.
    """
    limit = target + timedelta(days=tolerance_days)
    for i, (d, _c) in enumerate(series):
        if d < target:
            continue
        if d > limit:
            return None
        return i
    return None


def _nearest_on_or_before(series: list[tuple[date, float]], target: date,
                          tolerance_days: int = 5) -> int | None:
    """Index of the last trading day on/before `target` within tolerance, else None."""
    floor = target - timedelta(days=tolerance_days)
    found = None
    for i, (d, _c) in enumerate(series):
        if d < floor:
            continue
        if d > target:
            break
        found = i
    return found


def _forward_return(series: list[tuple[date, float]], year: int,
                    anchor_md: tuple[int, int], calendar_days: int) -> float | None:
    """Return over `calendar_days` forward from the anchor date in `year`."""
    try:
        start_target = date(year, anchor_md[0], anchor_md[1])
    except ValueError:
        return None  # e.g. Feb 29 in a non-leap year
    start_i = _nearest_on_or_after(series, start_target)
    if start_i is None:
        return None
    end_target = series[start_i][0] + timedelta(days=calendar_days)
    end_i = _nearest_on_or_before(series, end_target, tolerance_days=7)
    if end_i is None or end_i <= start_i:
        return None
    start_close = series[start_i][1]
    end_close = series[end_i][1]
    if not start_close:
        return None
    return round(end_close / start_close - 1.0, 4)


def _calendar_window_return(series: list[tuple[date, float]], year: int,
                            kind: str, anchor: date) -> float | None:
    """Return DURING the anchor's calendar week or month in `year`."""
    if kind == "week":
        # ISO week containing the anchor's month/day in `year`.
        try:
            anchor_in_year = date(year, anchor.month, anchor.day)
        except ValueError:
            return None
        start_d = anchor_in_year - timedelta(days=anchor_in_year.weekday())  # Monday
        end_d = start_d + timedelta(days=6)  # Sunday
    else:  # month
        start_d = date(year, anchor.month, 1)
        if anchor.month == 12:
            end_d = date(year, 12, 31)
        else:
            end_d = date(year, anchor.month + 1, 1) - timedelta(days=1)

    start_i = _nearest_on_or_after(series, start_d, tolerance_days=6)
    end_i = _nearest_on_or_before(series, end_d, tolerance_days=6)
    if start_i is None or end_i is None or end_i <= start_i:
        return None
    start_close = series[start_i][1]
    end_close = series[end_i][1]
    if not start_close:
        return None
    return round(end_close / start_close - 1.0, 4)


def compute_seasonality(series: list[tuple[date, float]], today: date) -> list[dict]:
    """Build all window objects with per-year returns from a (date, close) series."""
    if not series:
        return []
    series = sorted(series, key=lambda x: x[0])
    anchor_md = (today.month, today.day)
    current_year = today.year
    years = sorted({d.year for d, _ in series if d.year < current_year})

    windows: list[dict] = []

    for key, label, cal_days in _FORWARD_WINDOWS:
        per_year = []
        for y in years:
            r = _forward_return(series, y, anchor_md, cal_days)
            if r is not None:
                per_year.append({"year": y, "return": r})
        windows.append({"key": key, "label": label, "kind": "forward", "per_year": per_year})

    for key, label, kind in _CALENDAR_WINDOWS:
        per_year = []
        for y in years:
            r = _calendar_window_return(series, y, kind, today)
            if r is not None:
                per_year.append({"year": y, "return": r})
        windows.append({"key": key, "label": label, "kind": "calendar", "per_year": per_year})

    return windows


# "Where was this stock on this day N years ago?" — persisted alongside the
# window stats so the analysis page and report can show it without a refetch.
_ANCHOR_YEARS = [1, 2, 5]


def compute_anchors(series: list[tuple[date, float]], today: date) -> list[dict]:
    """Closing price on (or just before) today's date 1/2/5/max years ago.

    Each anchor records the real trading date used and its close; the %-change
    vs the current price is computed at render time so it never goes stale.
    """
    if not series:
        return []
    series = sorted(series, key=lambda x: x[0])
    anchors: list[dict] = []
    used_dates: set[date] = set()
    for n in _ANCHOR_YEARS:
        try:
            target = date(today.year - n, today.month, today.day)
        except ValueError:  # Feb 29 in a non-leap year
            target = date(today.year - n, today.month, 28)
        i = _nearest_on_or_before(series, target, tolerance_days=7)
        if i is None:
            continue
        d, close = series[i]
        anchors.append({"years_ago": n, "date": d.isoformat(), "close": round(close, 4)})
        used_dates.add(d)
    # "max": the earliest close on record, unless an explicit anchor already
    # sits on that same day (short histories) or it's not actually in the past.
    d0, c0 = series[0]
    if d0 not in used_dates and d0 < today:
        anchors.append({"years_ago": "max", "date": d0.isoformat(), "close": round(c0, 4)})
    return anchors


def _parse_series(payload: dict) -> list[tuple[date, float]]:
    """Extract sorted (date, close) pairs from a Yahoo chart payload (major
    currency units: TASE agorot are divided into shekels first)."""
    result = (currency.normalize_chart_payload(payload).get("chart") or {}).get("result") or []
    if not result:
        return []
    r0 = result[0]
    # Everything below is daily-bar arithmetic; bars of another granularity
    # (Yahoo downgrades some requests to 1wk/1mo) would give wrong answers
    # rather than no answer, so they are refused.
    granularity = (r0.get("meta") or {}).get("dataGranularity")
    if granularity and granularity != "1d":
        return []
    timestamps = r0.get("timestamp") or []
    quote = (r0.get("indicators") or {}).get("quote", [{}])[0]
    closes = quote.get("close", [])
    series: list[tuple[date, float]] = []
    for ts, close in zip(timestamps, closes):
        if close is None or ts is None:
            continue
        d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        series.append((d, float(close)))
    series.sort(key=lambda x: x[0])
    return series


def parse_response(payload: dict, ticker: str, fetched_at: str,
                   today: date | None = None) -> Seasonality | None:
    if today is None:
        today = datetime.now(timezone.utc).date()
    series = _parse_series(payload)
    if not series:
        return None
    windows = compute_seasonality(series, today)
    # Require at least one window with >=2 years to be meaningful.
    usable_years = {
        e["year"] for w in windows for e in w["per_year"]
    }
    if not any(len(w["per_year"]) >= 2 for w in windows):
        return None
    return Seasonality(
        ticker=ticker.upper(),
        computed_at=fetched_at,
        as_of=f"{today.month:02d}-{today.day:02d}",
        history_years=len(usable_years),
        windows_json=json.dumps(windows),
        anchors_json=json.dumps(compute_anchors(series, today)),
    )


# ---------- fetch ----------

def fetch(tickers: list[str], range_str: str | None = None) -> list[Seasonality]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today = datetime.now(timezone.utc).date()
    rng = range_str or _DEFAULT_RANGE
    out: list[Seasonality] = []

    with httpx.Client(timeout=30.0) as client:
        for ticker in tickers:
            try:
                r = client.get(
                    _YAHOO_URL.format(ticker=ticker),
                    params=history_params(rng),
                    headers=_YAHOO_HEADERS,
                )
                r.raise_for_status()
                payload = r.json()
            except (httpx.HTTPError, ValueError):
                continue
            season = parse_response(payload, ticker, now, today)
            if season:
                out.append(season)

    return out
