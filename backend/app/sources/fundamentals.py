"""Fundamentals + company profile per watchlist ticker, via Yahoo quoteSummary.

One request per ticker returns valuation, the company profile (name, sector,
HQ, headcount, officers) and the ownership breakdown. Everything is optional:
a field Yahoo doesn't return stays None and renders as "—" rather than being
guessed at.
"""
import json
from datetime import datetime, timezone

import httpx

from app.ingest import FetchResult
from app.models import CompanyHolder, Fundamentals
from app.sources import yahoo

_MODULES = (
    "defaultKeyStatistics,summaryProfile,summaryDetail,assetProfile,price,"
    "majorHoldersBreakdown,institutionOwnership,financialData"
)
_YF_URL = (
    "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
    f"?modules={_MODULES}"
)
_HEADERS = yahoo.HEADERS

# Officers are shown as a short "who runs it" list, not an org chart.
_MAX_OFFICERS = 6
_MAX_HOLDERS = 10


get_crumb = yahoo.get_crumb  # re-exported: tests and callers use it from here


def _raw(d: dict, key: str) -> float | None:
    v = d.get(key)
    if isinstance(v, dict):
        v = v.get("raw")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _text(*candidates) -> str | None:
    """First non-empty string among the candidates, else None."""
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()
    return None


def _officers(profile: dict) -> str:
    """Top officers as a compact JSON list. Yahoo orders them by seniority."""
    out = []
    for o in (profile.get("companyOfficers") or [])[:_MAX_OFFICERS]:
        if not isinstance(o, dict):
            continue
        name = _text(o.get("name"))
        if not name:
            continue
        age = _raw(o, "age")
        pay = _raw(o, "totalPay")
        out.append({
            "name": name,
            "title": _text(o.get("title")) or "",
            "age": int(age) if age else None,
            "pay": pay,
        })
    return json.dumps(out) if out else ""


def _currency_code(v) -> str | None:
    """A 3-letter ISO code, or None — never a guess."""
    if isinstance(v, str) and len(v.strip()) == 3 and v.strip().isalpha():
        return v.strip().upper()
    return None


def parse_response(payload: dict, ticker: str, fetched_at: str) -> "Fundamentals | None":
    try:
        result = payload["quoteSummary"]["result"]
        if not result:
            return None
        r = result[0]
    except (KeyError, TypeError, IndexError):
        return None

    # assetProfile is a superset of summaryProfile; prefer it, fall back.
    profile = r.get("assetProfile") or r.get("summaryProfile") or {}
    summary_profile = r.get("summaryProfile") or {}
    detail = r.get("summaryDetail") or {}
    stats = r.get("defaultKeyStatistics") or {}
    price = r.get("price") or {}
    holders = r.get("majorHoldersBreakdown") or {}
    financial = r.get("financialData") or {}

    employees = _raw(profile, "fullTimeEmployees")

    return Fundamentals(
        ticker=ticker.upper(),
        fetched_at=fetched_at,
        sector=_text(profile.get("sector"), summary_profile.get("sector")),
        industry=_text(profile.get("industry"), summary_profile.get("industry")),
        pe_ratio=_raw(detail, "trailingPE"),
        forward_pe=_raw(detail, "forwardPE"),
        peg_ratio=_raw(stats, "pegRatio"),
        pb_ratio=_raw(detail, "priceToBook"),
        revenue_growth=_raw(stats, "revenueGrowth"),
        profit_margin=_raw(stats, "profitMargins"),
        market_cap=_raw(detail, "marketCap"),
        name=_text(price.get("longName"), price.get("shortName")),
        website=_text(profile.get("website"), summary_profile.get("website")),
        country=_text(profile.get("country"), summary_profile.get("country")),
        city=_text(profile.get("city"), summary_profile.get("city")),
        employees=int(employees) if employees else None,
        summary=_text(profile.get("longBusinessSummary"),
                      summary_profile.get("longBusinessSummary")),
        officers_json=_officers(profile),
        insider_pct=_raw(holders, "insidersPercentHeld"),
        institution_pct=_raw(holders, "institutionsPercentHeld"),
        financial_currency=_currency_code(financial.get("financialCurrency")),
    )


def parse_holders(payload: dict, ticker: str) -> list[CompanyHolder]:
    """Top institutional holders from the institutionOwnership module."""
    try:
        result = payload["quoteSummary"]["result"]
        if not result:
            return []
        own = result[0].get("institutionOwnership") or {}
    except (KeyError, TypeError, IndexError):
        return []

    out: list[CompanyHolder] = []
    for row in (own.get("ownershipList") or [])[:_MAX_HOLDERS]:
        if not isinstance(row, dict):
            continue
        holder = _text(row.get("organization"))
        if not holder:
            continue
        reported = row.get("reportDate")
        if isinstance(reported, dict):
            reported = reported.get("fmt") or ""
        out.append(CompanyHolder(
            ticker=ticker.upper(),
            kind="institution",
            holder=holder,
            pct_held=_raw(row, "pctHeld"),
            shares=_raw(row, "position"),
            value=_raw(row, "value"),
            reported_at=reported if isinstance(reported, str) else "",
        ))
    return out


def fetch(tickers: list[str]) -> FetchResult:
    """Returns the Fundamentals records, with the institutional holders parsed
    from the same responses carried alongside on `.holders` (the store_fn in
    main.py unpacks both), so ownership costs no extra request."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results: list[Fundamentals] = []
    holders: list[CompanyHolder] = []
    failures: list[str] = []
    with yahoo.client() as client:
        crumb = yahoo.get_crumb(client)
        for ticker in tickers:
            try:
                r = client.get(yahoo.with_crumb(_YF_URL.format(ticker=ticker), crumb))
                r.raise_for_status()
                payload = r.json()
                f = parse_response(payload, ticker, now)
                if f:
                    results.append(f)
                    holders.extend(parse_holders(payload, ticker))
                else:
                    failures.append(ticker)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                failures.append(f"{ticker} ({type(exc).__name__})")

    # A run where every ticker failed is a broken source, not a successful run
    # over zero records — say so instead of reporting "ok".
    if tickers and not results:
        raise RuntimeError(f"no fundamentals returned for any of {len(tickers)} tickers")

    out = FetchResult(
        results,
        note=f"{len(failures)} ticker(s) unavailable" if failures else "",
    )
    out.holders = holders
    return out
