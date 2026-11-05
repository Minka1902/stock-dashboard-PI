"""Server-side order guardrails, run inside execution.submit for every order:
paper tickets and bot fills alike.

- The order limits (STC-50) have safe defaults.
- The per-trade limits (STC-82) are each off until set. They are ported from
  the trader-risk skill in Jake Schincariol's day-trader-skill (MIT,
  github.com/Jakeschincariol/day-trader-skill): max loss at the stop,
  minimum reward:risk, daily loss limit and entry window.
- **An exit is never blocked.** An exit is a sell that only reduces a holding.
  Getting out must always be possible, so every check below applies to
  entries alone, the kill switch included.
- Money limits are in the user's base currency. A limit that can't be checked
  (no price, no FX rate) refuses the entry rather than waving it through.
"""
import math
from datetime import datetime, timedelta

from app import costs, currency, db, execution
from app.market_calendar import exchange_for_ticker, session_hours
from app.models import Order, RiskSettings

_EXCHANGE_TZ = execution._EXCHANGE_TZ


def _price(o: Order) -> float | None:
    return o.fill_price or o.limit_price or o.stop_price or o.reference_price


def entry_price(o: Order) -> float | None:
    """The price an entry's risk is measured from: the limit for a limit
    order, else the higher of the decision price and the fill (a market
    order can't understate its risk because the price ran up)."""
    if o.type == "limit" and o.limit_price:
        return o.limit_price
    prices = [p for p in (o.fill_price, o.reference_price, o.stop_price if o.type == "stop" else None) if p]
    return max(prices) if prices else None


def loss_at_stop(o: Order) -> float | None:
    """What the entry loses if it's sold at its plan stop, in its own
    currency. Without a stop the whole position is at risk."""
    entry = entry_price(o)
    if entry is None:
        return None
    return o.qty * (entry - o.plan_stop if o.plan_stop is not None else entry)


class _Ctx:
    def __init__(self, conn, o: Order, now: datetime):
        self.conn, self.o, self.now = conn, o, now
        self.s: RiskSettings = db.get_risk_settings(conn, o.user_id)
        self._base = None

    def in_base(self, amount: float, ccy: str) -> float | None:
        if self._base is None:
            self._base = db.get_notify_profile(self.conn, self.o.user_id).base_currency
        rate = currency.fx_rate(ccy, self._base)
        return amount * rate if rate is not None else None


def kill_switch(c: _Ctx) -> str | None:
    if c.s.trading_halted_at:
        return (f"trading is halted by your kill switch (since {c.s.trading_halted_at[:16].replace('T', ' ')} UTC); "
                f"selling what you hold still works")
    return None


def market_hours(c: _Ctx) -> str | None:
    if c.s.allow_outside_hours or execution._is_live(c.o.ticker, c.now):
        return None
    return (f"{c.o.ticker}'s market isn't open — orders outside regular hours are off "
            f"(turn on 'allow outside hours' on the Risk tab to queue them)")


def entry_window(c: _Ctx) -> str | None:
    after, before = c.s.no_entry_after_open_min, c.s.no_entry_before_close_min
    if not after and not before:
        return None
    ex = exchange_for_ticker(c.o.ticker)
    if ex is None:
        return None
    local = c.now.astimezone(_EXCHANGE_TZ[ex])
    hours = session_hours(local.date(), ex)
    if hours is None:
        return f"{c.o.ticker}'s market has no session today, so no entry window"
    day = local.replace(second=0, microsecond=0)
    open_, close = (day.replace(hour=int(h[:2]), minute=int(h[3:])) for h in hours)
    start, end = open_ + timedelta(minutes=after or 0), close - timedelta(minutes=before or 0)
    if not start <= local < end:
        return (f"new entries in {c.o.ticker} are allowed {start:%H:%M}–{end:%H:%M} {ex} time today "
                f"(your entry window); it's {local:%H:%M}")
    return None


def order_notional(c: _Ctx) -> str | None:
    px = _price(c.o)
    if px is None:
        return "no price to check the order size against your limit"
    value = c.in_base(c.o.qty * px, c.o.currency)
    if value is None:
        return f"no {c.o.currency} exchange rate to check the order size against your limit"
    if value > c.s.max_order_notional:
        return f"order is {value:,.0f} in your base currency; the limit per order is {c.s.max_order_notional:,.0f}"
    return None


def daily_limits(c: _Ctx) -> str | None:
    today = db.orders_today(c.conn, c.o.user_id, c.now.date().isoformat())
    if len(today) >= c.s.max_orders_per_day:
        return f"{len(today)} orders today; the daily limit is {c.s.max_orders_per_day}"
    total = 0.0
    for x in [*today, c.o]:
        px = _price(x)
        value = c.in_base(x.qty * px, x.currency) if px else None
        if value is None:
            return f"no price or {x.currency} rate to check today's total against your limit"
        total += value
    if total > c.s.max_notional_per_day:
        return f"today's orders would total {total:,.0f}; the daily limit is {c.s.max_notional_per_day:,.0f}"
    return None


