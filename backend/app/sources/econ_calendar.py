"""Economic calendar — upcoming macro releases (CPI, FOMC, payrolls, …).

Two layered data paths, honoring the app principle of *real data or an explicit
error status, never fabricated values*:

* **FMP (optional key)** — Financial Modeling Prep's ``economic_calendar`` carries
  official Low/Medium/High impact ratings. Used when ``STOCKS_FMP_KEY`` is set.
* **Nasdaq (keyless, default)** — Nasdaq's ``calendar/economicevents`` endpoint has
  no impact ratings, so we classify importance from a transparent curated keyword
  list and label it as our own (``importance_source="curated"``).

Parsing helpers are pure/network-free and unit-tested directly; ``fetch`` does the
throttled HTTP and never writes to the DB.
"""
import hashlib
import html
from datetime import date, datetime, timedelta, timezone

import httpx

from app.models import EconEvent

_FMP_URL = "https://financialmodelingprep.com/api/v3/economic_calendar"
_NASDAQ_URL = "https://api.nasdaq.com/api/calendar/economicevents"
# Nasdaq blocks non-browser agents; mirror the tactic used for CNN in fear_greed.py.
_NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Signal Dashboard)",
    "Accept": "application/json, text/plain, */*",
}

# Curated importance classification for the keyless path. Matched as lowercase
# substrings against the event name. Clearly the dashboard's own read, not official.
_HIGH_IMPACT = (
    "fomc", "interest rate decision", "fed interest rate", "federal funds",
    "rate statement", "fed chair", "powell",
    "cpi", "consumer price index",
    "nonfarm payroll", "non-farm payroll", "unemployment rate",
    "gdp", "gross domestic product",
    "ppi", "producer price index",
    "pce", "personal consumption expenditures", "core pce",
    "retail sales",
    "ism manufacturing", "ism non-manufacturing", "ism services",
)
_MEDIUM_IMPACT = (
    "jobless claims", "durable goods", "consumer confidence",
    "consumer sentiment", "michigan", "housing starts", "building permits",
    "existing home sales", "new home sales", "factory orders", "trade balance",
    "industrial production", "adp", "jolts", "pmi", "empire state", "philadelphia fed",
)

# FMP reports country as a code; normalize the common ones to display names so the
# UI is consistent regardless of which path produced the row.
_FMP_COUNTRY_NAMES = {
    "US": "United States", "EU": "Euro Zone", "GB": "United Kingdom",
    "UK": "United Kingdom", "JP": "Japan", "CN": "China", "DE": "Germany",
    "FR": "France", "CA": "Canada", "AU": "Australia", "CH": "Switzerland",
    "IT": "Italy", "ES": "Spain", "IN": "India", "BR": "Brazil", "KR": "South Korea",
    "NZ": "New Zealand", "MX": "Mexico", "RU": "Russia", "ZA": "South Africa",
    "IL": "Israel",
}
# Country aliases so an allowlist of "United States" also matches FMP's "US" etc.
_COUNTRY_ALIASES = {
    "united states": {"united states", "us", "usa", "u.s.", "united states of america"},
    "euro zone": {"euro zone", "eu", "ea", "eurozone"},
    "united kingdom": {"united kingdom", "uk", "gb", "britain"},
    "israel": {"israel", "il", "isr"},
}


def _event_id(date_str: str, country: str, event: str) -> str:
    raw = f"{date_str}|{country}|{event}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def _clean_value(val) -> str | None:
    """Normalize a released/forecast/previous value to a display string or None.

    Nasdaq's feed carries HTML entities ("&nbsp;" for a not-yet-released value),
    so decode them first — otherwise the literal entity reached the UI."""
    if val is None:
        return None
    s = html.unescape(str(val)).replace(" ", " ").strip()
    if s in ("", "-", "—", "N/A", "n/a"):
        return None
    return s


def classify_importance(event_name: str) -> str:
    """Curated High/Medium/Low read for the keyless path (our own classification)."""
    name = (event_name or "").lower()
    if any(k in name for k in _HIGH_IMPACT):
        return "high"
    if any(k in name for k in _MEDIUM_IMPACT):
        return "medium"
    return "low"


