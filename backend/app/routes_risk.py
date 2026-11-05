"""Exit rules on holdings and the account's risk switches (STC-9).

- `/api/portfolio/rules` lists a book's rules with a suggested stop for every
  holding that has none; `/api/portfolio/{ticker}/rules` reads, saves and
  deletes one. Rules belong to a held position, so saving one for a ticker
  you don't hold is refused.
- `/api/risk/*` is the drawdown kill switch (its limit, its state and the
  acknowledge that re-enables new entries after a trip) and the regime gate.
- `/api/automute` lists each Boom component's recent hit rate and mute state
  with this user's override; `/api/automute/{component}` sets the override.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app import audit, auth, automute, config, db, exits, notify, quotes, risk
from app.models import PositionRule
from app.sources.boom_score import WEIGHTS
from app.validation import clean_ticker, validate_exit_rule

RULE_ACCOUNTS = ("manual", "paper")


class RuleBody(BaseModel):
    stop_price: float | str | None = None
    stop_pct: float | str | None = None
    take_profit: float | str | None = None
    trailing_pct: float | str | None = None
    active: bool = True


class RiskSettingsBody(BaseModel):
    """Only the fields sent are changed."""
    max_drawdown_pct: float | None = None
    regime_gate: str | None = None
    max_order_notional: float | None = None
    max_orders_per_day: int | None = None
    max_notional_per_day: float | None = None
    max_position_pct: float | None = None
    allow_outside_hours: bool | None = None
    require_stop: bool | None = None
    max_loss_per_trade: float | None = None
    max_daily_loss: float | None = None
    min_reward_risk: float | None = None
    no_entry_after_open_min: int | None = None
    no_entry_before_close_min: int | None = None


class KillBody(BaseModel):
    reason: str = ""


class StepUpBody(BaseModel):
    code: str


class OverrideBody(BaseModel):
    mode: str   # auto | on | off


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _account(account: str) -> str:
    if account == "live":
        raise HTTPException(status_code=409, detail="no live broker configured")
    if account not in RULE_ACCOUNTS:
        raise HTTPException(status_code=400, detail="account must be 'manual' or 'paper'")
    return account


def _view(conn, ticker: str, rule: PositionRule | None, holding,
          price: tuple[float, str] | None) -> dict:
    px = price[0] if price else None
    out = {
        "ticker": ticker,
        "held": holding is not None,
        "shares": holding.shares if holding else None,
        "avg_cost": holding.avg_cost if holding else None,
        "currency": holding.currency if holding else None,
        "price": px,
        "price_source": price[1] if price else None,
        "rule": rule.model_dump() if rule else None,
        "levels": None,
        "suggested": None,
    }
    if rule and holding:
        out["levels"] = {"stop": exits.stop_level(rule, holding.avg_cost),
                         "take_profit": rule.take_profit, "trailing": exits.trail_level(rule)}
    if holding and not (rule and exits.stop_level(rule, holding.avg_cost) is not None):
        out["suggested"] = exits.suggested_stop(db.get_analysis(conn, holding.ticker), px)
    return out


def build_router(conn) -> APIRouter:
    router = APIRouter()

    @router.get("/api/portfolio/rules")
    def list_rules(account: str = "manual", user=Depends(auth.get_current_user)):
        acct = _account(account)
        holdings = {h.ticker: h for h in db.get_portfolio(conn, user.id, acct)}
        rules = {r.ticker: r for r in db.get_position_rules(conn, user.id, acct)}
        tickers = sorted(set(holdings) | set(rules))
        prices = quotes.latest_prices(conn, sorted(holdings))
        return {"account": acct, "items": [
            _view(conn, t, rules.get(t), holdings.get(t), prices.get(t)) for t in tickers]}

    @router.get("/api/portfolio/{ticker}/rules")
    def get_rule(ticker: str, account: str = "manual", user=Depends(auth.get_current_user)):
        t, acct = clean_ticker(ticker), _account(account)
        holding = next((h for h in db.get_portfolio(conn, user.id, acct) if h.ticker == t), None)
        return _view(conn, t, db.get_position_rule(conn, user.id, acct, t), holding,
                     quotes.latest_prices(conn, [t]).get(t) if holding else None)

    @router.put("/api/portfolio/{ticker}/rules")
    def put_rule(ticker: str, body: RuleBody, account: str = "manual",
                 user=Depends(auth.get_current_user)):
        t, acct = clean_ticker(ticker), _account(account)
        holding = next((h for h in db.get_portfolio(conn, user.id, acct) if h.ticker == t), None)
        if holding is None:
            raise HTTPException(status_code=409, detail=f"you don't hold {t} in this account")
        price = quotes.latest_prices(conn, [t]).get(t) or (holding.avg_cost, "your average cost")
        clean, errors = validate_exit_rule(body.model_dump(), price[0], holding.avg_cost)
        if errors:
            raise HTTPException(status_code=422, detail={
                "message": "Some fields need attention", "errors": errors})
        existing = db.get_position_rule(conn, user.id, acct, t)
        now = _now()
        # A trail that stays on keeps its high; a new one starts at today's price.
        trail_high = None
        if clean["trailing_pct"] is not None:
            kept = existing.trail_high if existing and existing.trailing_pct is not None else None
            trail_high = max(kept or 0, price[0])
        rule = db.upsert_position_rule(conn, PositionRule(
            user_id=user.id, account=acct, ticker=t, trail_high=trail_high,
            created_at=existing.created_at if existing else now, updated_at=now, **clean))
        audit.record(conn, user.id, "exit_rule_saved", account=acct, ticker=t, **clean)
        return _view(conn, t, rule, holding, price)

    @router.delete("/api/portfolio/{ticker}/rules")
    def delete_rule(ticker: str, account: str = "manual", user=Depends(auth.get_current_user)):
        t, acct = clean_ticker(ticker), _account(account)
        if not db.delete_position_rule(conn, user.id, acct, t):
            raise HTTPException(status_code=404, detail="no rule for that ticker")
        audit.record(conn, user.id, "exit_rule_deleted", account=acct, ticker=t)
        return {"deleted": t}

    @router.get("/api/risk/status")
    def risk_status(user=Depends(auth.get_current_user)):
        return risk.status(conn, user.id)

    @router.put("/api/risk/settings")
    def put_risk_settings(body: RiskSettingsBody, user=Depends(auth.get_current_user)):
        sent = body.model_fields_set
        pct = body.max_drawdown_pct
        lo, hi = risk.MAX_DRAWDOWN_RANGE
        errors = {}
        if "max_drawdown_pct" in sent and pct is not None and not lo <= pct <= hi:
            errors["max_drawdown_pct"] = f"Between {lo:g}% and {hi:g}%, or off"
        if "regime_gate" in sent and body.regime_gate not in risk.REGIME_GATES:
            errors["regime_gate"] = f"One of: {', '.join(risk.REGIME_GATES)}"
        limits = {}
        for field, bounds in risk.GUARDRAIL_FIELDS.items():
            if field not in sent:
                continue
            v = getattr(body, field)
            if bounds is None:
                if v is not None:
                    limits[field] = bool(v)
            elif v is None or not bounds[0] <= v <= bounds[1]:
                errors[field] = f"Between {bounds[0]:,g} and {bounds[1]:,g}"
            else:
                limits[field] = v
        for field, (lo, hi) in risk.PER_TRADE_FIELDS.items():
            if field not in sent:
                continue
            v = getattr(body, field)
            if v is not None and not lo <= v <= hi:
                errors[field] = f"Between {lo:,g} and {hi:,g}, or empty for off"
            else:
                limits[field] = v
        if errors:
            raise HTTPException(status_code=422, detail={
                "message": "Some fields need attention", "errors": errors})
        if "max_drawdown_pct" in sent:
            risk.set_max_drawdown(conn, user.id, pct)
        if "regime_gate" in sent:
            risk.set_regime_gate(conn, user.id, body.regime_gate)
        if limits:
            s = db.get_risk_settings(conn, user.id)
            db.upsert_risk_settings(conn, s.model_copy(update={**limits, "updated_at": _now()}))
        audit.record(conn, user.id, "risk_settings_changed",
                     **{k: getattr(body, k) for k in sent})
        # A limit already exceeded trips now rather than at tonight's snapshot.
        alert = risk.check_user(conn, user.id)
        if alert:
            notify.push_stored(conn, [alert])
        return risk.status(conn, user.id)

    @router.post("/api/risk/drawdown/acknowledge")
    def acknowledge(user=Depends(auth.get_current_user)):
        if not risk.acknowledge(conn, user.id):
            raise HTTPException(status_code=409, detail="the kill switch isn't tripped")
        audit.record(conn, user.id, "drawdown_acknowledged")
        return risk.status(conn, user.id)

    @router.post("/api/risk/kill-switch")
    def kill_switch(body: KillBody, user=Depends(auth.get_current_user)):
        if db.get_risk_settings(conn, user.id).trading_halted_at:
            raise HTTPException(status_code=409, detail="trading is already halted")
        risk.halt_trading(conn, user.id, body.reason.strip()[:200])
        return risk.status(conn, user.id)

    @router.post("/api/risk/kill-switch/release")
    def release(body: StepUpBody, user=Depends(auth.get_current_user)):
        if not db.get_risk_settings(conn, user.id).trading_halted_at:
            raise HTTPException(status_code=409, detail="trading isn't halted")
        if not auth.verify_step_up(user, body.code):
            audit.record(conn, user.id, "step_up_failed", action="kill_switch_release")
            raise HTTPException(status_code=422, detail={
                "message": "That code didn't work", "errors": {"code": "Enter the current 6-digit code"}})
        risk.release_trading(conn, user.id)
        return risk.status(conn, user.id)

    @router.get("/api/audit")
    def audit_log(limit: int = 200, user=Depends(auth.get_current_user)):
        rows = db.get_audit(conn, user.id, max(1, min(limit, 2000)))
        return {"entries": [e.model_dump() for e in rows], "verify": audit.verify(conn, user.id)}

    @router.get("/api/audit.csv", response_class=PlainTextResponse)
    def audit_csv(user=Depends(auth.get_current_user)):
        return PlainTextResponse(audit.to_csv(conn, user.id), media_type="text/csv", headers={
            "Content-Disposition": 'attachment; filename="audit-log.csv"'})

    @router.get("/api/server/audit/verify")
    def audit_verify_all(user=Depends(auth.get_current_user)):
        if not user.is_admin:
            raise HTTPException(status_code=403, detail="admin only")
        return {"chains": [{"user_id": uid, **audit.verify(conn, uid)} for uid in db.get_audit_user_ids(conn)]}

    @router.get("/api/automute")
    def get_automute(user=Depends(auth.get_current_user)):
        states = {m.component: m for m in db.get_signal_mutes(conn)}
        overrides = db.get_mute_overrides(conn, user.id)
        effective = automute.muted_for(conn, user.id)
        perf = db.get_signal_performance(conn)
        items = [{
            "component": c, "label": automute.label(c), "weight": WEIGHTS[c],
            "auto_muted": states[c].muted if c in states else False,
            "since": states[c].since if c in states else None,
            "hit_rate": states[c].hit_rate if c in states else None,
            "n": states[c].n if c in states else 0,
            "reason": states[c].reason if c in states else "not measured yet",
            "override": overrides.get(c, "auto"),
            "counts": c not in effective,
        } for c in sorted(WEIGHTS)]
        return {"items": items, "performance": [p.model_dump() for p in perf], "rules": {
            "window_days": config.SIGNAL_WINDOW_DAYS, "mute_below": config.SIGNAL_MUTE_BELOW,
            "unmute_at": config.SIGNAL_MUTE_BELOW + config.SIGNAL_UNMUTE_MARGIN,
            "min_n": config.SIGNAL_MUTE_MIN_N, "horizon_days": automute.PRIMARY_HORIZON}}

    @router.put("/api/automute/{component}")
    def put_override(component: str, body: OverrideBody, user=Depends(auth.get_current_user)):
        if component not in WEIGHTS:
            raise HTTPException(status_code=404, detail="no such signal")
        if body.mode not in ("auto", *automute.OVERRIDE_MODES):
            raise HTTPException(status_code=422, detail={
                "message": "Some fields need attention", "errors": {"mode": "auto, on or off"}})
        db.set_mute_override(conn, user.id, component, None if body.mode == "auto" else body.mode)
        return get_automute(user)

    return router
