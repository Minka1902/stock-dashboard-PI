"""Order execution (STC-40, STC-42): every order goes through `submit`.

`submit` is the single choke point — validation now, the guardrails and the
audit log hook in here too — and then hands the order to the account's broker:

- `PaperBroker` ("paper"): market orders fill at the next tradable quote moved
  against you by the shared cost model (costs.apply); limit and stop orders
  rest until a quote crosses them. Resting orders are evaluated every minute
  (`poll`), only while the ticker's market is LIVE and on a fresh quote. A day
  order still open after its session is cancelled as expired. Fills go to the
  ledger as account "paper", atomically with the order's status.
- Engine-priced fills (the demo bots): the bot engine has already priced the
  fill on a tradable quote; `submit(..., fill=...)` runs the same checks and
  then lets the engine write the fill together with its own state.

- `AlpacaBroker` ("alpaca", app/alpaca.py): orders go to Alpaca's paper
  account. Fills come back through `sync_broker` (the minute job), each one
  a ledger row for exactly its new shares. Every few minutes the broker's
  positions and cash are reconciled against the ledger and any difference is
  recorded, labelled.

"live" stays refused: Alpaca live needs the per-order authenticator step
(STC-51).
"""
import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

from app import alpaca, audit, config, costs, db, events, ledger, quotes, telegram
from app.ledger import BOT_ACCOUNT
from app.market_calendar import exchange_for_ticker, is_trading_day, market_status, next_trading_day
from app.models import Holding, Order, Transaction

SIDES = ("BUY", "SELL")
TYPES = ("market", "limit", "stop")
TIFS = ("day", "gtc")
PAPER = "paper"
_EXCHANGE_TZ = {"NYSE": ZoneInfo("America/New_York"), "TASE": ZoneInfo("Asia/Jerusalem")}


