"""GDELT news source (geopolitical / market news).

`parse_response` is pure (no network) so it can be tested directly.
`fetch` is a thin HTTP wrapper around the GDELT DOC 2.0 API.
"""
import json
import re
import time

import httpx

from app import config
from app.ingest import SourceDeferred
from app.models import NewsArticle

API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
# GDELT throttles / blocks requests without a descriptive User-Agent.
HEADERS = {"User-Agent": "Mozilla/5.0 (Signal Dashboard; minka.scharff@gmail.com)"}

# GDELT aggressively rate-limits (HTTP 429). Once we're throttled, retrying every
# refresh just earns more 429s and burns worker time, so we back off entirely for
# a cooldown window. Monotonic clock; module-global assignment is GIL-atomic.
_cooldown_until = 0.0


class GdeltCoolingDown(SourceDeferred):
    """GDELT asked us to back off (HTTP 429), or we are still inside that
    backoff window. A deferral, not an error: ingest records it as a
    `deferred` run with this reason and retries once the cooldown ends."""


def _in_cooldown() -> bool:
    return time.monotonic() < _cooldown_until


def _cooldown_remaining() -> float:
    return max(0.0, _cooldown_until - time.monotonic())


def _begin_cooldown() -> None:
    global _cooldown_until
    _cooldown_until = time.monotonic() + config.GDELT_COOLDOWN_SECONDS

# GDELT occasionally emits raw control characters inside JSON string values,
# which strict JSON parsers reject. Strip them before parsing.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _loads_lenient(text: str) -> dict:
    try:
        return json.loads(text)
    except ValueError:
        return json.loads(_CONTROL_CHARS.sub("", text))


def _normalize_seendate(raw: str) -> str:
    # GDELT format: 20260622T124500Z -> 2026-06-22T12:45:00Z
    if raw and len(raw) >= 15 and "T" in raw:
        d, t = raw.split("T", 1)
        t = t.rstrip("Z")
        if len(d) == 8 and len(t) >= 6:
            iso = f"{d[0:4]}-{d[4:6]}-{d[6:8]}T{t[0:2]}:{t[2:4]}:{t[4:6]}Z"
            return iso
    return raw


def parse_response(payload: dict) -> list[NewsArticle]:
    articles = []
    seen = set()
    for row in payload.get("articles", []):
        url = row.get("url") or ""
        if not url or url in seen:
            continue
        seen.add(url)
        articles.append(
            NewsArticle(
                url=url,
                title=row.get("title") or "(untitled)",
                domain=row.get("domain") or "",
                seendate=_normalize_seendate(row.get("seendate") or ""),
                sourcecountry=row.get("sourcecountry") or "",
                image=row.get("socialimage") or "",
            )
        )
    return articles


def fetch(query: str, limit: int) -> list[NewsArticle]:
    """Call the live GDELT API and return normalized articles.

    Short-circuits (no network) while cooling down from a recent 429, and starts
    a cooldown on a fresh 429, so a rate-limited GDELT can't pin worker threads.
    """
    if _in_cooldown():
        raise GdeltCoolingDown(
            "GDELT rate-limited (HTTP 429); cooling down before the next request",
            retry_after_seconds=_cooldown_remaining())
    params = {
        "query": query,
        "mode": "ArtList",
        "maxrecords": limit,
        "format": "json",
        "sort": "DateDesc",
    }
    try:
        with httpx.Client(headers=HEADERS, timeout=config.GDELT_TIMEOUT_SECONDS) as client:
            resp = client.get(API_URL, params=params).raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            _begin_cooldown()
            raise GdeltCoolingDown(
                "GDELT returned HTTP 429 (rate limited)",
                retry_after_seconds=config.GDELT_COOLDOWN_SECONDS) from exc
        raise
    body = resp.text.strip()
    if not body:
        return []  # GDELT returns an empty body when it has nothing
    payload = _loads_lenient(body)
    return parse_response(payload)


# Company-name suffixes that hurt phrase matching ("Tesla, Inc." -> "Tesla").
_NAME_SUFFIX = re.compile(
    r"[,.]?\s*(incorporated|corporation|company|holdings?|group|inc|corp|co|ltd|plc|sa|nv|ag)\.?$",
    re.IGNORECASE,
)


def clean_company_name(name: str) -> str:
    prev = None
    name = name.strip().strip('"')
    while name and name != prev:
        prev = name
        name = _NAME_SUFFIX.sub("", name).strip(" ,.")
    return name


def build_ticker_query(ticker: str, company: str | None = None) -> str:
    """Precision-oriented GDELT query for one symbol.

    Bare tickers are too noisy ("F", "A"), so match the cashtag / "X stock"
    phrasing, plus the cleaned company name when one is known.
    """
    ticker = ticker.upper()
    parts = [f'"{ticker} stock"', f'"${ticker}"']
    cleaned = clean_company_name(company or "")
    if len(cleaned) >= 3:
        parts.append(f'"{cleaned}"')
    return f"({' OR '.join(parts)})"


def fetch_for_tickers(tickers: list[str], names: dict[str, str],
                      per_ticker_limit: int) -> list[NewsArticle]:
    """One query per portfolio/watchlist ticker, results tagged with it.

    Failures for a single ticker are skipped (that symbol just contributes no
    articles this cycle) so one bad query can't sink the whole news refresh.
    """
    out: list[NewsArticle] = []
    for ticker in tickers:
        if _in_cooldown():
            break  # rate-limited mid-loop; return what we gathered, don't hammer
        try:
            articles = fetch(build_ticker_query(ticker, names.get(ticker)), per_ticker_limit)
        except (httpx.HTTPError, ValueError, GdeltCoolingDown):
            continue
        for a in articles:
            a.ticker = ticker.upper()
        out.extend(articles)
    return out
