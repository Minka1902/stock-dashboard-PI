"""Market regime (STC-26): risk-on / neutral / risk-off / unknown.

A transparent vote, not a model. Each input that is available and fresh votes
+1 (risk-on), −1 (risk-off) or 0, and states why:

- VIX level, and its move over the last 5 sessions
- CNN Fear & Greed
- the 5-day put/call ratio
- the 10y−2y Treasury spread (inverted is risk-off)
- SPY against its 200-day moving average

Fewer than config.REGIME_MIN_INPUTS available inputs is "unknown" — never a
guess — and an unknown regime gates nothing. Thresholds are all config. The
derived step stores one row per day (the latest computation wins).
"""
import json
from datetime import date, datetime, timedelta, timezone

from app import config, db, sentiment
from app.models import RegimeSnapshot
from app.sources.technical import compute_ma

LABELS = ("risk_on", "neutral", "risk_off", "unknown")
SPY = "SPY"


def _vote(on: bool, off: bool) -> int:
    return 1 if on else -1 if off else 0


def _fresh(as_of: str | None, today: date) -> bool:
    if not as_of:
        return False
    try:
        return (today - date.fromisoformat(as_of[:10])).days <= config.REGIME_MAX_INPUT_AGE_DAYS
    except ValueError:
        return False


def _input(key: str, label: str, value, vote, detail: str, as_of: str | None) -> dict:
    return {"key": key, "label": label, "value": value, "vote": vote, "detail": detail, "as_of": as_of}


def _missing(key: str, label: str, why: str, as_of: str | None = None) -> dict:
    return _input(key, label, None, None, why, as_of)


def gather(conn, today: date) -> list[dict]:
    """Every input with its value, vote (None = unavailable) and reason."""
    c = config
    ind = sentiment.build_summary(conn)["indicators"]
    out = []

    vix = ind["vix"]
    if vix["value"] is None or not _fresh(vix["as_of"], today):
        out.append(_missing("vix", "VIX level", "no recent VIX close", vix["as_of"]))
    else:
        v = vix["value"]
        out.append(_input("vix", "VIX level", v, _vote(v <= c.REGIME_VIX_RISK_ON, v >= c.REGIME_VIX_RISK_OFF),
                          f"{v:.1f} (risk-on ≤ {c.REGIME_VIX_RISK_ON:g}, risk-off ≥ {c.REGIME_VIX_RISK_OFF:g})",
                          vix["as_of"]))

    closes = db.get_vix(conn, days=21)
    if len(closes) >= 6 and _fresh(closes[-1].date, today):
        move = (closes[-1].close / closes[-6].close - 1) * 100
        t = c.REGIME_VIX_TREND_PCT
        out.append(_input("vix_trend", "VIX 5-session move", round(move, 1), _vote(move <= -t, move >= t),
                          f"{move:+.1f}% (rising {t:g}%+ is risk-off, falling {t:g}%+ risk-on)", closes[-1].date))
    else:
        out.append(_missing("vix_trend", "VIX 5-session move", "needs 6 recent VIX closes"))

    fg = ind["fear_greed"]
    if fg["value"] is None or not _fresh(fg["as_of"], today):
        out.append(_missing("fear_greed", "Fear & Greed", "no recent reading", fg["as_of"]))
    else:
        v = fg["value"]
        out.append(_input("fear_greed", "Fear & Greed", v, _vote(v >= c.REGIME_FG_RISK_ON, v <= c.REGIME_FG_RISK_OFF),
                          f"{v:.0f} (risk-off ≤ {c.REGIME_FG_RISK_OFF:g}, risk-on ≥ {c.REGIME_FG_RISK_ON:g})",
                          fg["as_of"]))

    pc = ind["put_call"]
    if pc["value"] is None or not _fresh(pc["as_of"], today):
        out.append(_missing("put_call", "Put/call ratio", "no recent reading", pc["as_of"]))
    else:
        v = pc["value"]
        out.append(_input("put_call", "Put/call ratio", v, _vote(v <= c.REGIME_PC_RISK_ON, v >= c.REGIME_PC_RISK_OFF),
                          f"{v:.2f} (risk-on ≤ {c.REGIME_PC_RISK_ON:g}, risk-off ≥ {c.REGIME_PC_RISK_OFF:g})",
                          pc["as_of"]))

    curve = [p for p in db.get_yield_curve(conn, days=30) if p.spread is not None]
    if curve and _fresh(curve[-1].date, today):
        v = curve[-1].spread
        out.append(_input("yield_curve", "10y−2y spread", v, _vote(v >= c.REGIME_CURVE_RISK_ON, v < 0),
                          f"{v:+.2f} pts ({'inverted' if v < 0 else 'normal'}; risk-on ≥ {c.REGIME_CURVE_RISK_ON:+g})",
                          curve[-1].date))
    else:
        out.append(_missing("yield_curve", "10y−2y spread", "no recent Treasury curve"))

    bars = db.get_ohlc(conn, SPY, "daily")
    ma = compute_ma([b.close for b in bars], c.REGIME_SPY_MA_DAYS) if bars else None
    if ma is not None and _fresh(bars[-1].date, today):
        px = bars[-1].close
        out.append(_input("spy_trend", f"SPY vs {c.REGIME_SPY_MA_DAYS}-day average", round((px / ma - 1) * 100, 2),
                          _vote(px > ma, px < ma),
                          f"{px:,.2f} is {'above' if px > ma else 'below'} the {c.REGIME_SPY_MA_DAYS}-day "
                          f"average of {ma:,.2f}", bars[-1].date))
    else:
        out.append(_missing("spy_trend", f"SPY vs {c.REGIME_SPY_MA_DAYS}-day average",
                            f"needs {c.REGIME_SPY_MA_DAYS} recent SPY closes"))
    return out


