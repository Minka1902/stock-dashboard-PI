"""CNN Fear & Greed Index — unofficial endpoint, handle failures gracefully."""
from datetime import datetime, timezone

import httpx

from app.models import FearGreedSnapshot

_CNN_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Signal Dashboard)"}


def parse_response(payload: dict, captured_at: str) -> list[FearGreedSnapshot]:
    fg = payload.get("fear_and_greed") or {}
    score = fg.get("score")
    rating = fg.get("rating") or ""
    if score is None:
        return []
    try:
        score = float(score)
    except (TypeError, ValueError):
        return []
    return [FearGreedSnapshot(captured_at=captured_at, score=round(score, 1), rating=rating)]


def fetch() -> list[FearGreedSnapshot]:
    captured_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        resp = client.get(_CNN_URL, headers=_HEADERS)
        resp.raise_for_status()
        return parse_response(resp.json(), captured_at)
