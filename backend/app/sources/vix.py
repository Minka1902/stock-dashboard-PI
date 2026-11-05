"""VIX daily closes via the Yahoo Finance chart API — no API key required."""
from datetime import datetime, timezone

import httpx

from app.models import VixPoint
from app.sources.technical import _YAHOO_HEADERS

_YAHOO_VIX_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX"


def parse_response(payload: dict) -> list[VixPoint]:
    try:
        result = payload["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError):
        return []

    by_date: dict[str, float] = {}
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        try:
            day = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
            by_date[day] = round(float(close), 2)
        except (TypeError, ValueError, OSError):
            continue

    return [VixPoint(date=d, close=c) for d, c in sorted(by_date.items())]


def fetch(range_: str = "6mo") -> list[VixPoint]:
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(
            _YAHOO_VIX_URL,
            params={"interval": "1d", "range": range_},
            headers=_YAHOO_HEADERS,
        )
        resp.raise_for_status()
        return parse_response(resp.json())
