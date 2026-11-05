"""The market-wide live scanner (STC-69).

Three sources, each with its own schedule and honest status:
- `universe`: index constituents from Wikipedia, monthly (STC-70).
- `scan_history`: daily bars for universe tickers the OHLC job doesn't keep,
  a batch per run, stalest first.
- `scanner`: today's session for every ticker whose exchange is in its
  regular session, in batches (STC-71), run through the built-in scans
  (STC-72). With no exchange open it defers to the next open rather than
  calling Yahoo for nothing.

The scans are written in the rule builder's schema and evaluated by the same
engine (app/rules.py) on the completed daily bars plus today's bar so far.
Volume-based scans compare today's volume *so far* with full-day averages, so
early in the session they are conservative, never projected.

Matches are shared market data. Alerts are per user, opt-in and throttled
(STC-75): one per ticker a day, at most `per_hour` an hour, and the overflow
batched into one digest at most hourly.
"""
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app import config, db, events, ingest, rules
from app.data.majors import MAJORS
from app.market_calendar import exchange_for_ticker, market_status
from app.models import Alert, IntradayQuote, OHLCBar, OHLCSeries, ScanHit
from app.sources import intraday, ohlc as ohlc_source, universe as universe_source

UNIVERSE_LABELS = {"majors": "Large caps (built in)", "mine": "Everything held or watched",
                   "sp500": "S&P 500", "ndx100": "Nasdaq-100", "ta125": "TA-125"}
HISTORY_SERIES = [("scan_daily", "1d", "2y")]
HISTORY_BARS = 300
MIN_BARS = 21
HIT_RETENTION_DAYS = 7


@dataclass(frozen=True)
class Scan:
    key: str
    label: str
    description: str
    group: rules.Group
    metric: tuple[str, int | None]
    metric_label: str


def _scan(key, label, description, left, op, right, metric, metric_label) -> Scan:
    operand = (lambda o: {"kind": "value", "value": o} if isinstance(o, (int, float))
               else {"name": o[0], "period": o[1]})
    group = rules.Group.model_validate({"conditions": [{"left": operand(left), "op": op, "right": operand(right)}]})
    return Scan(key, label, description, group, metric, metric_label)


RVOL, GAP = config.SCANNER_RVOL_MIN, config.SCANNER_GAP_PCT
SCANS = [
    _scan("unusual_volume", "Unusual volume", f"Volume so far today is at least {RVOL:g}× its 20-day average",
          ("rvol", 20), ">=", RVOL, ("rvol", 20), "RVOL"),
    _scan("gap_up", "Gap up", f"Opened at least {GAP:g}% above the previous close",
          ("gap_pct", None), ">=", GAP, ("gap_pct", None), "Gap %"),
    _scan("gap_down", "Gap down", f"Opened at least {GAP:g}% below the previous close",
          ("gap_pct", None), "<=", -GAP, ("gap_pct", None), "Gap %"),
    _scan("high_52w", "New 52-week high", "Traded above the highest high of the previous 252 sessions",
          ("high", None), ">", ("high_n", 252), ("change_pct", None), "Change %"),
    _scan("low_52w", "New 52-week low", "Traded below the lowest low of the previous 252 sessions",
          ("low", None), "<", ("low_n", 252), ("change_pct", None), "Change %"),
    _scan("breakout_20d", "20-day breakout", "Trading above the highest high of the previous 20 sessions",
          ("close", None), ">", ("high_n", 20), ("change_pct", None), "Change %"),
    _scan("rsi_overbought", "RSI overbought", "RSI(14) at or above 70", ("rsi", 14), ">=", 70, ("rsi", 14), "RSI"),
    _scan("rsi_oversold", "RSI oversold", "RSI(14) at or below 30", ("rsi", 14), "<=", 30, ("rsi", 14), "RSI"),
]
_BY_KEY = {s.key: s for s in SCANS}
_PRIORITY = {s.key: i for i, s in enumerate(SCANS)}


# ---------- universe ----------

def universe(conn) -> tuple[list[str], dict[str, int | None]]:
    """(tickers, {universe: members or None if unknown}) for the configured
    universes, de-duplicated in order, FX left out, capped."""
    seen: dict[str, None] = {}
    counts: dict[str, int | None] = {}
    for name in config.SCANNER_UNIVERSES:
        if name == "majors":
            ts = MAJORS
        elif name == "mine":
            ts = sorted(set(db.get_all_watched_tickers(conn)) | set(db.get_all_held_tickers(conn)))
        elif name in universe_source.PAGES:
            ts = [m.ticker for m in db.get_universe(conn, name)]
        else:
            counts[name] = None
            continue
        counts[name] = len(ts)
        seen.update(dict.fromkeys(t for t in ts if exchange_for_ticker(t)))
    return list(seen)[: config.SCANNER_MAX_TICKERS], counts