class OrderRejected(Exception):
    """An order the checks refuse. `fields` names the bad inputs, if any."""

    def __init__(self, reason: str, fields: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.fields = fields or {}


class Broker(Protocol):
    def account(self, conn, user_id: int) -> dict: ...
    def positions(self, conn, user_id: int) -> list[Holding]: ...
    def place_order(self, conn, order: Order, now: datetime, quote_getter=None) -> Order: ...
    def cancel(self, conn, order: Order, reason: str) -> bool: ...
    def orders(self, conn, user_id: int, statuses: tuple[str, ...] | None = None) -> list[Order]: ...
    def poll_fills(self, conn, now: datetime, quote_getter=None) -> list[Order]: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _local_date(ticker: str, dt: datetime):
    return dt.astimezone(_EXCHANGE_TZ.get(exchange_for_ticker(ticker), timezone.utc)).date()


def session_for(ticker: str, placed: datetime):
    """The session a day order belongs to: today's if placed before or during
    it, else the next one (an order entered after the close is for tomorrow)."""
    exchange = exchange_for_ticker(ticker) or "NYSE"
    local = _local_date(ticker, placed)
    if is_trading_day(local, exchange) and market_status(placed, exchange) in ("PRE", "LIVE"):
        return local
    return next_trading_day(local, exchange)


def _is_live(ticker: str, now: datetime) -> bool:
    exchange = exchange_for_ticker(ticker)
    return exchange is not None and market_status(now, exchange) == "LIVE"


def slippage_bps(side: str, fill: float, reference: float | None) -> float | None:
    """Fill against the price the decision was made at, in bps; + is a cost."""
    if not reference:
        return None
    move = (fill / reference - 1) * 10_000
    return round(move if side == "BUY" else -move, 2)


def fill_price_for(order: Order, price: float) -> float | None:
    """What a resting order fills at on a quote of `price`, or None when the
    quote doesn't reach it. Costs always move against you; a limit never
    fills worse than its limit; a stop that gaps fills at the gapped price."""
    if order.type == "market":
        return costs.apply(price, order.side)
    if order.type == "limit":
        if order.side == "BUY" and price <= order.limit_price:
            return min(costs.apply(price, "BUY"), order.limit_price)
        if order.side == "SELL" and price >= order.limit_price:
            return max(costs.apply(price, "SELL"), order.limit_price)
        return None
    if order.side == "BUY" and price >= order.stop_price:
        return costs.apply(price, "BUY")
    if order.side == "SELL" and price <= order.stop_price:
        return costs.apply(price, "SELL")
    return None


def validate(order: Order) -> None:
    """Field-level checks every order passes before anything else."""
    errors = {}
    if order.side not in SIDES:
        errors["side"] = "BUY or SELL"
    if order.type not in TYPES:
        errors["type"] = f"One of: {', '.join(TYPES)}"
    if order.tif not in TIFS:
        errors["tif"] = "day or gtc"
    if not (isinstance(order.qty, (int, float)) and math.isfinite(order.qty) and order.qty > 0):
        errors["qty"] = "Enter a quantity above 0"
    if order.type == "limit" and not (order.limit_price and order.limit_price > 0):
        errors["limit_price"] = "A limit order needs a limit price above 0"
    if order.type == "stop" and not (order.stop_price and order.stop_price > 0):
        errors["stop_price"] = "A stop order needs a stop price above 0"
    for field in ("plan_stop", "plan_target"):
        v = getattr(order, field)
        if v is not None and not (math.isfinite(v) and v > 0):
            errors[field] = "Above 0, or leave it empty"
    if order.account == "live":
        raise OrderRejected("no live broker is connected — paper only")
    if order.account not in (PAPER, alpaca.ACCOUNT) and not BOT_ACCOUNT.fullmatch(order.account):
        errors["account"] = "paper, alpaca or a bot account"
    if errors:
        raise OrderRejected("Some fields need attention", errors)


# Checks run by submit after validation, in order: each returns None, a
# refusal reason, or (the guardrail's name, the reason). app/guardrails.py
# installs the guardrails (STC-50).
CHECKS: list[Callable[[object, Order, datetime], str | tuple[str, str] | None]] = []


def _run_checks(conn, order: Order, now: datetime) -> None:
    for check in CHECKS:
        refusal = check(conn, order, now)
        if refusal:
            name, reason = refusal if isinstance(refusal, tuple) else (check.__name__, refusal)
            audit.record(conn, order.user_id, "order_refused", account=order.account, ticker=order.ticker,
                         side=order.side, qty=order.qty, type=order.type, guardrail=name, reason=reason)
            raise OrderRejected(reason)


class PaperBroker:
    """The simulated broker behind account "paper"."""

    def account(self, conn, user_id: int) -> dict:
        return {"cash": [c.model_dump() for c in db.get_cash(conn, user_id, PAPER)]}

    def positions(self, conn, user_id: int) -> list[Holding]:
        return db.get_portfolio(conn, user_id, PAPER)

    def orders(self, conn, user_id: int, statuses=None) -> list[Order]:
        return db.get_orders(conn, user_id, PAPER, statuses)

    def place_order(self, conn, order: Order, now: datetime, quote_getter=None) -> Order:
        order = db.insert_order(conn, order.model_copy(update={"status": "submitted", "submitted_at": _iso(now)}))
        if _is_live(order.ticker, now):
            q = next(iter((quote_getter or quotes.get_quotes)([order.ticker])), None)
            return self._try_fill(conn, order, q, now) or order
        return order

    def cancel(self, conn, order: Order, reason: str = "cancelled by you") -> bool:
        return db.set_order_status(conn, order.id, "cancelled", reason)

    def _try_fill(self, conn, order: Order, q, now: datetime) -> Order | None:
        price, _ = quotes.tradable_price(q, now)
        if price is None:
            return None
        px = fill_price_for(order, price)
        if px is None:
            return None
        try:
            tx = ledger.build(order.user_id, ticker=order.ticker, side=order.side, qty=order.qty,
                              price=px, currency_code=order.currency, account=PAPER,
                              executed_at=_iso(now), source="paper_fill")
            done = db.fill_order(conn, order, tx, slippage_bps(order.side, px, order.reference_price))
        except ledger.LedgerError as exc:
            db.set_order_status(conn, order.id, "rejected", str(exc))
            audit.record(conn, order.user_id, "order_rejected", order_id=order.id, reason=str(exc))
            return db.get_order(conn, order.id)
        if done is not None:
            audit.record(conn, order.user_id, "order_filled", order_id=order.id, ticker=order.ticker,
                         side=order.side, qty=order.qty, price=px)
            events.publish("order", {"account": PAPER, "ticker": order.ticker, "side": order.side}, order.user_id)
        return done

    def poll_fills(self, conn, now: datetime, quote_getter=None) -> list[Order]:
        """Expire day orders whose session has passed, then fill whatever a
        fresh live quote now crosses. One quote call for every live ticker."""
        open_orders = db.get_orders(conn, account=PAPER, statuses=("submitted",), limit=10_000)
        resting = []
        for o in open_orders:
            if o.tif == "day" and _local_date(o.ticker, now) > session_for(
                    o.ticker, datetime.fromisoformat(o.submitted_at or o.created_at)):
                db.set_order_status(conn, o.id, "cancelled", "expired — day order past its session")
            else:
                resting.append(o)
        live = sorted({o.ticker for o in resting if _is_live(o.ticker, now)})
        if not live:
            return []
        by_ticker = {q.ticker: q for q in (quote_getter or quotes.get_quotes)(live)}
        filled = []
        for o in resting:
            if o.ticker in by_ticker:
                done = self._try_fill(conn, o, by_ticker[o.ticker], now)
                if done is not None and done.status == "filled":
                    filled.append(done)
        return filled


PAPER_BROKER = PaperBroker()


def _when(stamp: str | None, now: datetime) -> str:
    return datetime.fromisoformat(stamp).astimezone(timezone.utc).isoformat(timespec="seconds") if stamp else _iso(now)


class AlpacaBroker:
    """Account "alpaca": orders go to Alpaca, fills come back by `sync`."""

    def place_order(self, conn, order: Order, now: datetime) -> Order:
        order = db.insert_order(conn, order.model_copy(update={"status": "new", "submitted_at": _iso(now)}))
        try:
            with alpaca.client() as c:
                resp = alpaca.call(c, "POST", "/v2/orders", json=alpaca.payload(order))
        except alpaca.BrokerError as exc:
            db.set_order_status(conn, order.id, "rejected", str(exc), ("new",))
            return db.get_order(conn, order.id)
        db.set_broker_order_id(conn, order.id, resp["id"], "submitted")
        return self.apply(conn, db.get_order(conn, order.id), resp, now)

    def cancel(self, conn, order: Order, now: datetime) -> Order:
        with alpaca.client() as c:
            alpaca.call(c, "DELETE", f"/v2/orders/{order.broker_order_id}")
            resp = alpaca.call(c, "GET", f"/v2/orders/{order.broker_order_id}")
        done = self.apply(conn, order, resp, now)
        if done.status in db.OPEN_STATUSES:
            db.set_order_status(conn, done.id, done.status, "cancel requested", (done.status,))
        return db.get_order(conn, order.id)

    def apply(self, conn, order: Order, resp: dict, now: datetime) -> Order:
        """Alpaca's view of one order (and its bracket legs) into ours: a
        ledger row for any new fill, then the order's status."""
        status = alpaca.STATUS.get(resp.get("status"), order.status)
        filled = float(resp.get("filled_qty") or 0)
        avg = float(resp["filled_avg_price"]) if resp.get("filled_avg_price") else None
        delta = filled - order.filled_qty
        fields = {"status": status}
        tx = None
        if delta > ledger.EPS and avg:
            px = (avg * filled - (order.fill_price or 0) * order.filled_qty) / delta
            at = _when(resp.get("filled_at"), now)
            tx = ledger.build(order.user_id, ticker=order.ticker, side=order.side, qty=delta, price=px,
                              currency_code=order.currency, account=alpaca.ACCOUNT, executed_at=at,
                              source="alpaca_fill")
            fields.update(filled_qty=filled, fill_price=avg, filled_at=at,
                          slippage_bps=slippage_bps(order.side, avg, order.reference_price))
        if tx is None and status == order.status:
            done = order
        else:
            try:
                done = db.apply_broker_update(conn, order, tx, True, **fields)
            except ledger.LedgerError:
                # Alpaca filled past our cash mirror (its margin): recorded as
                # outside money now, and the reconcile settles the cash.
                done = db.apply_broker_update(conn, order, tx, False, **fields)
            if tx is not None:
                audit.record(conn, order.user_id, "order_filled", order_id=order.id, account=alpaca.ACCOUNT,
                             ticker=order.ticker, side=order.side, qty=delta, price=tx.price)
                events.publish("order", {"account": alpaca.ACCOUNT, "ticker": order.ticker, "side": order.side},
                               order.user_id)
                telegram.queue(order.user_id, f"Alpaca {alpaca.mode()} fill: {order.side} {delta:g} {order.ticker} "
                                              f"at {tx.price:g}.")
        for leg in resp.get("legs") or []:
            mine = db.get_order_by_broker_id(conn, leg["id"])
            if mine is None:
                if alpaca.STATUS.get(leg.get("status")) == "cancelled" and not float(leg.get("filled_qty") or 0):
                    continue
                mine = db.insert_order(conn, Order(
                    user_id=order.user_id, account=alpaca.ACCOUNT, ticker=order.ticker, side=leg["side"].upper(),
                    qty=float(leg["qty"]), type=leg.get("type", "market") if leg.get("type") in TYPES else "stop",
                    limit_price=float(leg["limit_price"]) if leg.get("limit_price") else None,
                    stop_price=float(leg["stop_price"]) if leg.get("stop_price") else None,
                    tif=leg.get("time_in_force", "gtc") if leg.get("time_in_force") in TIFS else "gtc",
                    status="submitted", broker_order_id=leg["id"], currency=order.currency,
                    created_at=_iso(now), submitted_at=_iso(now), source="bracket",
                    reason=f"bracket exit of order {order.id}"))
            self.apply(conn, mine, leg, now)
        return done

    def sync(self, conn, user_id: int, now: datetime) -> int:
        """Pull the last week's orders (legs nested) and apply each of ours."""
        after = (now - timedelta(days=7)).isoformat()
        with alpaca.client() as c:
            resps = alpaca.call(c, "GET", "/v2/orders", params={"status": "all", "nested": "true",
                                                               "after": after, "limit": 500})
        n = 0
        for r in resps:
            mine = db.get_order_by_broker_id(conn, r["id"])
            if mine is not None and mine.user_id == user_id:
                self.apply(conn, mine, r, now)
                n += 1
        return n

    def reconcile(self, conn, user_id: int, now: datetime) -> dict:
        """Make the ledger's "alpaca" account match Alpaca's positions and
        cash; each difference is a labelled ledger row and is audited."""
        with alpaca.client() as c:
            positions = alpaca.call(c, "GET", "/v2/positions")
            account = alpaca.call(c, "GET", "/v2/account")
        at = _iso(now)
        theirs = {alpaca.ticker(p["symbol"]): (float(p["qty"]), float(p["avg_entry_price"])) for p in positions}
        ours = {h.ticker: h for h in db.get_portfolio(conn, user_id, alpaca.ACCOUNT)}
        diffs = []
        for t in sorted(set(theirs) | set(ours)):
            qty, avg = theirs.get(t, (0.0, ours[t].avg_cost if t in ours else 0.0))
            held = ours[t].shares if t in ours else 0.0
            if abs(qty - held) > ledger.EPS:
                ledger.record(conn, user_id, ticker=t, side="ADJUST", qty=max(qty, 0.0), price=avg or 1.0,
                              currency_code="USD", account=alpaca.ACCOUNT, executed_at=at, source="reconcile")
                diffs.append({"ticker": t, "ledger": held, "alpaca": qty})
        cash = float(account["cash"])
        mine = sum(b.balance for b in db.get_cash(conn, user_id, alpaca.ACCOUNT) if b.currency == "USD")
        if abs(cash - mine) >= 0.01:
            ledger.record(conn, user_id, ticker="USD", side="DEPOSIT" if cash > mine else "WITHDRAW",
                          qty=round(abs(cash - mine), 2), currency_code="USD", account=alpaca.ACCOUNT,
                          executed_at=at, source="reconcile")
            diffs.append({"ticker": "USD cash", "ledger": round(mine, 2), "alpaca": cash})
        if diffs:
            audit.record(conn, user_id, "broker_reconciled", diffs=diffs)
        return {"at": at, "diffs": diffs}


ALPACA_BROKER = AlpacaBroker()
_last_reconcile: dict[int, datetime] = {}


def cancel_broker_orders(conn, user_id: int) -> list[int]:
    """Ask Alpaca to cancel every open order of the user's (the kill switch).
    Returns the ids it accepted; a refusal is audited, never swallowed."""
    out = []
    for o in db.get_orders(conn, user_id, alpaca.ACCOUNT, db.OPEN_STATUSES, limit=10_000):
        if o.source == "bracket" or not o.broker_order_id:
            continue
        try:
            ALPACA_BROKER.cancel(conn, o, _now())
            out.append(o.id)
        except alpaca.BrokerError as exc:
            audit.record(conn, user_id, "broker_cancel_failed", order_id=o.id, reason=str(exc))
    return out


def sync_broker(conn, now: datetime | None = None, force_reconcile: bool = False) -> dict:
    """The minute job's broker pass: reconcile first when the ledger has never
    seen the account or it's due, then pull order updates."""
    now = now or _now()
    uid = alpaca.owner(conn)
    if uid is None or alpaca.mode() != "paper":
        return {}
    out = {}
    last = _last_reconcile.get(uid)
    if force_reconcile or last is None or (now - last).total_seconds() >= config.ALPACA_RECONCILE_SECONDS:
        out["reconcile"] = ALPACA_BROKER.reconcile(conn, uid, now)
        _last_reconcile[uid] = now
    out["orders"] = ALPACA_BROKER.sync(conn, uid, now)
    return out


def submit(conn, order: Order, now: datetime | None = None, quote_getter=None,
           fill: Callable[[Order], Transaction | None] | None = None) -> Order | None:
    """The one way an order enters the system. Raises OrderRejected.

    Without `fill`, the order goes to the paper broker. With `fill` (a bot
    engine that has priced the fill itself), the checks run and then `fill`
    writes order, ledger row and engine state in one transaction; a None from
    it means the bot stopped meanwhile, nothing was written, and submit
    returns None."""
    now = now or _now()
    order = order.model_copy(update={"created_at": order.created_at or _iso(now)})
    validate(order)
    if order.account == alpaca.ACCOUNT:
        if alpaca.owner(conn) != order.user_id:
            raise OrderRejected("no broker is connected for your account")
        if alpaca.mode() != "paper":
            raise OrderRejected("Alpaca live trading needs the per-order authenticator step, which isn't built yet")
        if exchange_for_ticker(order.ticker) != "NYSE":
            raise OrderRejected("Alpaca trades US listings only")
        if order.user_id not in _last_reconcile:
            try:
                sync_broker(conn, now, force_reconcile=True)
            except alpaca.BrokerError as exc:
                raise OrderRejected(f"can't reach Alpaca to check the account first — {exc}") from None
    _run_checks(conn, order, now)
    if fill is not None:
        done = order.model_copy(update={
            "status": "filled", "submitted_at": _iso(now), "filled_at": _iso(now),
            "slippage_bps": slippage_bps(order.side, order.fill_price, order.reference_price)})
        tx = fill(done)
        if tx is None:
            return None
        audit.record(conn, order.user_id, "order_filled", order_id=tx.order_id, account=order.account,
                     ticker=order.ticker, side=order.side, qty=order.qty, price=order.fill_price)
        events.publish("order", {"account": order.account, "ticker": order.ticker, "side": order.side},
                       order.user_id)
        if order.account.startswith("bot:"):
            telegram.queue(order.user_id, f"Bot fill ({order.account}): {order.side} {order.qty:g} {order.ticker} "
                                          f"at {order.fill_price:g} {order.currency}. {order.reason or ''}".strip())
        return done.model_copy(update={"id": tx.order_id})
    if order.account == alpaca.ACCOUNT:
        placed = ALPACA_BROKER.place_order(conn, order, now)
    elif order.account == PAPER:
        placed = PAPER_BROKER.place_order(conn, order, now, quote_getter)
    else:
        raise OrderRejected("orders on this account are placed by its bot")
    audit.record(conn, order.user_id, "order_placed", order_id=placed.id, account=order.account, ticker=order.ticker,
                 side=order.side, qty=order.qty, type=order.type, limit=order.limit_price,
                 stop=order.stop_price, tif=order.tif, status=placed.status)
    return placed


def cancel(conn, user_id: int, order_id: int) -> Order:
    """Cancel one of the user's open paper or Alpaca orders. Raises
    OrderRejected when it isn't theirs, can't be cancelled, or has already
    filled or been cancelled."""
    o = db.get_order(conn, order_id)
    if o is None or o.user_id != user_id:
        raise OrderRejected("no such order")
    if o.account == alpaca.ACCOUNT and o.status in db.OPEN_STATUSES and o.broker_order_id:
        try:
            done = ALPACA_BROKER.cancel(conn, o, _now())
        except alpaca.BrokerError as exc:
            raise OrderRejected(str(exc)) from None
        audit.record(conn, user_id, "order_cancelled", order_id=order_id, account=alpaca.ACCOUNT)
        return done
    if o.account != PAPER or not PAPER_BROKER.cancel(conn, o):
        raise OrderRejected(f"this order is {o.status} and can't be cancelled")
    audit.record(conn, user_id, "order_cancelled", order_id=order_id)
    return db.get_order(conn, order_id)


def poll(conn, now: datetime | None = None, quote_getter=None) -> int:
    """The minute job's paper pass. Returns the number of fills."""
    return len(PAPER_BROKER.poll_fills(conn, now or _now(), quote_getter))


def median_slippage(conn, user_id: int | None, account: str) -> dict:
    """Measured slippage of filled orders: {median_bps, n}. For a live
    account this is what COST_BPS_PER_SIDE should be calibrated to; paper
    fills are simulated with the cost model itself, so theirs only confirms
    the model and must never feed back into it."""
    values = db.get_slippage(conn, user_id, account)
    return {"median_bps": round(statistics.median(values), 2) if values else None, "n": len(values),
            "calibrates_costs": account == "live"}


def reset_paper(conn, user_id: int, amount: float, ccy: str, now: datetime | None = None) -> None:
    """Start the paper account over: cancel open orders, clear the positions
    and cash, then deposit `amount` of `ccy`. Every step is a ledger row, so
    the account still replays from its history."""
    if not (math.isfinite(amount) and amount > 0):
        raise OrderRejected("Some fields need attention", {"amount": "Enter an amount above 0"})
    at = _iso(now or _now())
    for o in db.get_orders(conn, user_id, PAPER, ("submitted",), limit=10_000):
        db.set_order_status(conn, o.id, "cancelled", "paper account reset")
    for h in db.get_portfolio(conn, user_id, PAPER):
        ledger.record(conn, user_id, ticker=h.ticker, side="ADJUST", qty=0, price=h.avg_cost,
                      currency_code=h.currency, account=PAPER, executed_at=at, source="paper_reset")
    for c in db.get_cash(conn, user_id, PAPER):
        if c.balance > 0:
            ledger.record(conn, user_id, ticker=c.currency, side="WITHDRAW", qty=c.balance,
                          currency_code=c.currency, account=PAPER, executed_at=at, source="paper_reset")
    ledger.record(conn, user_id, ticker=ccy, side="DEPOSIT", qty=amount, currency_code=ccy,
                  account=PAPER, executed_at=at, source="paper_init")
    audit.record(conn, user_id, "paper_reset", amount=amount, currency=ccy)


# The guardrails install themselves into CHECKS; importing them here means
# every path that can submit an order has them, whatever imported it first.
from app import guardrails  # noqa: E402,F401
