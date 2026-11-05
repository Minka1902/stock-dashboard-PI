"""On-demand OHLCV bars for the pro chart (Yahoo chart API, TTL-cached).

Like app/quotes.py this is never in the SOURCES registry: bars are fetched
when a chart asks for them and cached in memory — 30s for intraday intervals
(so an open chart follows the live tape), an hour for daily and up. Intraday
bars keep their epoch timestamps; daily+ bars collapse to dates, matching what
lightweight-charts expects for each resolution.

Intraday bars also carry a `session` tag ("pre" | "regular" | "post") taken
from Yahoo's own trading-period metadata for that exchange — never from a
hardcoded clock — so the chart can shade extended hours honestly, and a market
with no extended session (TASE) simply has none.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx

from app import currency
from app.sources.ohlc import _HEADERS, _URL

# interval -> (yahoo interval, yahoo range, cache ttl seconds, intraday?)
INTERVALS: dict[str, tuple[str, str, int, bool]] = {
    "1m":  ("1m",  "1d",  30,   True),
    "5m":  ("5m",  "5d",  30,   True),
    "15m": ("15m", "1mo", 60,   True),
    "1h":  ("60m", "3mo", 120,  True),
    "1d":  ("1d",  "2y",  3600, False),
    "1wk": ("1wk", "10y", 3600, False),
    "1mo": ("1mo", "max", 3600, False),
}

_TIMEOUT_SECONDS = 15.0


_SESSIONS = ("regular", "pre", "post")


def _periods(meta: dict) -> dict[str, list[tuple[int, int]]]:
    """Yahoo's trading periods as {session: [(start, end), ...]} epoch spans.

    `tradingPeriods` is a dict of per-session day lists when the request asked
    for extended hours, and a bare list (regular session only) otherwise;
    `currentTradingPeriod` always has all three for the latest day. Zero-length
    spans (how TASE reports "no pre/post") are dropped.
    """
    out: dict[str, list[tuple[int, int]]] = {k: [] for k in _SESSIONS}

    def add(kind: str, span) -> None:
        if not isinstance(span, dict):
            return
        start, end = span.get("start"), span.get("end")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
            out[kind].append((int(start), int(end)))

    tp = meta.get("tradingPeriods")
    if isinstance(tp, dict):
        for kind in _SESSIONS:
            for day in tp.get(kind) or []:
                for span in (day if isinstance(day, list) else [day]):
                    add(kind, span)
    elif isinstance(tp, list):
        for day in tp:
            for span in (day if isinstance(day, list) else [day]):
                add("regular", span)
    for kind, span in (meta.get("currentTradingPeriod") or {}).items():
        if kind in out:
            add(kind, span)
    return out


def parse_session_info(payload: dict) -> dict:
    """Session metadata for the chart response: whether this listing has an
    extended session at all, its exchange timezone, and the regular hours as
    minutes after local midnight (from the latest trading period)."""
    result = (payload.get("chart") or {}).get("result") or []
    meta = (result[0].get("meta") or {}) if result else {}
    periods = _periods(meta)
    has_extended = bool(meta.get("hasPrePostMarketData")) and bool(periods["pre"] or periods["post"])
    regular = None
    cur = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    start, end, off = cur.get("start"), cur.get("end"), cur.get("gmtoffset")
    if all(isinstance(v, (int, float)) for v in (start, end, off)) and end > start:
        regular = {
            "start_min": int(((start + off) % 86400) // 60),
            "end_min": int(((end + off) % 86400) // 60),
        }
    return {
        "has_extended": has_extended,
        "timezone": meta.get("exchangeTimezoneName"),
        "regular": regular,
    }


def classify_session(ts: int, periods: dict[str, list[tuple[int, int]]],
                     gmtoffset: int | None = None,
                     current: dict | None = None) -> str | None:
    """"pre" | "regular" | "post" for a bar starting at `ts`, or None when the
    metadata can't place it. Explicit spans win; failing that, the latest
    period's time-of-day window is projected onto the bar's day (the same
    exchange hours, from Yahoo, not a hardcoded clock)."""
    for kind in _SESSIONS:
        for start, end in periods[kind]:
            if start <= ts < end:
                return kind
    if gmtoffset is None or not current:
        return None
    tod = (ts + gmtoffset) % 86400
    for kind in _SESSIONS:
        span = current.get(kind) or {}
        start, end = span.get("start"), span.get("end")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end <= start:
            continue
        s_tod, e_tod = (start + gmtoffset) % 86400, (end + gmtoffset) % 86400
        if s_tod <= tod < e_tod:
            return kind
    return None


def parse_chart_bars(payload: dict, intraday: bool) -> list[dict]:
    """Yahoo chart payload -> [{time, open, high, low, close, volume}].

    `time` is epoch seconds for intraday resolutions and "YYYY-MM-DD" for
    daily+ (the two time formats lightweight-charts accepts). Bars with any
    missing OHLC value are dropped, never interpolated. Intraday bars get a
    `session` tag when the payload's trading periods can place them.
    """
    # Prices in the major unit: Yahoo's TASE ILA (agorot) become ILS here.
    result = (currency.normalize_chart_payload(payload).get("chart") or {}).get("result") or []
    if not result:
        return []
    r = result[0]
    ts = r.get("timestamp") or []
    q = (r.get("indicators") or {}).get("quote", [{}])[0]
    o, h, l, c, v = (q.get(k) or [] for k in ("open", "high", "low", "close", "volume"))
    meta = r.get("meta") or {}
    periods = _periods(meta) if intraday else None
    gmtoffset = meta.get("gmtoffset") if isinstance(meta.get("gmtoffset"), (int, float)) else None
    bars: list[dict] = []
    for i, t in enumerate(ts):
        oo, hh, ll, cc = (arr[i] if i < len(arr) else None for arr in (o, h, l, c))
        if None in (oo, hh, ll, cc):
            continue
        vol = v[i] if i < len(v) and v[i] is not None else 0
        bars.append({
            "time": int(t) if intraday
            else datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat(),
            "open": round(float(oo), 4), "high": round(float(hh), 4),
            "low": round(float(ll), 4), "close": round(float(cc), 4),
            "volume": float(vol),
        })
        if intraday:
            session = classify_session(int(t), periods, gmtoffset,
                                       meta.get("currentTradingPeriod"))
            if session:
                bars[-1]["session"] = session
    if not intraday:
        # Daily+ payloads can repeat the live bar's date; keep the last one.
        dedup: dict[str, dict] = {}
        for b in bars:
            dedup[b["time"]] = b
        bars = sorted(dedup.values(), key=lambda b: b["time"])
    return bars


def fetch_chart(ticker: str, interval: str, prepost: bool = False) -> dict:
    """{"bars": [...], "session": {...}} from one Yahoo chart request."""
    yahoo_interval, yahoo_range, _, intraday = INTERVALS[interval]
    # Extended-hours bars only exist for intraday resolutions.
    include_prepost = "true" if (prepost and intraday) else "false"
    with httpx.Client(timeout=_TIMEOUT_SECONDS, headers=_HEADERS, follow_redirects=True) as client:
        resp = client.get(
            _URL.format(ticker=ticker),
            params={"interval": yahoo_interval, "range": yahoo_range,
                    "includePrePost": include_prepost},
        )
        resp.raise_for_status()
        payload = resp.json()
        bars = parse_chart_bars(payload, intraday)  # normalizes `payload` in place
        return {"bars": bars, "session": parse_session_info(payload),
                "currency": chart_currency(payload, ticker)}


def chart_currency(payload: dict, ticker: str) -> str | None:
    """Major-unit currency of a chart payload, falling back to the symbol's
    convention (bare -> USD, .TA -> ILS) when Yahoo's meta omits it."""
    result = (payload.get("chart") or {}).get("result") or []
    meta = (result[0].get("meta") or {}) if result else {}
    return currency.quote_currency(meta) or currency.currency_for_symbol(ticker)


