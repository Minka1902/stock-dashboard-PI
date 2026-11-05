"""Short interest data from Yahoo Finance quoteSummary."""
from datetime import datetime, timezone

from app.models import ShortInterest
from app.sources import yahoo

_YF_URL = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}?modules=defaultKeyStatistics"
_HEADERS = yahoo.HEADERS


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_response(payload: dict, ticker: str, fetched_at: str) -> ShortInterest | None:
    try:
        stats = payload["quoteSummary"]["result"][0]["defaultKeyStatistics"]
    except (KeyError, IndexError, TypeError):
        return None

    def _raw(key: str):
        entry = stats.get(key)
        if isinstance(entry, dict):
            return entry.get("raw")
        return None

    short_pct_float = _raw("shortPercentOfFloat")
    shares_short = _raw("sharesShort")
    days_to_cover = _raw("shortRatio")
    prior_month_shares = _raw("sharesShortPriorMonth")

    squeeze_flag = short_pct_float is not None and short_pct_float > 0.15

    return ShortInterest(
        ticker=ticker,
        fetched_at=fetched_at,
        shares_short=int(shares_short) if shares_short is not None else None,
        short_pct_float=short_pct_float,
        days_to_cover=days_to_cover,
        prior_month_shares=int(prior_month_shares) if prior_month_shares is not None else None,
        squeeze_flag=squeeze_flag,
    )


def fetch(tickers: list[str]) -> list[ShortInterest]:
    fetched_at = _now_iso()
    results: list[ShortInterest] = []
    with yahoo.client(timeout=15) as client:
        crumb = yahoo.get_crumb(client)
        for ticker in tickers:
            try:
                resp = client.get(yahoo.with_crumb(_YF_URL.format(ticker=ticker), crumb))
                resp.raise_for_status()
                record = parse_response(resp.json(), ticker, fetched_at)
                if record is not None:
                    results.append(record)
            except Exception:
                continue
    # Every ticker failing is a broken source, not a successful empty run.
    if tickers and not results:
        raise RuntimeError(f"no short interest returned for any of {len(tickers)} tickers")
    return results
