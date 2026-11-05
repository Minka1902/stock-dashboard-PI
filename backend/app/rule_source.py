"""Saved rules as a live signal source (STC-67).

In the derived step, every active saved rule is evaluated on the owner's
watched and held tickers, on **completed** daily bars only (today's bar is
still forming). A rule firing on the latest completed bar is a RuleSignal —
"entry", or "exit" on a ticker you hold — tracked under the rule's own model
version (rules.version), and alerts its owner once.
"""
import json
from datetime import datetime, timezone

from app import db, notify, rules
from app.models import Alert, RuleSignal


def fetch(conn, now: datetime | None = None) -> list[RuleSignal]:
    now = now or datetime.now(timezone.utc)
    today = now.date().isoformat()
    at = now.isoformat(timespec="seconds")
    out: list[RuleSignal] = []
    by_user: dict[int, tuple[set[str], list[str]]] = {}
    by_ticker: dict[str, tuple[str, dict] | None] = {}
    for rule in db.get_saved_rules(conn, active_only=True):
        spec = rules.RuleSpec.model_validate_json(rule.spec)
        if rule.user_id not in by_user:
            held = {h.ticker for h in db.get_portfolio(conn, rule.user_id)}
            by_user[rule.user_id] = held, sorted(held | set(db.get_watched_tickers_for_user(conn, rule.user_id)))
        held, universe = by_user[rule.user_id]
        for t in universe:
            if t not in by_ticker:
                bars = [b for b in db.get_ohlc(conn, t, "daily") if b.date < today]
                by_ticker[t] = (bars[-1].date, rules.arrays(bars)) if len(bars) >= 2 else None
            if by_ticker[t] is None:
                continue
            day, arr = by_ticker[t]
            fired = [("entry", rules.evaluate(spec.entry, arr)[-1])]
            if spec.exit is not None and t in held:
                fired.append(("exit", rules.evaluate(spec.exit, arr)[-1]))
            out += [RuleSignal(user_id=rule.user_id, rule_id=rule.id, ticker=t, day=day, kind=k,
                               model_version=rule.model_version, computed_at=at) for k, hit in fired if hit]
    return out


def store(conn, signals: list[RuleSignal]) -> None:
    """Keep the signals, and alert each one's owner the first time."""
    db.upsert_rule_signals(conn, signals)
    names = {r.id: r.name for r in db.get_saved_rules(conn)}
    new = []
    for s in signals:
        key = f"rule|{s.ticker}|{s.user_id}|{s.rule_id}|{s.day}|{s.kind}"
        if db.alert_exists(conn, key):
            continue
        verb = "entry" if s.kind == "entry" else "exit"
        new.append(Alert(
            dedup_key=key, created_at=s.computed_at, ticker=s.ticker, type="rule", severity="medium",
            title=f"Rule {verb}: {names.get(s.rule_id, s.rule_id)}",
            message=(f"Your rule \"{names.get(s.rule_id, s.rule_id)}\" signalled an {verb} on {s.ticker} at the "
                     f"{s.day} close ({s.model_version}). A signal, not an order."),
            user_id=s.user_id))
    db.upsert_alerts(conn, new)
    notify.push_stored(conn, [a for a in new if a.severity == "high"])


def latest_for_user(conn, user_id: int, since: str) -> list[dict]:
    """Recent signals with their rule names, newest first — for the digest."""
    names = {r.id: r.name for r in db.get_saved_rules(conn, user_id)}
    return [{**s.model_dump(), "rule": names.get(s.rule_id, str(s.rule_id))}
            for s in db.get_rule_signals(conn, user_id, since) if s.rule_id in names]


def spec_hash(spec: rules.RuleSpec) -> str:
    return rules.version(spec).split(":", 1)[1]


def parse(raw: str | dict) -> rules.RuleSpec:
    return rules.RuleSpec.model_validate(json.loads(raw) if isinstance(raw, str) else raw)
