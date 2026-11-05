"""The paper account and its order ticket (STC-39).

Orders go through execution.submit — the same choke point as the bots — and
rest until a quote crosses them. `ticket-defaults` pre-fills a ticket from
the stored analysis (size from your risk settings, stop from your exit rule
or the trade plan), so a suggestion becomes an order in one confirm.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import analysis, auth, config, currency, db, execution, quotes
from app.models import Order
from app.validation import clean_ticker

PAPER = execution.PAPER
MODES = ("paper", "live")


class OrderBody(BaseModel):
    ticker: str
    side: str
    qty: float
    type: str = "market"
    limit_price: float | None = None
    stop_price: float | None = None
    tif: str = "day"
    suggestion_for_date: str | None = None   # the suggestion this ticket came from
    plan_stop: float | None = None
    plan_target: float | None = None


class ResetBody(BaseModel):
    amount: float = config.PAPER_START_CASH
    currency: str = config.PAPER_START_CURRENCY


def _reject(exc: execution.OrderRejected, status: int = 422):
    raise HTTPException(status_code=status, detail={"message": exc.reason, "errors": exc.fields}) from None


def _price(conn, ticker: str) -> tuple[float | None, str | None]:
    """The live quote, else the last daily close — and which it was."""
    q = next(iter(quotes.get_quotes([ticker])), None)
    if q and q.price:
        return q.price, "live"
    bars = db.get_ohlc(conn, ticker, "daily")
    return (bars[-1].close, f"the {bars[-1].date} close") if bars else (None, None)


def _summary(conn, user_id: int) -> dict:
    holdings = db.get_portfolio(conn, user_id, PAPER)
    live = {q.ticker: q.price for q in quotes.get_quotes([h.ticker for h in holdings]) if q.price} \
        if holdings else {}
    positions = []
    for h in holdings:
        px = live.get(h.ticker)
        positions.append({**h.model_dump(), "price": px,
                          "value": h.shares * px if px is not None else None,
                          "pl_pct": round((px / h.avg_cost - 1) * 100, 2) if px and h.avg_cost else None})
    cash = db.get_cash(conn, user_id, PAPER)
    return {
        "mode": "paper",
        "modes": [{"value": "paper", "available": True},
                  {"value": "live", "available": False, "reason": "no broker connected"}],
        "funded": any(c.balance > 0 for c in cash) or bool(holdings),
        "cash": [c.model_dump() for c in cash],
        "positions": positions,
        "open_orders": [o.model_dump() for o in db.get_orders(conn, user_id, PAPER, ("submitted",))],
        "recent_orders": [o.model_dump() for o in db.get_orders(conn, user_id, PAPER, limit=50)],
        "slippage": execution.median_slippage(conn, user_id, PAPER),
        "defaults": {"amount": config.PAPER_START_CASH, "currency": config.PAPER_START_CURRENCY},
        "cost_bps_per_side": config.COST_BPS_PER_SIDE,
    }


def place_ticket(conn, body: OrderBody, user_id: int, account: str) -> dict:
    """An order ticket into `account` through execution.submit: priced at
    the decision, linked to the suggestion it came from. 422 with the reason
    when anything refuses it."""
    t = clean_ticker(body.ticker)
    ref, suggestion_id = _price(conn, t)[0], None
    if body.suggestion_for_date:
        found = db.get_suggestion_ref(conn, user_id, t, body.suggestion_for_date)
        if found:
            suggestion_id, ref = found[0], found[1] or ref
    order = Order(user_id=user_id, account=account, ticker=t, side=body.side.upper(), qty=body.qty,
                  type=body.type, limit_price=body.limit_price, stop_price=body.stop_price,
                  tif=body.tif, currency=currency.currency_for_symbol(t) or "USD",
                  created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  suggestion_id=suggestion_id, reference_price=ref, source="ticket",
                  plan_stop=body.plan_stop, plan_target=body.plan_target)
    try:
        return execution.submit(conn, order).model_dump()
    except execution.OrderRejected as exc:
        _reject(exc)


def build_router(conn) -> APIRouter:
    router = APIRouter(prefix="/api/paper")

    @router.get("")
    def paper_summary(user=Depends(auth.get_current_user)):
        return _summary(conn, user.id)

    @router.post("/reset")
    def reset(body: ResetBody, user=Depends(auth.get_current_user)):
        ccy = body.currency.upper()
        if ccy not in currency.BASE_CURRENCIES:
            raise HTTPException(status_code=422, detail={
                "message": "Some fields need attention",
                "errors": {"currency": f"One of: {', '.join(currency.BASE_CURRENCIES)}"}})
        try:
            execution.reset_paper(conn, user.id, body.amount, ccy)
        except execution.OrderRejected as exc:
            _reject(exc)
        return _summary(conn, user.id)

    @router.get("/ticket-defaults")
    def ticket_defaults(ticker: str, side: str = "BUY", user=Depends(auth.get_current_user)):
        t = clean_ticker(ticker)
        price, source = _price(conn, t)
        ccy = currency.currency_for_symbol(t) or "USD"
        a = db.get_analysis(conn, t)
        rule = db.get_position_rule(conn, user.id, PAPER, t)
        held = next((h for h in db.get_portfolio(conn, user.id, PAPER) if h.ticker == t), None)
        qty, stop, target, sizing_note = None, None, None, None
        if side.upper() == "SELL":
            qty = held.shares if held else None
        elif a is not None:
            profile = db.get_notify_profile(conn, user.id)
            # The paper book in this currency (cash + positions at cost) is the
            # account size the risk % applies to.
            equity = sum(c.balance for c in db.get_cash(conn, user.id, PAPER) if c.currency == ccy) + sum(
                h.shares * h.avg_cost for h in db.get_portfolio(conn, user.id, PAPER) if h.currency == ccy)
            sized = analysis.apply_sizing(a, equity or None, profile.risk_pct, account_currency=ccy)
            qty, sizing_note = sized.suggested_shares, sized.sizing_note
            stop, target = a.stop, a.target
        if rule and rule.stop_price is not None:
            stop = rule.stop_price
        return {"ticker": t, "side": side.upper(), "price": price, "price_source": source, "currency": ccy,
                "qty": qty, "stop": stop, "target": target, "held": held.shares if held else 0,
                "sizing_note": sizing_note,
                "risk_pct": db.get_notify_profile(conn, user.id).risk_pct}

    @router.post("/orders")
    def place(body: OrderBody, user=Depends(auth.get_current_user)):
        return place_ticket(conn, body, user.id, PAPER)

    @router.post("/orders/{order_id}/cancel")
    def cancel(order_id: int, user=Depends(auth.get_current_user)):
        try:
            return execution.cancel(conn, user.id, order_id).model_dump()
        except execution.OrderRejected as exc:
            _reject(exc, 409)

    return router
