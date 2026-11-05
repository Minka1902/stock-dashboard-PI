"""Account-level risk switches that pause new entries (STC-14, STC-27).

The drawdown kill switch measures the manual account's time-weighted equity
(analytics.twr_index over equity_snapshots) against its high-water mark. Bots
and paper trade simulated money, so only the real book is measured; bot
allocations are external flows to it and never look like a loss.

A trip holds until the user acknowledges it. Acknowledging resets the high to
the snapshots from that day on, so a drawdown that is still there doesn't trip
again the next evening. While tripped, new-buy suggestions and new bot entries
are paused; sells, exits and stops carry on. Nothing is ever sold for you.

The regime gate (risk_settings.regime_gate) acts only on a risk-off market
regime (regime.py): "downgrade" turns new buys into watch with the reason,
"pause" suppresses them like the kill switch, "off" ignores the regime. An
unknown regime gates nothing. Bots can't "watch", so either gate holds their
new entries.
"""
from datetime import datetime, timezone

from app import analytics, audit, db, execution, regime
from app.models import Alert, RiskSettings

MAX_DRAWDOWN_RANGE = (1.0, 90.0)
REGIME_GATES = ("off", "downgrade", "pause")
# Editable guardrail limits and their allowed ranges (STC-50).
GUARDRAIL_FIELDS = {
    "max_order_notional": (100.0, 10_000_000.0),
    "max_orders_per_day": (1, 1_000),
    "max_notional_per_day": (100.0, 50_000_000.0),
    "max_position_pct": (1.0, 100.0),
    "allow_outside_hours": None,
    "require_stop": None,
}
# Per-trade limits (STC-82): the same ranges, but null switches one off.
PER_TRADE_FIELDS = {
    "max_loss_per_trade": (1.0, 10_000_000.0),
    "max_daily_loss": (1.0, 50_000_000.0),
    "min_reward_risk": (0.1, 20.0),
    "no_entry_after_open_min": (0, 300),
    "no_entry_before_close_min": (0, 300),
}
_ACCOUNT = "manual"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_tripped(s: RiskSettings) -> bool:
    return s.drawdown_tripped_at is not None and (
        s.drawdown_acknowledged_at is None or s.drawdown_acknowledged_at < s.drawdown_tripped_at)


def current_drawdown(conn, user_id: int, since: str | None = None) -> dict | None:
    """The manual account's drawdown now, from the high since `since` (a date)."""
    snaps = analytics.current_base_run(db.get_equity_snapshots(conn, user_id, _ACCOUNT, since))
    out = analytics.drawdown_from_peak(analytics.twr_index(snaps))
    if out is not None:
        out["base_currency"] = snaps[-1].base_currency
    return out


def _since(s: RiskSettings) -> str | None:
    return s.drawdown_acknowledged_at[:10] if s.drawdown_acknowledged_at else None


def check_user(conn, user_id: int, now: str | None = None) -> Alert | None:
    """Trip the user's kill switch if the drawdown has reached the limit.
    Returns the stored alert on a fresh trip, else None."""
    s = db.get_risk_settings(conn, user_id)
    if s.max_drawdown_pct is None or is_tripped(s):
        return None
    dd = current_drawdown(conn, user_id, _since(s))
    if dd is None or dd["pct"] < s.max_drawdown_pct:
        return None
    at = now or _now()
    db.upsert_risk_settings(conn, s.model_copy(update={
        "drawdown_tripped_at": at, "drawdown_pct": dd["pct"], "updated_at": at}))
    alert = Alert(
        dedup_key=f"risk||{user_id}|drawdown|{at}", created_at=at, ticker="", type="risk",
        severity="high", title="Drawdown kill switch tripped",
        message=(f"Your portfolio is {dd['pct']:.1f}% below its high of {dd['peak_date']} "
                 f"(your limit is {s.max_drawdown_pct:g}%). New-buy suggestions and new bot "
                 f"entries are paused until you acknowledge it on the Trading Bot page. "
                 f"Nothing is sold automatically."),
        user_id=user_id)
    db.upsert_alerts(conn, [alert])
    return alert


def check_drawdowns(conn, now: str | None = None) -> list[Alert]:
    """Run check_user for everyone with a limit set (after each equity snapshot)."""
    return [a for s in db.get_all_risk_settings(conn) if s.max_drawdown_pct is not None
            for a in [check_user(conn, s.user_id, now)] if a is not None]


def acknowledge(conn, user_id: int, now: str | None = None) -> bool:
    """Re-enable entries after a trip. False when nothing is tripped."""
    s = db.get_risk_settings(conn, user_id)
    if not is_tripped(s):
        return False
    at = now or _now()
    db.upsert_risk_settings(conn, s.model_copy(update={"drawdown_acknowledged_at": at, "updated_at": at}))
    return True


def set_max_drawdown(conn, user_id: int, pct: float | None) -> RiskSettings:
    s = db.get_risk_settings(conn, user_id)
    return db.upsert_risk_settings(conn, s.model_copy(update={"max_drawdown_pct": pct, "updated_at": _now()}))


