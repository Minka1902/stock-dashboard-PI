"""Put/Call ratio (5-day average) from CNN's Fear & Greed graphdata payload.

Same unofficial endpoint fear_greed.py already uses — its response carries a
put_call_options sub-series alongside the headline index.
"""
from datetime import datetime, timezone

import httpx

from app.models import PutCallPoint
from app.sources.fear_greed import _CNN_URL, _HEADERS

# Sanity bounds: the 5-day average total put/call ratio lives well inside this.
_MIN_RATIO = 0.2
_MAX_RATIO = 3.0


def parse_response(payload: dict) -> list[PutCallPoint]:
    entries = (payload.get("put_call_options") or {}).get("data") or []

    by_date: dict[str, float] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            ts_ms = float(entry["x"])
            ratio = float(entry["y"])
        except (KeyError, TypeError, ValueError):
            continue
        if not _MIN_RATIO <= ratio <= _MAX_RATIO:
            continue
        try:
            day = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()
        except (ValueError, OSError, OverflowError):
            continue
        by_date[day] = round(ratio, 3)

    return [PutCallPoint(date=d, ratio=r) for d, r in sorted(by_date.items())]


def fetch() -> list[PutCallPoint]:
    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        resp = client.get(_CNN_URL, headers=_HEADERS)
        resp.raise_for_status()
        return parse_response(resp.json())