def fetch_bars(ticker: str, interval: str, prepost: bool = False) -> list[dict]:
    return fetch_chart(ticker, interval, prepost)["bars"]


# ---- TTL cache: (ticker, interval, prepost) -> (expires_at_monotonic, payload) ----
_cache: dict[tuple[str, str, bool], tuple[float, dict]] = {}
_lock = threading.Lock()


def get_bars(ticker: str, interval: str, prepost: bool = False) -> dict:
    """Cached bars payload: {ticker, interval, as_of, bars, session}. Raises KeyError
    for an unknown interval and httpx errors for a failed (uncached) fetch.
    `prepost` includes extended-hours bars (intraday intervals only)."""
    ticker = ticker.strip().upper()
    key = (ticker, interval, prepost)
    ttl = INTERVALS[interval][2]
    now = time.monotonic()

    with _lock:
        entry = _cache.get(key)
        if entry is not None and entry[0] > now:
            return entry[1]

    fetched = fetch_chart(ticker, interval, prepost)
    bars = fetched["bars"]
    payload = {
        "ticker": ticker,
        "interval": interval,
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bars": bars,
        "session": fetched.get("session"),
        "currency": fetched.get("currency") or currency.currency_for_symbol(ticker),
        "market": currency.market_for_symbol(ticker),
    }
    # Cache only non-empty results so a transient upstream failure doesn't
    # blank the chart for a whole TTL window.
    if bars:
        with _lock:
            _cache[key] = (time.monotonic() + ttl, payload)
    return payload