def set_regime_gate(conn, user_id: int, gate: str) -> RiskSettings:
    s = db.get_risk_settings(conn, user_id)
    return db.upsert_risk_settings(conn, s.model_copy(update={"regime_gate": gate, "updated_at": _now()}))


def gate(conn, user_id: int, current_regime: dict | None = None) -> dict:
    """{halted: [reasons new buys are suppressed], downgrade: reason | None}."""
    s = db.get_risk_settings(conn, user_id)
    halted, downgrade = [], None
    if s.trading_halted_at:
        halted.append(f"Kill switch: all trading halted ({s.trading_halt_reason})")
    if is_tripped(s):
        halted.append(f"Drawdown kill switch: {s.drawdown_pct:.1f}% below the high "
                      f"(limit {s.max_drawdown_pct:g}%)")
    reg = current_regime if current_regime is not None else regime.latest(conn)
    if reg["label"] == "risk_off" and s.regime_gate != "off":
        why = f"Risk-off market regime (score {reg['score']:+d} on {reg['date']})"
        if s.regime_gate == "pause":
            halted.append(why)
        else:
            downgrade = why
    return {"halted": halted, "downgrade": downgrade}


def halt_trading(conn, user_id: int, reason: str, now: str | None = None) -> dict:
    """The global kill switch (STC-53): cancel every open paper order, pause
    every running bot, and refuse every new order until released with a
    fresh authenticator code. Nothing is sold."""
    at = now or _now()
    s = db.get_risk_settings(conn, user_id)
    cancelled = [o.id for o in db.get_orders(conn, user_id, "paper", ("submitted",), limit=10_000)
                 if db.set_order_status(conn, o.id, "cancelled", "kill switch")]
    cancelled += execution.cancel_broker_orders(conn, user_id)
    paused = [b.id for b in db.get_bots(conn, user_id, "active")
              if db.set_bot_status(conn, b.id, "paused", ("active",))]
    db.upsert_risk_settings(conn, s.model_copy(update={
        "trading_halted_at": at, "trading_halt_reason": reason or "kill switch", "updated_at": at}))
    audit.record(conn, user_id, "kill_switch_on", reason=reason, cancelled_orders=cancelled, paused_bots=paused)
    db.upsert_alerts(conn, [Alert(
        dedup_key=f"risk||{user_id}|kill_switch|{at}", created_at=at, ticker="", type="risk", severity="high",
        title="Kill switch: trading halted",
        message=(f"{len(cancelled)} open paper order(s) cancelled and {len(paused)} bot(s) paused. Every new "
                 f"order is refused until you re-enable trading with an authenticator code."),
        user_id=user_id)])
    return {"cancelled_orders": cancelled, "paused_bots": paused}


def release_trading(conn, user_id: int) -> bool:
    """Lift the kill switch (the caller has checked a fresh TOTP). Bots stay
    paused — resuming each is a decision of its own."""
    s = db.get_risk_settings(conn, user_id)
    if not s.trading_halted_at:
        return False
    db.upsert_risk_settings(conn, s.model_copy(update={
        "trading_halted_at": None, "trading_halt_reason": None, "updated_at": _now()}))
    audit.record(conn, user_id, "kill_switch_off", halted_since=s.trading_halted_at)
    return True


def halt_reasons(conn, user_id: int) -> list[str]:
    """Why new-buy suggestions are suppressed for this user right now."""
    return gate(conn, user_id)["halted"]


def entry_blockers(conn, user_id: int, current_regime: dict | None = None) -> list[str]:
    """Why a bot may not open a position right now: everything that halts
    suggestions, plus a downgrade (a bot has no 'watch')."""
    g = gate(conn, user_id, current_regime)
    return g["halted"] + ([g["downgrade"]] if g["downgrade"] else [])


def status(conn, user_id: int) -> dict:
    """Everything the Risk tab and the banner show."""
    s = db.get_risk_settings(conn, user_id)
    reg = regime.latest(conn)
    g = gate(conn, user_id, reg)
    reasons = g["halted"]
    return {
        "halted": bool(reasons),
        "reasons": reasons,
        "downgrade": g["downgrade"],
        "regime": reg,
        "regime_gate": s.regime_gate,
        "kill_switch": {"halted_at": s.trading_halted_at, "reason": s.trading_halt_reason},
        "guardrails": {k: getattr(s, k) for k in (*GUARDRAIL_FIELDS, *PER_TRADE_FIELDS)},
        "regime_gates": REGIME_GATES,
        "drawdown": {
            "max_pct": s.max_drawdown_pct,
            "tripped": is_tripped(s),
            "tripped_at": s.drawdown_tripped_at,
            "tripped_pct": s.drawdown_pct,
            "acknowledged_at": s.drawdown_acknowledged_at,
            "current": current_drawdown(conn, user_id, _since(s)),
            "range": MAX_DRAWDOWN_RANGE,
        },
    }
