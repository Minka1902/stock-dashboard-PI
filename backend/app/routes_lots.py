"""Tax lots, a sale check, target weights and the rebalance plan (STC-85)."""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app import audit, auth, currency, db, quotes, rebalance, taxlots
from app.validation import clean_ticker

ACCOUNTS = ("manual", "paper")


class SaleBody(BaseModel):
    account: str = "manual"
    ticker: str
    qty: float
    method: str = "fifo"
    lot_ids: list[str] = []
    price: float | None = None


class TargetsBody(BaseModel):
    account: str = "manual"
    targets: dict[str, float]


def _account(account: str) -> str:
    if account not in ACCOUNTS:
        raise HTTPException(status_code=400, detail="account must be 'manual' or 'paper'")
    return account


def _fields(message: str, errors: dict):
    raise HTTPException(status_code=422, detail={"message": message, "errors": errors})


def _lots(conn, user_id: int, account: str):
    """(this account's lots, realized sales, the lots wash sales are checked against)."""
    lots, realized = taxlots.build(db.get_ledger(conn, user_id, (account,)))
    if account in taxlots.REAL_ACCOUNTS:
        wash, _ = taxlots.build(db.get_ledger(conn, user_id, taxlots.REAL_ACCOUNTS))
    else:
        wash = lots
    return lots, realized, wash


def build_router(conn) -> APIRouter:
    router = APIRouter(prefix="/api/portfolio")

    @router.get("/lots")
    def lots_view(account: str = "manual", min_loss: float = Query(100.0, ge=0),
                  user=Depends(auth.get_current_user)):
        account = _account(account)
        lots, realized, wash = _lots(conn, user.id, account)
        prices = quotes.latest_prices(conn, sorted(lots))
        ccy = {h.ticker: h.currency for h in db.get_portfolio(conn, user.id, account)}
        today = date.today()
        positions = []
        for t, held in sorted(lots.items()):
            px, source = prices.get(t, (None, None))
            rows = [{"id": lot.id, "acquired": lot.acquired.isoformat(), "qty": lot.qty,
                     "per_share": round(lot.per_share, 4), "cost": round(lot.qty * lot.per_share, 2),
                     "value": round(lot.qty * px, 2) if px else None,
                     "gain": round(lot.qty * (px - lot.per_share), 2) if px else None,
                     "term": taxlots.term(lot.acquired, today), "exact": lot.exact,
                     "long_term_from": taxlots.long_term_from(lot.acquired).isoformat()} for lot in held]
            positions.append({"ticker": t, "currency": ccy.get(t, "USD"), "price": px, "price_source": source,
                              "qty": sum(lot.qty for lot in held), "lots": rows,
                              "unrealized": {k: round(sum(r["gain"] for r in rows if r["gain"] is not None
                                                          and r["term"] == k), 2) for k in ("short", "long")}})
        return {"account": account, "as_of": today.isoformat(), "positions": positions,
                "realized": taxlots.realized_by_year(realized, today.year),
                "harvest": taxlots.harvest(lots, {t: p for t, (p, _) in prices.items()}, today, min_loss, wash),
                "approximate": any(not lot.exact for held in lots.values() for lot in held),
                "wash_scope": "your real accounts" if account in taxlots.REAL_ACCOUNTS else "this paper account",
                "note": taxlots.NOT_ADVICE}

    @router.post("/lots/sale")
    def sale(body: SaleBody, user=Depends(auth.get_current_user)):
        account, t = _account(body.account), clean_ticker(body.ticker)
        lots, _, wash = _lots(conn, user.id, account)
        price = body.price or quotes.latest_prices(conn, [t]).get(t, (None,))[0]
        if not price:
            _fields(f"No price for {t}", {"price": "enter a price"})
        today = date.today()
        try:
            out = taxlots.simulate_sale(lots.get(t, []), body.qty, price, today, body.method, body.lot_ids)
        except taxlots.TaxError as exc:
            _fields(str(exc), {"lot_ids" if body.method == "specific" else "qty": str(exc)})
        hits = taxlots.wash_hits(t, today, out["lots"], wash) if out["gain"] < 0 else []
        return {**out, "ticker": t, "price": price, "method": body.method, "wash": hits,
                "disallowed": taxlots.disallowed(out["gain"], body.qty, hits),
                "rebuy_on_or_after": taxlots.rebuy_date(today) if out["gain"] < 0 else None,
                "note": taxlots.NOT_ADVICE}

    @router.get("/targets")
    def get_targets(account: str = "manual", user=Depends(auth.get_current_user)):
        return {"account": _account(account), "targets": db.get_target_weights(conn, user.id, account)}

    @router.put("/targets")
    def put_targets(body: TargetsBody, user=Depends(auth.get_current_user)):
        account = _account(body.account)
        clean, errors = {}, {}
        for raw, w in body.targets.items():
            t = clean_ticker(raw)
            if not 0 < w <= 100:
                errors[t] = "between 0 and 100"
            clean[t] = round(w, 2)
        if sum(clean.values()) > 100 + 1e-9:
            errors["total"] = f"targets add up to {sum(clean.values()):g}%; at most 100"
        if errors:
            _fields("Some targets need attention", errors)
        db.replace_target_weights(conn, user.id, account, clean)
        audit.record(conn, user.id, "targets_saved", account=account, targets=clean)
        return {"account": account, "targets": db.get_target_weights(conn, user.id, account)}

    @router.get("/rebalance")
    def rebalance_plan(account: str = "manual", band: float = Query(5.0, ge=0.5, le=50),
                       min_trade: float = Query(50.0, ge=0), user=Depends(auth.get_current_user)):
        account = _account(account)
        targets = db.get_target_weights(conn, user.id, account)
        held = {h.ticker: h for h in db.get_portfolio(conn, user.id, account)}
        tickers = sorted(set(held) | set(targets))
        ccy = {t: held[t].currency if t in held else currency.currency_for_symbol(t) or "USD" for t in tickers}
        cash = db.get_cash(conn, user.id, account)
        base = db.get_notify_profile(conn, user.id).base_currency
        fx = currency.rates_to_base(base, [*ccy.values(), *(c.currency for c in cash)])["rates"]
        if any(fx.get(c.currency) is None for c in cash):
            _fields("No exchange rate for some of the cash", {"cash": "an exchange rate is unavailable"})
        prices = {t: p for t, (p, _) in quotes.latest_prices(conn, tickers).items()}
        try:
            out = rebalance.plan({t: h.shares for t, h in held.items()}, prices, fx, ccy,
                                 sum(c.balance * fx[c.currency] for c in cash), targets, band, min_trade)
        except ValueError as exc:
            _fields(str(exc), {"targets": str(exc)})
        return {"account": account, "base_currency": base, "targets": targets,
                "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"), **out}

    return router
