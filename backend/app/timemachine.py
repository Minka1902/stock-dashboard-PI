"""The Time Machine: replay a trade from the stock's own past days before
entering it.

Ported from trader-timemachine in Jake Schincariol's day-trader-skill (MIT,
github.com/Jakeschincariol/day-trader-skill; THIRD_PARTY_NOTICES.md),
adapted from the rest of one session in 5-minute bars to an N-day horizon in
daily bars, which is how this app's bots, exit rules and paper orders trade.

1. **The pool.** Each of the stock's last `LOOKBACK` completed days becomes
   four ratios to the day before's close: open, high, low, close.
2. **One replay.** Start at the entry. Each day of the horizon draws a past
   day at random and scales it to the price. The stop is checked first, then
   the target; a day touching both counts as the stop. A gap through a level
   fills at the open, so a gapped stop loses more than 1R, as in our bar
   backtester.
3. **10,000 replays**, seeded so a run can be repeated exactly. They give the
   odds of target first, stop first, or neither by the horizon, plus the
   average R and the price bands day by day.
4. **The history.** The same stop and target distances, run forward from
   every past close in the pool. That is what really happened.
5. **The verdict** is one line. Among replays that hit a side, the target
   must come first in 1 / (1 + R) of them to break even. Under 5 points away
   is close to break-even, 5–15 leans, 15+ is strongly.

A simulation from the stock's own past, not a prediction. The future can do
what those days never did.
"""
import numpy as np

from app.models import OHLCBar

LOOKBACK = 500
MIN_DAYS = 60
COIN_FLIP = 0.05
STRONG = 0.15
MANY_NEITHER = 0.40
BANDS = (5, 25, 50, 75, 95)


def pool(bars: list[OHLCBar]) -> np.ndarray:
    """(days, 4): open, high, low, close over the previous day's close."""
    a = np.array([[b.open, b.high, b.low, b.close] for b in bars[-(LOOKBACK + 1):]], dtype=float)
    return a[1:] / a[:-1, 3:4]


def _first(hit: np.ndarray) -> np.ndarray:
    """Index of the first True per row, or the row length when there's none."""
    return np.where(hit.any(axis=1), hit.argmax(axis=1), hit.shape[1])


def _outcomes(o, h, l, c, entry, stop, target):
    """Per path: outcome (2 target, 1 stop, 0 neither), exit price, day index."""
    n, horizon = c.shape
    s = _first(l <= stop[:, None] if np.ndim(stop) else l <= stop)
    t = _first(h >= target[:, None] if np.ndim(target) else h >= target) if target is not None \
        else np.full(n, horizon)
    rows = np.arange(n)
    stop_first = (s < horizon) & (s <= t)
    target_first = (t < horizon) & (t < s)
    day = np.where(stop_first, s, np.where(target_first, t, horizon - 1))
    at_open = o[rows, np.minimum(day, horizon - 1)]
    px = np.where(stop_first, np.minimum(at_open, stop),
                  np.where(target_first, np.maximum(at_open, target) if target is not None else 0.0, c[:, -1]))
    kind = np.where(stop_first, 1, np.where(target_first, 2, 0))
    return kind, px, day


def simulate(p: np.ndarray, entry: float, stop: float, target: float | None, horizon: int,
             replays: int = 10_000, seed: int = 19) -> dict:
    rng = np.random.default_rng(seed)
    r = p[rng.integers(0, len(p), size=(replays, horizon))]
    c = entry * np.cumprod(r[..., 3], axis=1)
    prev = np.concatenate([np.full((replays, 1), entry), c[:, :-1]], axis=1)
    o, h, l = prev * r[..., 0], prev * r[..., 1], prev * r[..., 2]
    kind, px, day = _outcomes(o, h, l, c, entry, stop, target)
    risk = entry - stop
    rs = (px - entry) / risk
    decided = kind > 0
    return {
        "replays": replays, "seed": seed,
        "p_target": round(float((kind == 2).mean()), 4), "p_stop": round(float((kind == 1).mean()), 4),
        "p_neither": round(float((kind == 0).mean()), 4),
        "p_green": round(float(((kind == 2) | ((kind == 0) & (c[:, -1] > entry))).mean()), 4),
        "avg_r": round(float(rs.mean()), 3),
        "r_at_target": round((target - entry) / risk, 3) if target is not None else None,
        "median_days_to_exit": int(np.median(day[decided]) + 1) if decided.any() else None,
        "bands": [{"day": d + 1, **{f"p{q}": round(float(v), 2) for q, v in zip(BANDS, col)}}
                  for d, col in enumerate(np.percentile(c, BANDS, axis=0).T)],
    }


