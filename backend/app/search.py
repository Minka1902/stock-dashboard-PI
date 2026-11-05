"""Ticker / company-name search via Yahoo's keyless search endpoint.

Like quotes.py and chart_data.py this is on-demand (never in SOURCES):
results are fetched when the user types and TTL-cached per query.
"""
import threading
import time

import httpx

from app import currency
from app.sources.ohlc import _HEADERS

_URL = "https://query1.finance.yahoo.com/v1/finance/search"
_TIMEOUT_SECONDS = 10.0
_TTL_SECONDS = 300
_ALLOWED_TYPES = {"EQUITY", "ETF", "INDEX"}
# Upstream page size. Deliberately larger than what the palette shows, so a
# market filter still has enough rows once other exchanges are dropped (Yahoo
# ranks Tel Aviv listings below the US and European lines of the same name).
_FETCH_COUNT = 20
# Palette market filter -> the currency.market_for_symbol values it keeps.
MARKETS: dict[str, set[str] | None] = {"all": None, "us": {"US"}, "tase": {"TASE"}}


def parse_results(payload: dict, limit: int = 8) -> list[dict]:
    """Yahoo search payload -> [{symbol, name, exchange, type, market, currency}]
    (tradables only). `market`/`currency` come from the symbol convention
    (bare = US/USD, .TA = TASE/ILS); `currency` is None where it can't say."""
    out: list[dict] = []
    for q in payload.get("quotes") or []:
        symbol = (q.get("symbol") or "").strip()
        qtype = (q.get("quoteType") or "").upper()
        if not symbol or qtype not in _ALLOWED_TYPES:
            continue
        out.append({
            "symbol": symbol.upper(),
            "name": q.get("longname") or q.get("shortname") or symbol,
            "exchange": q.get("exchDisp") or q.get("exchange") or "",
            "type": qtype,
            "market": currency.market_for_symbol(symbol),
            "currency": currency.currency_for_symbol(symbol),
        })
        if len(out) >= limit:
            break
    return out


def fetch(query: str, limit: int = 8) -> list[dict]:
    with httpx.Client(timeout=_TIMEOUT_SECONDS, headers=_HEADERS) as client:
        resp = client.get(_URL, params={
            "q": query, "quotesCount": max(limit, 8),
            "newsCount": 0, "listsCount": 0,
        })
        resp.raise_for_status()
        return parse_results(resp.json(), limit)


# ---- TTL cache: normalized query -> (expires_at_monotonic, results) ----
_cache: dict[str, tuple[float, list[dict]]] = {}
_lock = threading.Lock()


def _filter(rows: list[dict], market: str, limit: int) -> list[dict]:
    keep = MARKETS[market]
    if keep is not None:
        rows = [r for r in rows if r.get("market") in keep]
    return rows[:limit]


def search(query: str, limit: int = 8, market: str = "all") -> list[dict]:
    """Cached search, optionally narrowed to one market ("all" | "us" | "tase").
    One upstream fetch per query serves every market filter."""
    if market not in MARKETS:
        raise ValueError(f"market must be one of {', '.join(MARKETS)}")
    key = query.strip().lower()
    now = time.monotonic()
    with _lock:
        entry = _cache.get(key)
        if entry is not None and entry[0] > now:
            return _filter(entry[1], market, limit)
    results = fetch(query, max(limit, _FETCH_COUNT))
    if results:  # don't cache empties: a transient failure shouldn't stick
        with _lock:
            _cache[key] = (now + _TTL_SECONDS, results)
    return _filter(results, market, limit)