# ---- extended hours for the daily+ chart: latest pre / after-hours print ----
def _as_point(bar: dict | None) -> dict | None:
    return {"price": bar["close"], "time": bar["time"]} if bar else None


def summarize_extended(bars: list[dict]) -> dict:
    """The current pre-market or after-hours print from session-tagged
    intraday bars (one trading day, oldest first).

    Only the extended print that is *current* is reported — i.e. when the
    newest tagged bar is itself a pre-market or after-hours bar, its close is
    that session's latest print. During the regular session both are None
    — the live price already is the regular price. Never interpolated: a
    session with no bars reports None.
    """
    tagged = [b for b in bars if b.get("session") in ("pre", "regular", "post")]
    latest = tagged[-1] if tagged else None
    regular = next((b for b in reversed(tagged) if b["session"] == "regular"), None)
    kind = latest["session"] if latest else None
    return {
        "pre": _as_point(latest) if kind == "pre" else None,
        "post": _as_point(latest) if kind == "post" else None,
        "regular_close": regular["close"] if regular else None,
    }


def get_extended(ticker: str) -> dict:
    """{ticker, supported, pre, post, regular_close, as_of} for the D/W/M
    chart's "Pre" / "After" price lines, from the cached 1m extended-hours
    bars. `supported` is False for listings with no extended session."""
    data = get_bars(ticker, "1m", prepost=True)
    session = data.get("session") or {}
    supported = bool(session.get("has_extended"))
    summary = summarize_extended(data["bars"]) if supported else \
        {"pre": None, "post": None, "regular_close": None}
    return {"ticker": data["ticker"], "supported": supported, "as_of": data["as_of"], **summary}


# ---- sparklines: tiny close-only series for the watchlist/portfolio tables ----
# range key -> (reused bar interval, number of trailing bars to keep)
SPARK_RANGES: dict[str, tuple[str, int]] = {
    "1d": ("5m", 78),    # ~1 trading session of 5-minute bars
    "3d": ("15m", 78),   # ~3 sessions of 15-minute bars
    "1w": ("1h", 40),    # ~1 week of hourly bars
    "1m": ("1d", 22),    # ~1 month of daily bars
}


def get_sparkline(ticker: str, rng: str) -> dict:
    """Trailing close-only series for `rng` ("1d"/"3d"/"1w"/"1m"), reusing the
    TTL-cached bar fetch. Never fabricates — an empty series means no data."""
    interval, tail = SPARK_RANGES[rng]
    bars = get_bars(ticker, interval)["bars"][-tail:]
    closes = [b["close"] for b in bars]
    change_pct = None
    if len(closes) >= 2 and closes[0]:
        change_pct = round((closes[-1] - closes[0]) / closes[0] * 100, 2)
    return {"closes": closes, "change_pct": change_pct}


def get_sparklines(tickers: list[str], rng: str) -> dict:
    """Trailing series for many tickers, fetched concurrently. A per-ticker
    failure surfaces as an `error` flag rather than blanking the whole batch."""
    if not tickers:
        return {}

    def one(t: str) -> tuple[str, dict]:
        try:
            return t, get_sparkline(t, rng)
        except Exception:
            return t, {"closes": [], "change_pct": None, "error": True}

    with ThreadPoolExecutor(max_workers=min(6, len(tickers))) as ex:
        return dict(ex.map(one, tickers))