def history(bars: list[OHLCBar], entry: float, stop: float, target: float | None, horizon: int) -> dict:
    """The same stop and target distances from every past close in the pool
    with a full horizon after it."""
    a = np.array([[b.open, b.high, b.low, b.close] for b in bars[-(LOOKBACK + 1):]], dtype=float)
    starts = len(a) - horizon - 1
    if starts < 1:
        return {"sessions": 0}
    win = np.lib.stride_tricks.sliding_window_view(a[1:], horizon, axis=0)[:starts]   # (starts, 4, horizon)
    base = a[:starts, 3]
    kind, px, _ = _outcomes(win[:, 0], win[:, 1], win[:, 2], win[:, 3], base, base * stop / entry,
                            base * target / entry if target is not None else None)
    rs = (px - base) / (base - base * stop / entry)
    return {"sessions": int(starts), "target": int((kind == 2).sum()), "stop": int((kind == 1).sum()),
            "neither": int((kind == 0).sum()), "avg_r": round(float(rs.mean()), 3)}


def verdict(sim: dict, horizon: int) -> tuple[str, list[str]]:
    """One line from the odds, and the notes that keep it from reading one-sided."""
    pt, ps, pn = sim["p_target"], sim["p_stop"], sim["p_neither"]
    by = f"within {horizon} trading days"
    if sim["r_at_target"] is None:
        return (f"Stop hit {by} in {ps:.1%} of the replays; no target to weigh it against.", [])
    if pt + ps == 0:
        return (f"No replay reached the target or the stop {by}: where the price ends decides every one.", [])
    r = sim["r_at_target"]
    share, even = pt / (pt + ps), 1 / (1 + r)
    d = share - even
    one_r = abs(r - 1) < 0.005
    if abs(d) < COIN_FLIP:
        head = "Close to a coin flip" if one_r else "Close to break-even"
    elif abs(d) < STRONG:
        head = "Leans toward the target" if d > 0 else "Leans toward the stop"
    else:
        head = "Strongly toward the target" if d > 0 else "Strongly toward the stop"
    odds = f"target first {pt:.1%} vs stop first {ps:.1%}"
    line = (f"{head}: {odds}." if one_r else
            f"{head}: at {r:g}R the target has to come first in {even:.0%} of the replays that hit a side; "
            f"it did in {share:.0%} ({odds}).")
    notes = []
    if pn > MANY_NEITHER:
        notes.append(f"{pn:.1%} of the replays hit neither {by}; where the price ends decides those.")
    if abs(d) >= COIN_FLIP and sim["avg_r"] and (sim["avg_r"] > 0) != (d > 0):
        notes.append(f"Average R is {sim['avg_r']:+.3f} all the same: the replays that hit neither pull it "
                     f"the other way.")
    return line, notes


def run(bars: list[OHLCBar], entry: float, stop: float, target: float | None, horizon: int = 20,
        replays: int = 10_000, seed: int = 19) -> dict:
    """The whole report for one plan. Raises ValueError, naming the field,
    for a plan that can't be replayed."""
    if not entry > stop > 0:
        raise ValueError("stop|the stop must be below the entry")
    if target is not None and target <= entry:
        raise ValueError("target|the target must be above the entry")
    if len(bars) < MIN_DAYS + 1:
        raise ValueError(f"ticker|needs at least {MIN_DAYS} days of history; there are {max(0, len(bars) - 1)}")
    p = pool(bars)
    sim = simulate(p, entry, stop, target, horizon, replays, seed)
    line, notes = verdict(sim, horizon)
    used = bars[-(LOOKBACK + 1):]
    return {"entry": entry, "stop": stop, "target": target, "horizon_days": horizon,
            "pool": {"days": len(p), "from": used[1].date, "to": used[-1].date},
            **sim, "history": history(bars, entry, stop, target, horizon), "verdict": line, "notes": notes}
