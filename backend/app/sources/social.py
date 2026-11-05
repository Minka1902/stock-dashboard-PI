"""Social sentiment from ApeWisdom (Reddit / WSB mentions)."""
import time
from datetime import datetime, timezone

import httpx

from app.models import SocialSentiment

_BASE_URL = "https://apewisdom.io/api/v1.0/filter/all-reddit/page/{page}"
_PAGES = 4  # 25 results/page → up to 100 tickers
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; stock-dashboard/1.0)"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_response(payload: dict) -> dict[str, dict]:
    """Return ticker → raw-row dict from one page of ApeWisdom results."""
    results = payload.get("results", [])
    out: dict[str, dict] = {}
    for item in results:
        ticker = (item.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        rank = item.get("rank")
        rank_24h = item.get("rank_24h_ago")
        rank_change = (rank_24h - rank) if (rank is not None and rank_24h is not None) else None
        out[ticker] = {
            "mentions": item.get("mentions"),
            "upvotes": item.get("upvotes"),
            "rank": rank,
            "rank_24h_ago": rank_24h,
            "rank_change": rank_change,
        }
    return out


def fetch(tickers: list[str]) -> list[SocialSentiment]:
    fetched_at = _now_iso()
    target = {t.upper() for t in tickers}
    combined: dict[str, dict] = {}

    with httpx.Client(headers=_HEADERS, timeout=15) as client:
        for page in range(1, _PAGES + 1):
            try:
                resp = client.get(_BASE_URL.format(page=page))
                resp.raise_for_status()
                combined.update(parse_response(resp.json()))
            except Exception:
                pass
            if page < _PAGES:
                time.sleep(0.3)

    results: list[SocialSentiment] = []
    for ticker in target:
        if ticker in combined:
            row = combined[ticker]
            results.append(SocialSentiment(ticker=ticker, fetched_at=fetched_at, **row))
    return results
