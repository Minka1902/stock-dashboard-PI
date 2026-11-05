"""Exit rules on holdings (STC-9): stop, take-profit and trailing stop.

Rules alert; they never sell. The evaluator runs every minute and looks at a
ticker only while that ticker's exchange is in its regular session, on a quote
that is itself live and fresh (quotes.tradable_price). Each kind fires once
per version of a rule — saving the rule re-arms it — and the alert quotes the
price that crossed the level: after a gap that is below the stop, not the stop.

A trailing stop's high (trail_high) is the highest live price seen since the
trail was set, and only ever rises.
"""
import logging
from datetime import datetime, timezone

from app import db, notify, quotes
from app.market_calendar import exchange_for_ticker, market_status
from app.models import Alert, PositionRule, StockAnalysis

logger = logging.getLogger(__name__)

KINDS = ("stop", "take_profit", "trailing")
_TITLES = {"stop": "Stop hit", "take_profit": "Take-profit reached", "trailing": "Trailing stop hit"}


def _money(v: float) -> str:
    return f"{v:,.2f}"


def stop_level(rule: PositionRule, avg_cost: float) -> float | None:
    """The fixed stop: the price itself, or stop_pct below the average cost."""
    if rule.stop_price is not None:
        return rule.stop_price
    if rule.stop_pct is not None:
        return round(avg_cost * (1 - rule.stop_pct / 100), 4)
    return None


def trail_level(rule: PositionRule) -> float | None:
    if rule.trailing_pct is None or rule.trail_high is None:
        return None
    return round(rule.trail_high * (1 - rule.trailing_pct / 100), 4)


def crossed(rule: PositionRule, avg_cost: float, price: float) -> list[tuple[str, float]]:
    """[(kind, level)] the price is through right now. Pure."""
    out = []
    stop = stop_level(rule, avg_cost)
    if stop is not None and price <= stop:
        out.append(("stop", stop))
    if rule.take_profit is not None and price >= rule.take_profit:
        out.append(("take_profit", rule.take_profit))
    trail = trail_level(rule)
    if trail is not None and price <= trail:
        out.append(("trailing", trail))
    return out


def describe(kind: str, ticker: str, ccy: str, price: float, level: float,
             avg_cost: float, rule: PositionRule) -> str:
    """The alert text: what crossed, by how much, and where the position stands."""
    if kind == "take_profit":
        what = f"{ticker} at {_money(price)} {ccy}, at or above your target of {_money(level)}"
    elif kind == "trailing":
        what = (f"{ticker} at {_money(price)} {ccy}, {rule.trailing_pct:g}% or more below its high "
                f"of {_money(rule.trail_high)} (trail at {_money(level)})")
    else:
        what = f"{ticker} at {_money(price)} {ccy}, at or below your stop of {_money(level)}"
    if kind != "take_profit" and price < level:
        what += f" — it gapped {(1 - price / level) * 100:.1f}% through"
    pl = (price / avg_cost - 1) * 100 if avg_cost else 0.0
    return (f"{what}. The position is {pl:+.1f}% against your average cost of "
            f"{_money(avg_cost)}. Rules alert only — nothing was sold.")


def suggested_stop(analysis: StockAnalysis | None, price: float | None) -> dict | None:
    """A stop proposed from the stored analysis (the same plan apply_sizing
    uses), with the reason. A suggestion only — never applied for you."""
    if analysis is None or analysis.stop is None or not price or analysis.stop >= price:
        return None
    pct = (1 - analysis.stop / price) * 100
    if analysis.stop_basis == "atr" and analysis.atr14 and analysis.entry:
        basis = f"{(analysis.entry - analysis.stop) / analysis.atr14:.1f}×ATR below the last close"
    elif analysis.stop_basis == "structure":
        basis = "under the nearest support, with an ATR buffer against stop-hunt wicks"
    elif analysis.stop_basis == "trendline":
        basis = "under the rising trendline, with an ATR buffer"
    else:
        basis = "from the stored trade plan"
    return {"stop_price": analysis.stop, "reason": f"{basis} — {pct:.1f}% below {_money(price)}",
            "computed_at": analysis.computed_at}


def _is_live(ticker: str, now: datetime) -> bool:
    exchange = exchange_for_ticker(ticker)
    return exchange is not None and market_status(now, exchange) == "LIVE"


def run(conn, now: datetime | None = None, quote_getter=None) -> int:
    """One evaluator pass over every active rule on a held position. Returns
    the number of alerts fired."""
    now = now or datetime.now(timezone.utc)
    rules = db.get_position_rules(conn, active_only=True)
    if not rules:
        return 0
    books: dict[tuple[int, str], dict] = {}
    for r in rules:
        key = (r.user_id, r.account)
        if key not in books:
            books[key] = {h.ticker: h for h in db.get_portfolio(conn, r.user_id, r.account)}
    held = [r for r in rules if r.ticker in books[(r.user_id, r.account)]]
    live = sorted({r.ticker for r in held if _is_live(r.ticker, now)})
    if not live:
        return 0
    by_ticker = {q.ticker: q for q in (quote_getter or quotes.get_quotes)(live)}
    at = now.isoformat(timespec="seconds")
    fired = 0
    for rule in held:
        price, _ = quotes.tradable_price(by_ticker.get(rule.ticker), now)
        if price is None:
            continue
        if rule.trailing_pct is not None:
            high = db.ratchet_trail_high(conn, rule, price)
            if high is None:
                continue  # edited or deleted since it was read
            rule = rule.model_copy(update={"trail_high": high})
        h = books[(rule.user_id, rule.account)][rule.ticker]
        for kind, level in crossed(rule, h.avg_cost, price):
            if kind in rule.fired:
                continue
            alert = Alert(
                dedup_key=f"exit|{rule.ticker}|{rule.user_id}|{rule.account}|{kind}|{rule.updated_at}",
                created_at=at, ticker=rule.ticker, type="exit", severity="high",
                title=_TITLES[kind],
                message=describe(kind, rule.ticker, h.currency, price, level, h.avg_cost, rule),
                user_id=rule.user_id)
            if db.fire_rule(conn, rule, kind, alert):
                fired += 1
                notify.push_stored(conn, [alert])
    return fired
