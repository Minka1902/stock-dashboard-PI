"""The historical bar backtester (STC-64) with overfitting guards (STC-65).

How a rule trades, bar by bar, long-only, one position at a time:
- Signals are read on a bar's close and filled at the **next bar's open**,
  through costs.apply — the same cost model as paper fills and the track
  record. Nothing acts on a close it couldn't have known yet.
- Stops, targets and trailing stops are checked **intrabar** on each bar's
  high and low, from the fill bar on. A gap through a level fills at the open
  (the worse price), not at the level.
- Signals are vectorized (app/rules.py); the fill loop walks the bars
  because a stop depends on the path.

Guards against fooling yourself:
- Metrics are shown separately for the first 70% of the history (where you
  tuned the rule) and the last 30% (which you didn't look at).
- Every distinct variant you test of a rule is counted and reported.
- Below MIN_TRADES trades no metric is shown at all.
"""
import math

import numpy as np

from app import analytics, costs, rules
from app.models import OHLCBar

MIN_TRADES = 20
IN_SAMPLE = 0.7
TRADING_DAYS = 252


def _cagr(start: float, end: float, days: int) -> float | None:
    if start <= 0 or end <= 0 or days < 30:
        return None
    return ((end / start) ** (365.25 / days) - 1) * 100


def _metrics(dates: list[str], equity: np.ndarray, trades: list[dict], in_pos: np.ndarray,
             closes: np.ndarray) -> dict:
    """Metrics over one stretch of the run. None everywhere below MIN_TRADES."""
    n = len(trades)
    out = {"trades": n, "insufficient": n < MIN_TRADES}
    if n < MIN_TRADES or len(equity) < 2:
        return {**out, **dict.fromkeys(("total_return_pct", "cagr_pct", "max_drawdown_pct", "sharpe",
                                        "win_rate", "exposure_pct", "buy_hold_pct"))}
    days = (np.datetime64(dates[-1]) - np.datetime64(dates[0])).astype(int)
    perf = analytics.performance_metrics(list(zip(dates, equity / equity[0])), 0.0, None)
    wins = sum(1 for t in trades if t["return_pct"] > 0)
    buy_hold = costs.round_trip(float(closes[0]), float(closes[-1]))
    return {
        **out,
        "total_return_pct": round(float(equity[-1] / equity[0] - 1) * 100, 2),
        "cagr_pct": None if (c := _cagr(equity[0], equity[-1], int(days))) is None else round(c, 2),
        "max_drawdown_pct": perf["max_drawdown_pct"],
        "sharpe": perf["sharpe"],
        "win_rate": round(wins / n * 100, 1),
        "exposure_pct": round(float(in_pos.mean()) * 100, 1),
        "buy_hold_pct": round(buy_hold, 2),
    }


def run(bars: list[OHLCBar], spec: rules.RuleSpec, start_cash: float = 10_000.0) -> dict:
    """Simulate `spec` over `bars` (oldest first). Returns trades, the equity
    curve and metrics for the whole run, in-sample and out-of-sample."""
    b = rules.arrays(bars)
    o, h, l, c = b["open"], b["high"], b["low"], b["close"]
    n = len(bars)
    entry_sig = rules.evaluate(spec.entry, b)
    exit_sig = rules.evaluate(spec.exit, b) if spec.exit else np.zeros(n, dtype=bool)

    cash, shares, entry_px, entry_i, high_since = start_cash, 0.0, 0.0, -1, 0.0
    pending = None          # "buy" | "sell" for the next open
    equity = np.empty(n)
    in_pos = np.zeros(n, dtype=bool)
    trades: list[dict] = []

    def close_out(i: int, px: float, reason: str):
        nonlocal cash, shares
        fill = costs.apply(px, "SELL")
        cash += shares * fill
        trades.append({"entry_date": bars[entry_i].date, "entry": round(float(entry_px), 4),
                       "exit_date": bars[i].date, "exit": round(float(fill), 4), "reason": reason,
                       "return_pct": round(float(fill / entry_px - 1) * 100, 2)})
        shares = 0.0

    for i in range(n):
        if pending == "buy" and shares == 0:
            entry_px = costs.apply(o[i], "BUY")
            shares = cash * spec.size_pct / 100 / entry_px
            cash -= shares * entry_px
            entry_i, high_since = i, o[i]
        elif pending == "sell" and shares > 0:
            close_out(i, o[i], "exit signal")
        pending = None

        if shares > 0:
            fixed = entry_px * (1 - spec.stop_pct / 100) if spec.stop_pct else None
            trail = high_since * (1 - spec.trail_pct / 100) if spec.trail_pct else None
            stop = max((x for x in (fixed, trail) if x is not None), default=None)
            target = entry_px * (1 + spec.target_pct / 100) if spec.target_pct else None
            if stop is not None and l[i] <= stop:
                close_out(i, min(o[i], stop), "trailing stop" if stop == trail and (fixed is None or trail > fixed)
                          else "stop")
            elif target is not None and h[i] >= target:
                close_out(i, max(o[i], target), "target")
            else:
                high_since = max(high_since, h[i])

        if i + 1 < n:
            if shares > 0 and exit_sig[i]:
                pending = "sell"
            elif shares == 0 and entry_sig[i]:
                pending = "buy"
        in_pos[i] = shares > 0
        equity[i] = cash + shares * c[i]

    if shares > 0:   # mark the open trade at the last close, said so
        close_out(n - 1, c[-1], "still open (marked at the last close)")
        equity[-1] = cash

    dates = [x.date for x in bars]
    split = int(n * IN_SAMPLE)
    split_date = dates[split] if 0 < split < n else None

    def stretch(lo: int, hi: int):
        ts = [t for t in trades if dates[lo] <= t["entry_date"] <= dates[hi - 1]]
        return _metrics(dates[lo:hi], equity[lo:hi], ts, in_pos[lo:hi], c[lo:hi])

    return {
        "bars": n, "from": dates[0] if dates else None, "to": dates[-1] if dates else None,
        "start_cash": start_cash, "min_trades": MIN_TRADES, "split_date": split_date,
        "metrics": stretch(0, n) if n else _metrics([], np.array([]), [], in_pos, c),
        "in_sample": stretch(0, split) if split_date else None,
        "out_of_sample": stretch(split, n) if split_date else None,
        "trades": trades,
        "equity": [{"date": dates[i], "equity": round(float(equity[i]), 2)}
                   for i in sorted({*range(0, n, max(1, n // 500)), n - 1}) if n and not math.isnan(equity[i])],
    }
