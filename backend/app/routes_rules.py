"""The rule builder's API (STC-61): the schema, saved rules and backtests.

A backtest runs on the ticker's full adjusted history (daily_max), fetched
on demand when it isn't stored yet, else on the stored 2-year daily bars —
and says which. Every distinct variant of a rule you test is counted, so the
number of tries is always next to the result.
"""
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError

from app import audit, auth, bar_backtest, db, rule_source, rules
from app.models import SavedRule
from app.sources import ohlc as ohlc_source
from app.validation import clean_ticker


class RuleBody(BaseModel):
    spec: dict
    active: bool = True


class BacktestBody(BaseModel):
    spec: dict
    ticker: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _spec(raw: dict) -> rules.RuleSpec:
    try:
        return rules.RuleSpec.model_validate(raw)
    except ValidationError as exc:
        errors = {".".join(str(p) for p in e["loc"]) or "spec": e["msg"].removeprefix("Value error, ")
                  for e in exc.errors()}
        raise HTTPException(status_code=422, detail={"message": "The rule needs attention", "errors": errors}) from None


def history_bars(conn, ticker: str) -> tuple[list, str]:
    """(daily bars, which) for a backtest or a replay: the full adjusted
    history, fetched on demand when it isn't stored, else the stored 2 years."""
    bars = db.get_ohlc(conn, ticker, "daily_max")
    if not bars:
        fetched = ohlc_source.fetch([ticker], ohlc_source.MAX_SERIES)
        if fetched:
            db.upsert_ohlc(conn, fetched)
            bars = db.get_ohlc(conn, ticker, "daily_max")
    if bars:
        return bars, "full adjusted history"
    return db.get_ohlc(conn, ticker, "daily"), "the stored 2 years of daily bars (full history unavailable)"


def _out(r: SavedRule) -> dict:
    return {**r.model_dump(exclude={"spec"}), "spec": rules.RuleSpec.model_validate_json(r.spec).model_dump()}


def _store(conn, r: SavedRule) -> SavedRule:
    try:
        return db.save_rule(conn, r)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=422, detail={
            "message": "The rule needs attention",
            "errors": {"name": f"you already have a rule named '{r.name}'"}}) from None


def build_router(conn) -> APIRouter:
    router = APIRouter(prefix="/api/rules")

    def own(rule_id: int, user_id: int) -> SavedRule:
        r = db.get_saved_rule(conn, rule_id)
        if r is None or r.user_id != user_id:
            raise HTTPException(status_code=404, detail="no such rule")
        return r

    @router.get("/schema")
    def schema(user=Depends(auth.get_current_user)):
        return {
            "indicators": [{"name": k, "label": rules.LABELS[k], "period": d, "bounds": b}
                           for k, (d, b) in rules.INDICATORS.items()],
            "ops": rules.OPS, "max_conditions": rules.MAX_CONDITIONS,
            "min_trades": bar_backtest.MIN_TRADES, "in_sample": bar_backtest.IN_SAMPLE,
        }

    @router.get("")
    def list_rules(user=Depends(auth.get_current_user)):
        return {"rules": [_out(r) for r in db.get_saved_rules(conn, user.id)]}

    @router.post("")
    def create(body: RuleBody, user=Depends(auth.get_current_user)):
        spec = _spec(body.spec)
        now = _now()
        r = _store(conn, SavedRule(user_id=user.id, name=spec.name, spec=spec.model_dump_json(),
                                         model_version=rules.version(spec), active=body.active,
                                         created_at=now, updated_at=now))
        audit.record(conn, user.id, "rule_saved", rule_id=r.id, name=r.name, model_version=r.model_version)
        return _out(r)

    @router.put("/{rule_id}")
    def update(rule_id: int, body: RuleBody, user=Depends(auth.get_current_user)):
        old = own(rule_id, user.id)
        spec = _spec(body.spec)
        r = _store(conn, old.model_copy(update={
            "name": spec.name, "spec": spec.model_dump_json(), "model_version": rules.version(spec),
            "active": body.active, "updated_at": _now()}))
        audit.record(conn, user.id, "rule_saved", rule_id=r.id, name=r.name, model_version=r.model_version)
        return _out(r)

    @router.delete("/{rule_id}")
    def delete(rule_id: int, user=Depends(auth.get_current_user)):
        own(rule_id, user.id)
        db.delete_saved_rule(conn, user.id, rule_id)
        audit.record(conn, user.id, "rule_deleted", rule_id=rule_id)
        return {"deleted": rule_id}

    @router.post("/backtest")
    def backtest(body: BacktestBody, user=Depends(auth.get_current_user)):
        spec = _spec(body.spec)
        t = clean_ticker(body.ticker)
        bars, source = history_bars(conn, t)
        if len(bars) < 60:
            raise HTTPException(status_code=422, detail={
                "message": f"Not enough history for {t} to backtest", "errors": {"ticker": "needs 60+ daily bars"}})
        variants = db.note_variant(conn, user.id, spec.name, rule_source.spec_hash(spec), _now())
        return {"ticker": t, "data": source, "variants_tested": variants, "model_version": rules.version(spec),
                **bar_backtest.run(bars, spec)}

    return router
