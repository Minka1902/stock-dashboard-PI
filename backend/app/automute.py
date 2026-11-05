"""Per-signal performance and auto-mute (STC-28, STC-29).

Each Boom Score component is measured on its own firings: for every (ticker,
day) it fired, the net-of-cost return over the next 7 and 30 days, in the
component's direction (bullish = long, bearish = short). The rolling window is
the SIGNAL_WINDOW_DAYS of firings whose horizon has fully elapsed, so every
firing counted has an outcome.

A component whose 7-day hit rate drops below SIGNAL_MUTE_BELOW on at least
SIGNAL_MUTE_MIN_N outcomes is muted: its weight counts as zero. It counts
again only once the hit rate is back above SIGNAL_MUTE_BELOW +
SIGNAL_UNMUTE_MARGIN (hysteresis, so a signal sitting on the line doesn't
flicker). Too few outcomes never changes a state either way.

Mutes are shared; each user can override one (always count it / never count
it). The scores everyone sees use the shared mute set; a user's suggestions
use their own. Mutes don't change the model version — the set in force is
stored on each suggestion row instead.
"""
import json
from datetime import date, datetime, timedelta, timezone

from app import config, costs, db
from app.models import BoomScore, SignalMute, SignalPerformance
from app.sources import boom_score

HORIZONS = (7, 30)
PRIMARY_HORIZON = 7
OVERRIDE_MODES = ("on", "off")
_ACRONYMS = {"wsb": "WSB", "rsi": "RSI", "macd": "MACD", "vix": "VIX", "aaii": "AAII", "52w": "52w"}


def label(component: str) -> str:
    """'wsb_rising' → 'WSB rising'."""
    words = component.split("_")
    out = [_ACRONYMS.get(w, w) for w in words]
    if out[0] == words[0]:
        out[0] = out[0].capitalize()
    return " ".join(out)


def _close_on_or_after(closes: list[tuple[str, float]], target: str) -> float | None:
    for d, c in closes:
        if d >= target:
            return c
    return None


def performance(conn, today: date | None = None) -> list[SignalPerformance]:
    """Rolling per-component outcomes at each horizon (the derived-step fetch)."""
    today = today or date.today()
    window = config.SIGNAL_WINDOW_DAYS
    since = (today - timedelta(days=window + max(HORIZONS))).isoformat()
    firings = db.get_boom_component_history(conn, since)
    closes: dict[str, list[tuple[str, float]]] = {}
    acc: dict[tuple[str, int], list[float]] = {}
    for f in firings:
        t = f["ticker"]
        if t not in closes:
            closes[t] = [(b.date, b.close) for b in db.get_ohlc(conn, t, "daily") if b.close]
        for h in HORIZONS:
            end = today - timedelta(days=h)
            start = end - timedelta(days=window)
            if not start.isoformat() <= f["day"] <= end.isoformat():
                continue
            entry = _close_on_or_after(closes[t], f["day"])
            exit_ = _close_on_or_after(closes[t], (date.fromisoformat(f["day"]) + timedelta(days=h)).isoformat())
            if entry and exit_:
                direction = "long" if f["weight"] > 0 else "short"
                acc.setdefault((f["component"], h), []).append(costs.round_trip(entry, exit_, direction))
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out = []
    for (comp, h), rets in sorted(acc.items()):
        hits = sum(1 for r in rets if r > 0)
        out.append(SignalPerformance(
            component=comp, horizon_days=h, window_days=window, n=len(rets), hits=hits,
            hit_rate=round(hits / len(rets) * 100, 1), mean_net=round(sum(rets) / len(rets), 2),
            computed_at=at))
    return out


def next_mutes(perf: list[SignalPerformance], current: dict[str, SignalMute],
               now: str) -> list[SignalMute]:
    """The mute state after this run's performance, with hysteresis. Pure."""
    c = config
    out = []
    primary = {p.component: p for p in perf if p.horizon_days == PRIMARY_HORIZON}
    for comp in sorted(set(primary) | set(current)):
        p, prev = primary.get(comp), current.get(comp)
        muted = prev.muted if prev else False
        enough = p is not None and p.n >= c.SIGNAL_MUTE_MIN_N
        if enough and not muted and p.hit_rate < c.SIGNAL_MUTE_BELOW:
            muted = True
        elif enough and muted and p.hit_rate >= c.SIGNAL_MUTE_BELOW + c.SIGNAL_UNMUTE_MARGIN:
            muted = False
        since = (prev.since if prev and prev.muted == muted else now) if muted else None
        if p is None:
            reason = f"{label(comp)}: no scored firings in the last {c.SIGNAL_WINDOW_DAYS}d"
        else:
            reason = (f"{label(comp)} {'muted' if muted else 'counting'}: {p.hit_rate:g}% hit rate "
                      f"over {c.SIGNAL_WINDOW_DAYS}d, n={p.n}")
            if not enough:
                reason += f" (needs n≥{c.SIGNAL_MUTE_MIN_N} to change state)"
        out.append(SignalMute(component=comp, muted=muted, since=since,
                              hit_rate=p.hit_rate if p else None, n=p.n if p else 0,
                              reason=reason, updated_at=now))
    return out


def store(conn, perf: list[SignalPerformance]) -> None:
    """The derived-step store: the performance table, then the mute states."""
    db.replace_signal_performance(conn, perf)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    current = {m.component: m for m in db.get_signal_mutes(conn)}
    db.upsert_signal_mutes(conn, next_mutes(perf, current, now))


def global_mutes(conn) -> frozenset[str]:
    return frozenset(m.component for m in db.get_signal_mutes(conn) if m.muted)


def muted_for(conn, user_id: int) -> frozenset[str]:
    """The shared mute set with this user's overrides applied."""
    overrides = db.get_mute_overrides(conn, user_id)
    base = {c for c in global_mutes(conn) if overrides.get(c) != "on"}
    return frozenset(base | {c for c, m in overrides.items() if m == "off"})


def apply_mutes(b: BoomScore, muted: frozenset[str]) -> BoomScore:
    """`b` as it scores with exactly `muted` excluded: the score is recomputed
    from the stored component points (renormalized for TASE as at scoring time)
    and a muted component's flag reads False."""
    points = json.loads(b.components or "{}")
    fired_muted = sorted(k for k in points if k in muted)
    if fired_muted == json.loads(b.muted or "[]"):
        return b
    kept = {k: v for k, v in points.items() if k not in muted}
    score, _ = boom_score.renormalize(kept, boom_score.not_applicable_for(b.ticker))
    flags = {k: False for k in fired_muted if k in BoomScore.model_fields}
    restored = {k: True for k in json.loads(b.muted or "[]") if k not in muted and k in BoomScore.model_fields}
    return b.model_copy(update={"score": score, "muted": json.dumps(fired_muted), **restored, **flags})
