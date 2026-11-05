"""AAII Investor Sentiment Survey — layered fetch, never fabricated.

Tier 1: the public results page (weekly bull/neutral/bear percentages parsed
defensively out of the page text). Tier 2: AAII's long-standing historical
spreadsheet (sentiment.xls), which also backfills past weeks. Whichever tier
succeeds is recorded in the source status ("ok (fallback: sentiment.xls)");
if every tier fails the combined errors surface via the source-status UI.
"""
import re
from datetime import datetime, timezone

import httpx

from app.ingest import FetchResult
from app.models import AaiiSentiment

_URL = "https://www.aaii.com/sentimentsurvey/sent_results"
_XLS_URL = "https://www.aaii.com/files/surveys/sentiment.xls"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

_PCT_PATTERNS = {
    "bullish": re.compile(r"Bullish\D{0,40}?([\d.]+)\s*%", re.IGNORECASE),
    "neutral": re.compile(r"Neutral\D{0,40}?([\d.]+)\s*%", re.IGNORECASE),
    "bearish": re.compile(r"Bearish\D{0,40}?([\d.]+)\s*%", re.IGNORECASE),
}
_DATE_US = re.compile(r"Week [Ee]nding\D{0,20}?(\d{1,2}/\d{1,2}/\d{2,4})")
_DATE_LONG = re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4})")


def _to_iso(raw: str) -> str | None:
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_response(html: str, fetched_at: str) -> list[AaiiSentiment]:
    text = re.sub(r"<[^>]+>", " ", html)

    pcts: dict[str, float] = {}
    for key, pattern in _PCT_PATTERNS.items():
        m = pattern.search(text)
        if not m:
            return []
        try:
            value = float(m.group(1))
        except ValueError:
            return []
        if not 0 <= value <= 100:
            return []
        pcts[key] = value

    if not 95 <= sum(pcts.values()) <= 105:
        return []

    week_ending = None
    m = _DATE_US.search(text) or _DATE_LONG.search(text)
    if m:
        week_ending = _to_iso(m.group(1))
    if week_ending is None:
        # Weekly upsert key still converges on repeated fetches within a week.
        week_ending = fetched_at[:10]

    return [
        AaiiSentiment(
            week_ending=week_ending,
            bullish=pcts["bullish"],
            neutral=pcts["neutral"],
            bearish=pcts["bearish"],
            fetched_at=fetched_at,
        )
    ]


def rows_to_sentiments(rows: list[tuple[str | None, list[float]]], fetched_at: str,
                       limit: int = 26) -> list[AaiiSentiment]:
    """Spreadsheet rows -> sentiments. Each row is (date_iso|None, numeric cells).

    The first three numeric cells must be bull/neutral/bear either as fractions
    (0–1 summing to ~1, the sentiment.xls format) or percentages summing to
    ~100. Anything else is skipped — never guessed.
    """
    out: list[AaiiSentiment] = []
    for date_iso, vals in rows:
        if not date_iso or len(vals) < 3:
            continue
        bull, neut, bear = vals[0], vals[1], vals[2]
        total = bull + neut + bear
        if all(0 <= v <= 1.0 for v in (bull, neut, bear)) and 0.95 <= total <= 1.05:
            bull, neut, bear = bull * 100, neut * 100, bear * 100
        elif all(0 <= v <= 100 for v in (bull, neut, bear)) and 95 <= total <= 105:
            pass
        else:
            continue
        out.append(AaiiSentiment(
            week_ending=date_iso,
            bullish=round(bull, 2), neutral=round(neut, 2), bearish=round(bear, 2),
            fetched_at=fetched_at,
        ))
    out.sort(key=lambda s: s.week_ending)
    return out[-limit:]


def parse_xls(content: bytes, fetched_at: str, limit: int = 26) -> list[AaiiSentiment]:
    """Parse the historical sentiment.xls workbook (thin xlrd shell around
    rows_to_sentiments, which holds all the validation logic)."""
    import xlrd  # .xls (BIFF) only — exactly what AAII publishes

    book = xlrd.open_workbook(file_contents=content)
    sheet = book.sheet_by_index(0)
    rows: list[tuple[str | None, list[float]]] = []
    for r in range(sheet.nrows):
        date_iso: str | None = None
        vals: list[float] = []
        for cell in sheet.row(r):
            if cell.ctype == xlrd.XL_CELL_DATE and date_iso is None:
                try:
                    dt = xlrd.xldate.xldate_as_datetime(cell.value, book.datemode)
                    date_iso = dt.date().isoformat()
                except Exception:
                    continue
            elif cell.ctype == xlrd.XL_CELL_NUMBER:
                vals.append(float(cell.value))
            elif cell.ctype == xlrd.XL_CELL_TEXT and date_iso is None:
                iso = _to_iso(str(cell.value).strip())
                if iso:
                    date_iso = iso
        rows.append((date_iso, vals))
    return rows_to_sentiments(rows, fetched_at, limit=limit)


def fetch() -> list[AaiiSentiment]:
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    errors: list[str] = []
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        # Tier 1: the current results page.
        try:
            resp = client.get(_URL, headers=_HEADERS)
            resp.raise_for_status()
            results = parse_response(resp.text, fetched_at)
            if results:
                return results
            errors.append("page: no percentages found")
        except httpx.HTTPError as exc:
            errors.append(f"page: {exc}")
        # Tier 2: the historical spreadsheet (also backfills prior weeks).
        try:
            resp = client.get(_XLS_URL, headers=_HEADERS)
            resp.raise_for_status()
            results = parse_xls(resp.content, fetched_at)
            if results:
                return FetchResult(results, note="fallback: sentiment.xls")
            errors.append("xls: no valid rows")
        except Exception as exc:
            errors.append(f"xls: {exc}")
    raise RuntimeError("; ".join(errors))