def _matches_country(country: str, allowlist: list[str] | None) -> bool:
    if not allowlist:
        return True
    c = (country or "").strip().lower()
    for allowed in allowlist:
        aliases = _COUNTRY_ALIASES.get(allowed.lower(), {allowed.lower()})
        if c in aliases:
            return True
    return False


def parse_fmp(payload: list, fetched_at: str) -> list[EconEvent]:
    """Parse FMP economic_calendar rows. Impact rating is official."""
    out: list[EconEvent] = []
    for row in payload or []:
        raw_date = str(row.get("date") or "").strip()
        if not raw_date:
            continue
        # "YYYY-MM-DD HH:MM:SS" (UTC) -> date + HH:MM
        date_str, _, time_part = raw_date.partition(" ")
        time_str = time_part[:5] if len(time_part) >= 5 else ""
        event = str(row.get("event") or "").strip()
        if not event:
            continue
        code = str(row.get("country") or "").strip()
        country = _FMP_COUNTRY_NAMES.get(code, code)
        importance = str(row.get("impact") or "").strip().lower()
        if importance not in ("high", "medium", "low"):
            importance = "low"
        out.append(EconEvent(
            event_id=_event_id(date_str, country, event),
            date=date_str, time=time_str, country=country, event=event,
            importance=importance, importance_source="fmp",
            actual=_clean_value(row.get("actual")),
            forecast=_clean_value(row.get("estimate")),
            previous=_clean_value(row.get("previous")),
            source="fmp", fetched_at=fetched_at,
        ))
    return out


def parse_nasdaq(payload: dict, date_str: str, fetched_at: str) -> list[EconEvent]:
    """Parse one day's Nasdaq economicevents payload. Importance is curated."""
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = (data or {}).get("rows") or []
    out: list[EconEvent] = []
    for row in rows:
        raw_event = str(row.get("eventName") or "").strip()
        # Display text is entity-decoded; the id keeps the raw name so rows
        # stored before the decode still upsert onto themselves.
        event = html.unescape(raw_event).strip()
        if not event:
            continue
        country = str(row.get("country") or "").strip()
        gmt = str(row.get("gmt") or "").strip()
        # gmt is like "13:30"; non-time markers ("All Day", "Tentative") -> no time.
        time_str = gmt if (len(gmt) == 5 and gmt[2] == ":") else ""
        out.append(EconEvent(
            event_id=_event_id(date_str, country, raw_event),
            date=date_str, time=time_str, country=country, event=event,
            importance=classify_importance(event), importance_source="curated",
            actual=_clean_value(row.get("actual")),
            forecast=_clean_value(row.get("consensus")),
            previous=_clean_value(row.get("previous")),
            source="nasdaq", fetched_at=fetched_at,
        ))
    return out


def _fetch_fmp(start: date, end: date, fmp_key: str, fetched_at: str) -> list[EconEvent]:
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        resp = client.get(_FMP_URL, params={
            "from": start.isoformat(), "to": end.isoformat(), "apikey": fmp_key,
        })
        resp.raise_for_status()
        return parse_fmp(resp.json(), fetched_at)


def _fetch_nasdaq(start: date, end: date, fetched_at: str) -> list[EconEvent]:
    seen: set[str] = set()
    events: list[EconEvent] = []
    day = start
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        while day <= end:
            iso = day.isoformat()
            resp = client.get(_NASDAQ_URL, params={"date": iso}, headers=_NASDAQ_HEADERS)
            resp.raise_for_status()
            for ev in parse_nasdaq(resp.json(), iso, fetched_at):
                if ev.event_id not in seen:
                    seen.add(ev.event_id)
                    events.append(ev)
            day += timedelta(days=1)
    return events


def fetch(
    days_ahead: int = 7,
    days_back: int = 1,
    fmp_key: str | None = None,
    countries: list[str] | None = None,
) -> list[EconEvent]:
    """Fetch upcoming macro events. FMP path (official impact) when a key is set,
    otherwise the keyless Nasdaq path (curated impact)."""
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today = date.today()
    start = today - timedelta(days=max(0, days_back))
    end = today + timedelta(days=max(0, days_ahead))

    if fmp_key:
        events = _fetch_fmp(start, end, fmp_key, fetched_at)
    else:
        events = _fetch_nasdaq(start, end, fetched_at)

    if countries:
        events = [e for e in events if _matches_country(e.country, countries)]
    return sorted(events, key=lambda e: (e.date, e.time or "99:99"))