def universe_fetch():
    names = [n for n in config.SCANNER_UNIVERSES if n in universe_source.PAGES]
    if not names:
        return ingest.FetchResult([], note="no index universe configured (STOCKS_SCANNER_UNIVERSES)")
    return universe_source.fetch(names)


# ---------- daily history ----------

def history_fetch(conn, now: datetime | None = None):
    """Daily bars for the universe tickers that have none fresh, stalest first."""
    now = now or datetime.now(timezone.utc)
    tickers, _ = universe(conn)
    stale = (now - timedelta(hours=config.SCANNER_HISTORY_MAX_AGE_HOURS)).isoformat()
    have = db.get_latest_ohlc_fetched(conn, tickers, ("daily", "scan_daily"))
    due = sorted((t for t in tickers if have.get(t, "") < stale), key=lambda t: have.get(t, ""))
    batch = due[: config.SCANNER_HISTORY_PER_RUN]
    if not batch:
        return ingest.FetchResult([], note=f"all {len(tickers)} tickers have fresh history")
    out = [s.model_copy(update={"bars_json": json.dumps(json.loads(s.bars_json)[-HISTORY_BARS:])})
           for s in ohlc_source.fetch(batch, HISTORY_SERIES)]
    if not out:
        raise RuntimeError(f"Yahoo returned no daily bars for any of {len(batch)} tickers")
    return ingest.FetchResult(out, note=f"{len(out)} of {len(batch)} fetched; {len(due) - len(batch)} more due")


def history_store(conn, series: list[OHLCSeries]) -> None:
    db.upsert_ohlc(conn, series)
    db.prune_ohlc(conn, "scan_daily", universe(conn)[0])


# ---------- the scan ----------

def _completed_bars(conn, ticker: str, day: str) -> list[OHLCBar]:
    bars = db.get_ohlc(conn, ticker, "daily") or db.get_ohlc(conn, ticker, "scan_daily")
    return [b for b in bars if b.date < day][-HISTORY_BARS:]


def evaluate(bars: list[OHLCBar], q: IntradayQuote, at: str) -> list[ScanHit]:
    """Every scan `q` matches, given its completed daily `bars`."""
    b = rules.arrays([*bars, OHLCBar(date=q.day, open=q.open, high=max(q.high, q.price),
                                     low=min(q.low, q.price), close=q.price, volume=q.volume)])
    change = rules.value("change_pct", None, b)[-1]
    out = []
    for s in SCANS:
        if rules.evaluate(s.group, b)[-1]:
            m = rules.value(*s.metric, b)[-1]
            out.append(ScanHit(scan=s.key, ticker=q.ticker, day=q.day, price=q.price,
                               change_pct=None if math.isnan(change) else round(float(change), 2),
                               metric=None if math.isnan(m) else round(float(m), 2),
                               first_seen_at=at, last_seen_at=at))
    return out


def seconds_to_next_session(now: datetime, exchanges: set[str]) -> float:
    """How long until any of `exchanges` is next in its regular session."""
    t = now
    for _ in range(6 * 24 * 12):
        t += timedelta(minutes=5)
        if any(market_status(t, ex) == "LIVE" for ex in exchanges):
            return (t - now).total_seconds()
    return 6 * 3600


def fetch(conn, now: datetime | None = None, quote_fetch=None):
    now = now or datetime.now(timezone.utc)
    tickers, _ = universe(conn)
    exchanges = {exchange_for_ticker(t) for t in tickers}
    live = [t for t in tickers if market_status(now, exchange_for_ticker(t)) == "LIVE"]
    if not live:
        raise ingest.SourceDeferred("no exchange in the scanner universe is in its regular session",
                                    seconds_to_next_session(now, exchanges))
    quotes, failed = (quote_fetch or intraday.fetch)(live)
    at = now.isoformat(timespec="seconds")
    hits, waiting, scanned = [], 0, 0
    for q in quotes:
        if q.market_state != "LIVE":
            continue
        bars = _completed_bars(conn, q.ticker, q.day)
        if len(bars) < MIN_BARS:
            waiting += 1
            continue
        scanned += 1
        hits += evaluate(bars, q, at)
    note = f"{scanned} of {len(live)} live tickers scanned, {waiting} waiting for history, {len(hits)} matches"
    return ingest.FetchResult(hits, note=note + (f", {failed} quote batches failed" if failed else ""))


# ---------- alerts (STC-75) ----------

def best_per_ticker(hits: list[ScanHit]) -> list[tuple[ScanHit, list[str]]]:
    """One entry per ticker — its highest-priority scan, plus every scan it
    matched — strongest first."""
    by_ticker: dict[str, list[ScanHit]] = {}
    for h in hits:
        by_ticker.setdefault(h.ticker, []).append(h)
    out = []
    for hs in by_ticker.values():
        hs.sort(key=lambda h: _PRIORITY.get(h.scan, 99))
        out.append((hs[0], [h.scan for h in hs]))
    out.sort(key=lambda e: (_PRIORITY.get(e[0].scan, 99), -abs(e[0].metric or 0)))
    return out