def classify(inputs: list[dict]) -> tuple[str, int, int]:
    """(label, score, available) from the votes. Pure."""
    votes = [i["vote"] for i in inputs if i["vote"] is not None]
    score = sum(votes)
    if len(votes) < config.REGIME_MIN_INPUTS:
        return "unknown", score, len(votes)
    if score >= config.REGIME_SCORE_THRESHOLD:
        return "risk_on", score, len(votes)
    if score <= -config.REGIME_SCORE_THRESHOLD:
        return "risk_off", score, len(votes)
    return "neutral", score, len(votes)


def compute(conn, now: datetime | None = None) -> list[RegimeSnapshot]:
    """The derived-step fetch: today's regime as one row."""
    now = now or datetime.now(timezone.utc)
    inputs = gather(conn, now.date())
    label, score, _ = classify(inputs)
    return [RegimeSnapshot(date=now.date().isoformat(), label=label, score=score,
                           inputs=json.dumps(inputs), computed_at=now.isoformat(timespec="seconds"))]


def latest(conn) -> dict:
    """The most recent stored regime, parsed, with the rules it was judged by.
    Unknown (with the reason) when nothing has been computed recently."""
    snap = db.get_latest_regime(conn)
    rules = {"min_inputs": config.REGIME_MIN_INPUTS, "score_threshold": config.REGIME_SCORE_THRESHOLD,
             "max_input_age_days": config.REGIME_MAX_INPUT_AGE_DAYS}
    stale = snap is None or (date.today() - date.fromisoformat(snap.date)) > timedelta(
        days=config.REGIME_MAX_INPUT_AGE_DAYS)
    if stale:
        return {"label": "unknown", "score": 0, "date": snap.date if snap else None, "inputs": [],
                "computed_at": snap.computed_at if snap else None, "rules": rules,
                "note": "not computed recently" if snap else "not computed yet"}
    return {"label": snap.label, "score": snap.score, "date": snap.date, "inputs": json.loads(snap.inputs),
            "computed_at": snap.computed_at, "rules": rules, "note": None}
