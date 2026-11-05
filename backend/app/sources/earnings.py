"""Company earnings dates and past results, from Yahoo Finance quoteSummary.

Deliberately a separate source from `analyst`, which already reads one forward
earnings date. analyst_signals is one row per ticker and structurally cannot
hold one row per (ticker, event) — and the question "who reports, and when"
needs the calendar, plus the past quarters to judge whether a print usually
lands well.

The important discipline here is `is_estimate`. Yahoo marks forward dates that
are its own projection rather than a company-confirmed date, and those two are
not the same claim. The flag is persisted and rendered differently instead of
being flattened into a date that looks certain.
"""
from datetime import datetime, timezone

from app.ingest import FetchResult
from app.models import EarningsEvent
from app.sources import yahoo

_YF_URL = (
    "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
    "?modules=calendarEvents,earnings,earningsHistory"
)
_TIMEOUT = 12.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_date(ts) -> str | None:
    try:
        return datetime.fromtimestamp(int(ts), timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _timing(ts) -> str:
    """Before/after the bell, inferred from the timestamp's US-Eastern hour.

    Yahoo doesn't label this, and it changes how a print is traded, so it's
    worth deriving — but only when there's a real intraday time to read. A
    date-only entry (midnight UTC) returns "" rather than guessing.
    """
    try:
        dt = datetime.fromtimestamp(int(ts), timezone.utc)
    except (TypeError, ValueError, OSError):
        return ""
    if dt.hour == 0 and dt.minute == 0:
        return ""
    # US market hours are 13:30-20:00 UTC (14:30-21:00 during winter EST).
    if dt.hour < 13:
        return "bmo"    # before market open
    if dt.hour >= 20:
        return "amc"    # after market close
    return "intraday"


def _num(node):
    """Yahoo wraps numbers as {raw, fmt}; take raw, tolerate a bare value."""
    if isinstance(node, dict):
        node = node.get("raw")
    try:
        return float(node)
    except (TypeError, ValueError):
        return None


def parse_response(payload: dict, ticker: str, fetched_at: str) -> list[EarningsEvent]:
    """quoteSummary payload -> earnings events (forward + up to 4 past quarters)."""
    try:
        result = payload["quoteSummary"]["result"][0]
    except (KeyError, IndexError, TypeError):
        return []

    events: dict[str, EarningsEvent] = {}

    # --- forward dates ---
    try:
        cal = result["calendarEvents"]["earnings"]
        # Yahoo emits a range when the date isn't pinned down; both entries are
        # the same (estimated) event, so only the first is taken.
        estimated = bool(cal.get("isEarningsDateEstimate", False))
        for entry in (cal.get("earningsDate") or [])[:1]:
            raw = entry.get("raw") if isinstance(entry, dict) else entry
            date = _to_date(raw)
            if not date:
                continue
            events[date] = EarningsEvent(
                ticker=ticker, event_date=date, is_estimate=estimated,
                timing=_timing(raw),
                eps_estimate=_num(cal.get("earningsAverage")),
                revenue_estimate=_num(cal.get("revenueAverage")),
                eps_actual=None, surprise_pct=None, quarter="",
                source="yahoo", fetched_at=fetched_at,
            )
    except (KeyError, TypeError):
        pass

    # --- past quarters (actual vs estimate) ---
    try:
        for row in (result["earningsHistory"]["history"] or []):
            date = _to_date((row.get("quarter") or {}).get("raw"))
            if not date:
                continue
            actual = _num(row.get("epsActual"))
            estimate = _num(row.get("epsEstimate"))
            events[date] = EarningsEvent(
                ticker=ticker, event_date=date,
                is_estimate=False,          # it already happened
                timing="",
                eps_estimate=estimate,
                eps_actual=actual,
                surprise_pct=_num(row.get("surprisePercent")),
                revenue_estimate=None,
                quarter=row.get("period") or "",
                source="yahoo", fetched_at=fetched_at,
            )
    except (KeyError, TypeError):
        pass

    return sorted(events.values(), key=lambda e: e.event_date)


def fetch(tickers: list[str]) -> list[EarningsEvent]:
    """One quoteSummary call per ticker, sequentially and bounded by the caller.

    Partial results are returned rather than raising: one delisted or throttled
    symbol shouldn't cost the whole calendar. If *nothing* comes back and there
    were errors, it raises so the source is stamped as failed rather than
    reporting a successful run over zero rows.
    """
    if not tickers:
        return []
    fetched_at = _now_iso()
    out: list[EarningsEvent] = []
    errors: list[str] = []

    with yahoo.client(timeout=_TIMEOUT) as client:
        try:
            crumb = yahoo.get_crumb(client)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"yahoo crumb: {exc}") from exc

        for ticker in tickers:
            try:
                # with_crumb, not params=: httpx's params REPLACES the query
                # string, which would drop `modules=` and make every call a 400.
                resp = client.get(yahoo.with_crumb(_YF_URL.format(ticker=ticker), crumb))
                resp.raise_for_status()
                out.extend(parse_response(resp.json(), ticker, fetched_at))
            except Exception as exc:  # noqa: BLE001
                # Carry the status code: "HTTPStatusError" alone doesn't say
                # whether it's a bad request, a 404 or a rate limit.
                status = getattr(getattr(exc, "response", None), "status_code", None)
                errors.append(f"{ticker}: {type(exc).__name__}"
                              + (f" {status}" if status else ""))

    if not out and errors:
        raise RuntimeError("; ".join(errors[:5]))
    if errors:
        # Real data, but incomplete — say which symbols are missing rather than
        # letting the calendar look complete.
        return FetchResult(out, note=f"{len(errors)} ticker(s) failed: {', '.join(errors[:3])}")
    return out
