"""Daily equity snapshots: what each account was worth at the close, in its
owner's base currency.

Run as the `equity_snapshot` source, after the US close (the TASE close is
hours earlier, so one run covers both). An account with any position lacking
a price, or any amount lacking an FX rate, gets no snapshot that day — a
partial value would read as a drawdown. The run still stores the complete
accounts and is stamped as an error naming what was skipped.
"""
from datetime import datetime, timezone

from app import currency, db, ingest, quotes
from app.market_calendar import last_closed_session
from app.models import CashBalance, EquitySnapshot, Holding, LiveQuote


def value_account(
    positions: list[Holding],
    cash: list[CashBalance],
    flows: dict[str, float],
    quote_for,
    rate_for,
    base: str,
) -> tuple[tuple[float, float, float] | None, list[str]]:
    """((invested, cash, flow) in `base`, []) or (None, [what was missing])."""
    missing: list[str] = []
    invested = cash_base = flow_base = 0.0
    for h in positions:
        q: LiveQuote | None = quote_for(h.ticker)
        price = (q.regular_price or q.price) if q else None
        if not price:
            missing.append(f"{h.ticker}: no price")
            continue
        qccy = q.currency or h.currency
        rate = rate_for(qccy, base)
        if rate is None:
            missing.append(f"{h.ticker}: no {qccy}->{base} rate")
            continue
        invested += h.shares * price * rate
    for c in cash:
        if not c.balance:
            continue
        rate = rate_for(c.currency, base)
        if rate is None:
            missing.append(f"{c.currency} cash: no {c.currency}->{base} rate")
            continue
        cash_base += c.balance * rate
    for ccy, amount in flows.items():
        rate = rate_for(ccy, base)
        if rate is None:
            missing.append(f"{ccy} flows: no {ccy}->{base} rate")
            continue
        flow_base += amount * rate
    if missing:
        return None, missing
    return (invested, cash_base, flow_base), []


def snapshot_all(conn, now: datetime | None = None, quote_getter=None, rate_fn=None):
    """One EquitySnapshot per (user, account) with holdings or cash."""
    now = now or datetime.now(timezone.utc)
    computed_at = now.isoformat(timespec="seconds")
    day = last_closed_session(now).isoformat()
    accounts = db.get_accounts_with_holdings(conn)
    holdings = {(u, a): db.get_portfolio(conn, u, a) for u, a in accounts}
    tickers = sorted({h.ticker for hs in holdings.values() for h in hs})
    by_ticker = {q.ticker: q for q in (quote_getter or quotes.get_quotes)(tickers)} if tickers else {}
    rates: dict[tuple[str, str], float | None] = {}

    def rate_for(f: str, t: str) -> float | None:
        if (f, t) not in rates:
            rates[(f, t)] = (rate_fn or currency.fx_rate)(f, t)
        return rates[(f, t)]

    out: list[EquitySnapshot] = []
    skipped: list[str] = []
    for user_id, account in accounts:
        base = db.get_notify_profile(conn, user_id).base_currency
        prev = db.get_last_snapshot_before(conn, user_id, account, day)
        flows = db.get_flows_since(conn, user_id, account, prev.computed_at if prev else "",
                                   computed_at)
        values, missing = value_account(
            holdings[(user_id, account)], db.get_cash(conn, user_id, account), flows,
            by_ticker.get, rate_for, base)
        if values is None:
            skipped.append(f"user {user_id}/{account} ({'; '.join(missing)})")
            continue
        invested, cash_base, flow_base = values
        out.append(EquitySnapshot(
            user_id=user_id, account=account, date=day, base_currency=base,
            value_base=round(invested + cash_base, 2), cash_base=round(cash_base, 2),
            invested_base=round(invested, 2), flow_base=round(flow_base, 2),
            computed_at=computed_at,
        ))
    if skipped:
        return ingest.FetchResult(
            out, warning=f"no snapshot for {len(skipped)} account(s): {', '.join(skipped)}"[:500])
    return out