def throttle(best: list[tuple[ScanHit, list[str]]], logged: set[str], sent_last_hour: int,
             cap: int) -> tuple[list, list]:
    """(alert now, hold for the digest): tickers already logged today are
    dropped, then at most `cap - sent_last_hour` go out individually."""
    new = [e for e in best if e[0].ticker not in logged]
    room = max(0, cap - sent_last_hour)
    return new[:room], new[room:]


def _labels(scans: list[str]) -> str:
    return ", ".join(_BY_KEY[k].label.lower() if k in _BY_KEY else k for k in scans)


def _alert_text(h: ScanHit, scans: list[str]) -> str:
    move = f" ({h.change_pct:+.2f}% today)" if h.change_pct is not None else ""
    return f"{h.ticker} at {h.price:g}{move} matched: {_labels(scans)}. A scan match, not a recommendation."


def alerts_for(conn, hits: list[ScanHit], now: datetime) -> list[Alert]:
    """Each opted-in user's throttled alerts for this run, logged."""
    if not hits:
        return []
    at, day = now.isoformat(timespec="seconds"), hits[0].day
    hour_ago = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    best = best_per_ticker(hits)
    scans_of = {h.ticker: scans for h, scans in best}
    out: list[Alert] = []
    for prefs in db.get_scanner_alert_users(conn):
        uid = prefs.user_id
        log = db.get_scanner_log(conn, uid, day)
        sent, last_digest = db.get_scanner_sends_since(conn, uid, hour_ago)
        send, hold = throttle(best, {r["ticker"] for r in log}, sent, prefs.per_hour)
        db.log_scanner_alerts(conn, [{"user_id": uid, "ticker": h.ticker, "day": day, "scan": h.scan,
                                      "state": state, "logged_at": at}
                                     for state, group in (("sent", send), ("pending", hold)) for h, _ in group])
        out += [Alert(dedup_key=f"scan|{h.ticker}|{uid}|{day}", created_at=at, ticker=h.ticker, type="scanner",
                      severity="medium", user_id=uid, title=f"Scanner: {h.ticker} — {_BY_KEY[h.scan].label}",
                      message=_alert_text(h, scans))
                for h, scans in send]
        pending = [r["ticker"] for r in log if r["state"] == "pending"] + [h.ticker for h, _ in hold]
        if pending and (last_digest is None or last_digest <= hour_ago):
            db.mark_scanner_digested(conn, uid, pending, day, at)
            out.append(Alert(
                dedup_key=f"scan-digest|{uid}|{at}", created_at=at, ticker=pending[0], type="scanner",
                severity="medium", user_id=uid, title=f"Scanner digest: {len(pending)} more matches",
                message="Held back to keep alerts to a few an hour: " + "; ".join(
                    f"{t} ({_labels(scans_of.get(t, []))})" if scans_of.get(t) else t for t in pending) + "."))
    conn.commit()
    return out


def store(conn, hits: list[ScanHit], now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    db.upsert_scan_hits(conn, hits)
    if hits:
        db.prune_scan_hits(conn, (date.fromisoformat(hits[0].day) - timedelta(days=HIT_RETENTION_DAYS)).isoformat())
    alerts = alerts_for(conn, hits, now)
    db.upsert_alerts(conn, alerts)
    events.publish("scanner", {"as_of": now.isoformat(timespec="seconds"), "matches": len(hits)})


# ---------- the API view ----------

def view(conn, user_id: int) -> dict:
    tickers, counts = universe(conn)
    refreshed = db.get_universe_refreshed(conn)
    hits = db.get_scan_hits(conn)
    as_of = max((h.last_seen_at for h in hits), default=None)
    statuses = {s.source: s.model_dump() for s in db.get_source_statuses(conn)
                if s.source in ("universe", "scan_history", "scanner")}
    with_history = db.get_latest_ohlc_fetched(conn, tickers, ("daily", "scan_daily"))
    return {
        "universes": [{"name": n, "label": UNIVERSE_LABELS.get(n, n), "members": c,
                       "refreshed_at": refreshed.get(n, (None, None))[1]} for n, c in counts.items()],
        "tickers": len(tickers), "max_tickers": config.SCANNER_MAX_TICKERS,
        "with_history": sum(t in with_history for t in tickers),
        "scans": [{"key": s.key, "label": s.label, "description": s.description,
                   "metric_label": s.metric_label} for s in SCANS],
        "day": hits[0].day if hits else None, "as_of": as_of,
        "hits": [{**h.model_dump(), "current": h.last_seen_at == as_of} for h in hits],
        "sources": statuses,
        "prefs": db.get_scanner_prefs(conn, user_id, config.SCANNER_ALERTS_PER_HOUR).model_dump(),
        "interval_seconds": config.SCANNER_INTERVAL_SECONDS,
    }
