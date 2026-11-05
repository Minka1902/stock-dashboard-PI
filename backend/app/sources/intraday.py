"""Today's session for many tickers at once (STC-71), for the scanner.

One request per batch of symbols to Yahoo's v7 quote API (crumb-authenticated,
app/sources/yahoo.py), which carries today's open, high, low, last price and
volume. The 1-minute chart API the rest of the app uses for quotes answers one
ticker per request — fine for a watchlist, not for a few hundred tickers every
few minutes.

Batches are spaced out. A 429 stops the run and raises SourceDeferred with
Yahoo's Retry-After (or a default cooldown), so the scheduler waits as asked
and the Server page shows the deferral. A batch that fails any other way is
counted, and the run's note says how many; if every batch fails, it raises.
"""
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from app import config, currency, ingest
from app.models import IntradayQuote
from app.quotes import normalize_market_state
from app.sources import yahoo

_URL = "https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols}"
_PAUSE_SECONDS = 1.0
_DEFAULT_COOLDOWN_SECONDS = 600


def _day(epoch, tz_name: str | None) -> str | None:
    if not isinstance(epoch, (int, float)):
        return None
    try:
        tz = ZoneInfo(tz_name) if tz_name else timezone.utc
    except (KeyError, ValueError):
        tz = timezone.utc
    return datetime.fromtimestamp(epoch, tz=tz).date().isoformat()


def parse_quotes(payload: dict, fetched_at: str) -> list[IntradayQuote]:
    """Quotes with a complete session so far, in major currency units. A
    symbol missing its open, high, low, price or volume is left out."""
    out = []
    for r in ((payload or {}).get("quoteResponse") or {}).get("result") or []:
        ccy = r.get("currency")
        vals = {k: currency.normalize_price(r.get(f), ccy)[0] for k, f in (
            ("price", "regularMarketPrice"), ("open", "regularMarketOpen"), ("high", "regularMarketDayHigh"),
            ("low", "regularMarketDayLow"), ("previous_close", "regularMarketPreviousClose"))}
        volume, day = r.get("regularMarketVolume"), _day(r.get("regularMarketTime"), r.get("exchangeTimezoneName"))
        if not r.get("symbol") or day is None or volume is None or any(
                not isinstance(vals[k], (int, float)) or vals[k] <= 0 for k in ("price", "open", "high", "low")):
            continue
        out.append(IntradayQuote(
            ticker=str(r["symbol"]).upper(), day=day, volume=float(volume),
            market_state=normalize_market_state(r.get("marketState")),
            currency=currency.normalize_price(None, ccy)[1], fetched_at=fetched_at, **vals))
    return out


def _retry_after(resp: httpx.Response) -> float:
    try:
        return max(60.0, float(resp.headers.get("Retry-After", "")))
    except ValueError:
        return _DEFAULT_COOLDOWN_SECONDS


def fetch(tickers: list[str], batch: int | None = None) -> tuple[list[IntradayQuote], int]:
    """(quotes, batches that failed). Raises SourceDeferred on a 429 and
    RuntimeError when no batch got through."""
    size = batch or config.SCANNER_QUOTE_BATCH
    chunks = [tickers[i:i + size] for i in range(0, len(tickers), size)]
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out: list[IntradayQuote] = []
    failed = 0
    with yahoo.client() as client:
        crumb = yahoo.get_crumb(client)
        for i, chunk in enumerate(chunks):
            if i:
                time.sleep(_PAUSE_SECONDS)
            url = _URL.format(symbols=",".join(chunk))
            r = client.get(yahoo.with_crumb(url, crumb))
            if r.status_code == 401:
                crumb = yahoo.get_crumb(client)
                r = client.get(yahoo.with_crumb(url, crumb))
            if r.status_code == 429:
                raise ingest.SourceDeferred(
                    f"Yahoo rate-limited the quote batch (429) after {i} of {len(chunks)} batches",
                    _retry_after(r))
            try:
                r.raise_for_status()
                out += parse_quotes(r.json(), now)
            except (httpx.HTTPError, ValueError):
                failed += 1
    if chunks and failed == len(chunks):
        raise RuntimeError(f"every quote batch failed ({failed} of {len(chunks)})")
    return out, failed