def position_size(c: _Ctx) -> str | None:
    o = c.o
    px = _price(o) or 0.0
    holdings = [h for h in db.get_portfolio(c.conn, o.user_id, o.account) if h.currency == o.currency]
    equity = sum(x.balance for x in db.get_cash(c.conn, o.user_id, o.account) if x.currency == o.currency) \
        + sum(h.shares * h.avg_cost for h in holdings)
    if equity <= 0:
        return None   # nothing to measure against; the ledger refuses an unfunded buy anyway
    held = next((h.shares * h.avg_cost for h in holdings if h.ticker == o.ticker), 0.0)
    pct = (held + o.qty * px) / equity * 100
    if pct > c.s.max_position_pct:
        return f"{o.ticker} would be {pct:.0f}% of the account; the limit per position is {c.s.max_position_pct:g}%"
    return None


def cash_only(c: _Ctx) -> str | None:
    """A paper or broker buy must fit the cash in its currency: this app
    never borrows, whatever margin the broker would allow."""
    o = c.o
    if o.side != "BUY" or o.account not in ("paper", "alpaca") or o.fill_price is not None:
        return None
    px = entry_price(o)
    if px is None:
        return "no price to check the buy against your cash"
    need = costs.apply(px, "BUY") * o.qty
    cash = sum(x.balance for x in db.get_cash(c.conn, o.user_id, o.account) if x.currency == o.currency)
    if need > cash + 1e-6:
        return f"this buy needs about {need:,.2f} {o.currency}; the account has {cash:,.2f} — no margin here"
    return None


def stop_and_target(c: _Ctx) -> str | None:
    o, entry = c.o, entry_price(c.o)
    if o.plan_stop is None and c.s.require_stop:
        return "your limits require a stop on every entry; set one"
    if entry is None:
        return None
    if o.plan_stop is not None and o.plan_stop >= entry:
        return f"the stop {o.plan_stop:g} must be below the entry {entry:g}"
    if o.plan_target is not None and o.plan_target <= entry:
        return f"the target {o.plan_target:g} must be above the entry {entry:g}"
    return None


def loss_per_trade(c: _Ctx) -> str | None:
    limit = c.s.max_loss_per_trade
    if limit is None:
        return None
    loss = loss_at_stop(c.o)
    base = c.in_base(loss, c.o.currency) if loss is not None else None
    if base is None:
        return "no price or exchange rate to check the loss at the stop against your limit"
    if base <= limit:
        return None
    per_share = base / c.o.qty
    fits = math.floor(limit / per_share) if per_share > 0 else 0
    where = "at the stop" if c.o.plan_stop is not None else "with no stop (the whole position)"
    return (f"this entry could lose {base:,.2f} {where}; your max loss per trade is {limit:,.2f} "
            f"— {fits} share{'s' if fits != 1 else ''} would fit")


def reward_risk(c: _Ctx) -> str | None:
    floor = c.s.min_reward_risk
    if floor is None:
        return None
    o, entry = c.o, entry_price(c.o)
    if o.plan_stop is None or o.plan_target is None or entry is None:
        return f"your minimum reward:risk is {floor:g}R; set a stop and a target to measure it"
    rr = (o.plan_target - entry) / (entry - o.plan_stop)
    if rr < floor:
        return f"reward:risk is {rr:.2f}R; your minimum is {floor:g}R"
    return None


def daily_loss(c: _Ctx) -> str | None:
    limit = c.s.max_daily_loss
    if limit is None:
        return None
    entries = [x for x in db.orders_today(c.conn, c.o.user_id, c.now.date().isoformat())
               if x.side == "BUY" and x.status in ("submitted", "filled", "partially_filled")]
    total = 0.0
    for x in [*entries, c.o]:
        loss = loss_at_stop(x)
        base = c.in_base(loss, x.currency) if loss is not None else None
        if base is None:
            return "no price or exchange rate to check today's risk against your daily loss limit"
        total += base
    if total > limit:
        return (f"today's entries could lose {total:,.2f} at their stops with this one; "
                f"your daily loss limit is {limit:,.2f}")
    return None


ENTRY_CHECKS = (kill_switch, market_hours, entry_window, order_notional, daily_limits, cash_only, position_size,
                stop_and_target, loss_per_trade, reward_risk, daily_loss)


def is_exit(conn, o: Order) -> bool:
    """A sell of no more than is held in that account: it only reduces risk."""
    if o.side != "SELL":
        return False
    held = next((h.shares for h in db.get_portfolio(conn, o.user_id, o.account) if h.ticker == o.ticker), 0.0)
    return o.qty <= held + 1e-9


def guardrails(conn, o: Order, now: datetime) -> tuple[str, str] | None:
    if is_exit(conn, o):
        return None
    c = _Ctx(conn, o, now)
    for check in ENTRY_CHECKS:
        reason = check(c)
        if reason:
            return check.__name__, reason
    return None


execution.CHECKS[:] = [guardrails]
