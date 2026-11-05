"""The Alpaca account (STC-86): its status and connection checks, the
mirrored positions and orders, placing and cancelling, and a sync now."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app import alpaca, auth, db, execution, quotes
from app.routes_paper import OrderBody, _reject, place_ticket


def build_router(conn) -> APIRouter:
    router = APIRouter(prefix="/api/broker")

    def own(user_id: int) -> None:
        if alpaca.owner(conn) != user_id:
            raise HTTPException(status_code=409, detail="no broker is connected for your account")

    @router.get("")
    def status(user=Depends(auth.get_current_user)):
        if not alpaca.configured():
            return {"configured": False, "yours": False}
        if alpaca.owner(conn) != user.id:
            return {"configured": True, "yours": False}
        out = {"configured": True, "yours": True, "broker": "Alpaca", "mode": alpaca.mode(),
               "account": alpaca.ACCOUNT, "error": None, "checks": [], "summary": None}
        try:
            with alpaca.client() as c:
                acct = alpaca.call(c, "GET", "/v2/account")
                cfg = alpaca.call(c, "GET", "/v2/account/configurations")
            out["checks"] = alpaca.checks(acct, cfg)
            out["summary"] = {k: float(acct[k]) for k in ("cash", "buying_power", "equity") if acct.get(k)}
        except alpaca.BrokerError as exc:
            out["error"] = str(exc)
        holdings = db.get_portfolio(conn, user.id, alpaca.ACCOUNT)
        prices = quotes.latest_prices(conn, [h.ticker for h in holdings])
        out["positions"] = [{**h.model_dump(), "price": prices.get(h.ticker, (None, None))[0]} for h in holdings]
        out["cash"] = [c.model_dump() for c in db.get_cash(conn, user.id, alpaca.ACCOUNT)]
        out["open_orders"] = [o.model_dump() for o in db.get_orders(conn, user.id, alpaca.ACCOUNT, db.OPEN_STATUSES)]
        out["recent_orders"] = [o.model_dump() for o in db.get_orders(conn, user.id, alpaca.ACCOUNT, limit=50)]
        out["slippage"] = execution.median_slippage(conn, user.id, alpaca.ACCOUNT)
        return out

    @router.post("/orders")
    def place(body: OrderBody, user=Depends(auth.get_current_user)):
        own(user.id)
        return place_ticket(conn, body, user.id, alpaca.ACCOUNT)

    @router.post("/orders/{order_id}/cancel")
    def cancel(order_id: int, user=Depends(auth.get_current_user)):
        try:
            return execution.cancel(conn, user.id, order_id).model_dump()
        except execution.OrderRejected as exc:
            _reject(exc, 409)

    @router.post("/sync")
    def sync(user=Depends(auth.get_current_user)):
        own(user.id)
        try:
            return {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    **execution.sync_broker(conn, force_reconcile=True)}
        except alpaca.BrokerError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None

    return router
