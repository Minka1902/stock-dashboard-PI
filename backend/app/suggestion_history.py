"""Suggestion history: what we said about a stock, and what it did next.

Scope is deliberately narrow (watchlisted and held tickers only) and the stored
record is deliberately lean — ticker, day, kind, action, reference price. The
"what happened next" side is *derived* from the stored daily bars every time it
is read, so it is always current and can never drift from the price data.

History accrues from the day this ships. There is no backfill: reconstructing
suggestions we never made would be inventing them.
"""
import json
from datetime import date, datetime, timedelta, timezone

from app import costs, db, model_version
from app.models import SuggestionHistoryEntry

# Horizons reported for every entry, in calendar days.
HORIZONS = (7, 30)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _reference_price(conn, ticker: str, fallback: float | None) -> float | None:
    """Price to measure the outcome from: the technical signal's price when we
    have one, else the most recent stored close."""
    if fallback is not None:
        return fallback
    bars = db.get_ohlc(conn, ticker, "daily")
    return bars[-1].close if bars else None


def collect(conn, digest: dict, user_id: int) -> list[SuggestionHistoryEntry]:
    """Turn one digest into history rows — holdings and watchlist names only.

    `new_ideas` are excluded on purpose: they aren't yours yet, so tracking
    their outcome would be scoring the market, not your book.
    """
    for_date = digest["for_date"]
    now = _now_iso()
    muted = json.dumps(digest.get("muted", []))
    rows: list[SuggestionHistoryEntry] = []
    seen: set[str] = set()

    def add(ticker: str, kind: str, action: str, price: float | None, version: str | None = None):
        if not ticker or ticker in seen:
            return
        seen.add(ticker)
        rows.append(SuggestionHistoryEntry(
            user_id=user_id, ticker=ticker, for_date=for_date, kind=kind,
            action=action, price=_reference_price(conn, ticker, price),
            created_at=now, model_version=version or model_version.current(), muted=muted,
        ))

    for a in digest.get("holdings_alerts", []):
        add(a["ticker"], "holding", a.get("action") or "", a.get("price"))

    for o in digest.get("opportunities", []):
        if o.get("ta_pending"):
            action = f"Boom {o.get('score')} (TA pending)"
        else:
            action = (o.get("recommendation") or "hold").upper()
            if o.get("conviction") is not None:
                action += f" · conv {o['conviction']}"
        add(o["ticker"], "watchlist", action, o.get("entry"))

    # A saved rule's signal is tracked under that rule's own version, so its
    # record never mixes with the app's model or with another rule.
    for r in digest.get("rule_signals", []):
        if r["kind"] == "entry":
            add(r["ticker"], "rule", f"RULE {r['rule']} · entry", None, r["model_version"])

    return rows


def record(conn, digest: dict, user_id: int) -> int:
    """Persist one digest's suggestions. Safe to call repeatedly — the first
    write for a (user, ticker, day) wins."""
    return db.record_suggestion_history(conn, collect(conn, digest, user_id))


def _closes_by_date(conn, ticker: str) -> list[tuple[str, float]]:
    return [(b.date, b.close) for b in db.get_ohlc(conn, ticker, "daily") if b.close is not None]


def _close_on_or_after(closes: list[tuple[str, float]], target: str) -> tuple[str, float] | None:
    """First close on or after `target` — markets are shut on plenty of days,
    so an exact date match would drop most horizons."""
    for when, close in closes:
        if when >= target:
            return when, close
    return None


def _pct(from_price: float | None, to_price: float | None) -> float | None:
    if not from_price or to_price is None:
        return None
    return round((to_price / from_price - 1) * 100, 1)


def _net_pct(from_price: float | None, to_price: float | None) -> float | None:
    """The same move after a round trip's costs (costs.round_trip)."""
    if not from_price or to_price is None:
        return None
    return round(costs.round_trip(from_price, to_price), 1)


def with_outcomes(conn, entries: list[SuggestionHistoryEntry]) -> list[dict]:
    """Attach the realised move at each horizon, plus the move to date —
    gross in `outcomes`, after a round trip's costs in `outcomes_net`.

    A horizon that hasn't elapsed yet — or has no bar to measure against —
    reports None, which the UI renders as "pending" rather than as zero.
    """
    by_ticker: dict[str, list[tuple[str, float]]] = {}
    out: list[dict] = []
    today = date.today().isoformat()

    for e in entries:
        closes = by_ticker.get(e.ticker)
        if closes is None:
            closes = _closes_by_date(conn, e.ticker)
            by_ticker[e.ticker] = closes

        row = e.model_dump()
        row["outcomes"] = {}
        row["outcomes_net"] = {}
        for days in HORIZONS:
            key = f"d{days}"
            row["outcomes"][key] = row["outcomes_net"][key] = None
            try:
                target = (date.fromisoformat(e.for_date) + timedelta(days=days)).isoformat()
            except ValueError:
                continue
            if target > today:
                continue  # not elapsed yet
            hit = _close_on_or_after(closes, target)
            exit_price = hit[1] if hit else None
            row["outcomes"][key] = _pct(e.price, exit_price)
            row["outcomes_net"][key] = _net_pct(e.price, exit_price)

        row["outcomes"]["since"] = _pct(e.price, closes[-1][1] if closes else None)
        row["last_close"] = closes[-1][1] if closes else None
        out.append(row)
    return out
