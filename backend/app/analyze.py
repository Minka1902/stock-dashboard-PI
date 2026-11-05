"""On-demand technical analysis for ANY ticker, watched/held or not.

Portfolio tickers reuse the stored (scheduled) analysis and OHLC. Everything
else is computed live from Yahoo chart bars, TTL-cached in memory and never
persisted — stock_analysis stays "portfolio only" and no fabricated rows land
in the DB. Results are unsized; callers apply the requesting user's sizing
via analysis.apply_sizing.
"""
import json
import threading
import time

from app import analysis, chart_data, db, quotes
from app.models import OHLCBar, StockAnalysis
from app.sources import seasonality as seasonality_source

_TTL_SECONDS = 600
_ANCHOR_TTL_SECONDS = 43200  # deep history barely moves intraday
_MIN_BARS = 30


def bars_to_ohlc(bars: list[dict]) -> list[OHLCBar]:
    """chart_data bar dicts (daily+: time == 'YYYY-MM-DD') -> OHLCBar models."""
    return [
        OHLCBar(date=str(b["time"]), open=b["open"], high=b["high"],
                low=b["low"], close=b["close"], volume=b.get("volume") or 0)
        for b in bars
    ]


# ---- TTL cache: ticker -> (expires_at_monotonic, result dict) ----
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def analyze(conn, ticker: str) -> dict:
    """{analysis: StockAnalysis|None, daily: [OHLCBar], weekly: [OHLCBar], source}.

    Raises httpx errors when a live fetch fails outright (route maps to 502).
    `analysis` is None when there is not enough history to analyze honestly.
    """
    ticker = ticker.strip().upper()

    # Fast path: scheduled pipeline already covers portfolio tickers.
    stored = db.get_analysis(conn, ticker)
    if stored is not None:
        return {
            "analysis": stored,
            "daily": db.get_ohlc(conn, ticker, "daily"),
            "weekly": db.get_ohlc(conn, ticker, "weekly"),
            "source": "stored",
        }

    now = time.monotonic()
    with _lock:
        entry = _cache.get(ticker)
        if entry is not None and entry[0] > now:
            return entry[1]

    daily = bars_to_ohlc(chart_data.get_bars(ticker, "1d")["bars"])
    weekly = bars_to_ohlc(chart_data.get_bars(ticker, "1wk")["bars"])

    built: StockAnalysis | None = None
    if len(daily) >= _MIN_BARS:
        price = None
        try:
            quote = quotes.get_quotes([ticker])
            price = quote[0].price if quote else None
        except Exception:
            price = None  # fall back to last close below
        built = analysis.build(ticker, daily, price or daily[-1].close, None, None)

    result = {"analysis": built, "daily": daily, "weekly": weekly, "source": "live"}
    if daily:  # don't cache empty fetches
        with _lock:
            _cache[ticker] = (time.monotonic() + _TTL_SECONDS, result)
    return result


# ---- "this day N years ago" anchors ----
_anchor_cache: dict[str, tuple[float, list[dict]]] = {}
_anchor_lock = threading.Lock()


def get_anchors(conn, ticker: str) -> list[dict]:
    """Seasonality anchors ({years_ago, date, close}) for any ticker.

    Watched tickers read the scheduled seasonality row; anything else costs
    one Yahoo range=max fetch, cached 12h. Returns [] when no data exists —
    never fabricated values.
    """
    ticker = ticker.strip().upper()
    stored = db.get_seasonality_for(conn, ticker)
    if stored is not None:
        try:
            return json.loads(stored.anchors_json)
        except (json.JSONDecodeError, TypeError):
            return []

    now = time.monotonic()
    with _anchor_lock:
        entry = _anchor_cache.get(ticker)
        if entry is not None and entry[0] > now:
            return entry[1]

    # seasonality.fetch never raises; it returns [] on any upstream failure.
    rows = seasonality_source.fetch([ticker])
    anchors: list[dict] = []
    if rows:
        try:
            anchors = json.loads(rows[0].anchors_json)
        except (json.JSONDecodeError, TypeError):
            anchors = []
    if anchors:  # don't cache empties
        with _anchor_lock:
            _anchor_cache[ticker] = (time.monotonic() + _ANCHOR_TTL_SECONDS, anchors)
    return anchors
