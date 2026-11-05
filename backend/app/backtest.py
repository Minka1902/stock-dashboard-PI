"""Backtesting over what the app actually recorded.

Two honest questions, and a hard limit on both.

The limit: suggestion history accrues from the day it shipped and boom-score
history from the day scoring started. Neither is backfilled, because
reconstructing calls we never made would be inventing them. So every result
here ships with a `coverage` block, and horizons that haven't elapsed are
counted as pending rather than quietly dropped — twelve samples over three
weeks must not read like a track record.

Explicitly NOT done: re-running analysis.build() over historical bars to
synthesize what we "would have" said. Today's weights, and today's
fundamentals/analyst/congress snapshots, applied to last year's prices is
look-ahead bias wearing a track record's clothes.

Every number is per model version (model_version.py) — a reweighted model
starts a new record — and comes gross and net of costs (costs.py). A hit rate
carries its Wilson 95% interval and is flagged `insufficient` below
config.MIN_TRACK_SAMPLE outcomes.
"""
import math
from datetime import date, timedelta

from app import config, costs, db, model_version, suggestion_history

# Forward windows for the signal replay, in calendar days.
REPLAY_HORIZONS = (7, 30)
# Threshold curve: the question "is 60 the right alert level?" is only
# answerable by comparing it against its neighbours.
REPLAY_THRESHOLDS = (40, 50, 60, 70)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for a hit rate, in %. Honest at small n, where
    the plain ± interval would run past 0 or 100."""
    if n <= 0:
        return None
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(max(0.0, centre - half) * 100, 1), round(min(1.0, centre + half) * 100, 1)


def _stats(returns: list[float]) -> dict:
    """Summary of a bucket of realised returns. All None when empty — never 0."""
    if not returns:
        return {"n": 0, "hit_rate": None, "hit_rate_ci": None, "insufficient": True,
                "median": None, "mean": None, "best": None, "worst": None}
    wins = sum(1 for r in returns if r > 0)
    return {
        "n": len(returns),
        "hit_rate": round(wins / len(returns) * 100, 1),
        "hit_rate_ci": wilson(wins, len(returns)),
        "insufficient": len(returns) < config.MIN_TRACK_SAMPLE,
        "median": round(_median(returns), 2),
        "mean": round(sum(returns) / len(returns), 2),
        "best": round(max(returns), 2),
        "worst": round(min(returns), 2),
    }


def _versions(rows: list[dict], date_key: str) -> list[dict]:
    """The model versions present, newest last-seen first."""
    seen: dict[str, dict] = {}
    for r in rows:
        v = r.get("model_version") or model_version.LEGACY
        d = r[date_key][:10]
        e = seen.setdefault(v, {"version": v, "n": 0, "first_date": d, "last_date": d})
        e["n"] += 1
        e["first_date"], e["last_date"] = min(e["first_date"], d), max(e["last_date"], d)
    current = model_version.current()
    out = sorted(seen.values(), key=lambda e: e["last_date"], reverse=True)
    for e in out:
        e["current"] = e["version"] == current
    return out


def _pick_version(versions: list[dict], requested: str | None) -> str:
    """The requested version if it has data, else the running model's, else
    the most recently seen one."""
    names = [v["version"] for v in versions]
    if requested in names:
        return requested
    current = model_version.current()
    if current in names or not names:
        return current
    return names[0]


def _coverage(dates: list[str]) -> dict:
    """How much history this actually rests on. Always present, never implied."""
    if not dates:
        return {"first_date": None, "last_date": None, "days": 0, "distinct_days": 0}
    first, last = min(dates), max(dates)
    try:
        span = (date.fromisoformat(last) - date.fromisoformat(first)).days + 1
    except ValueError:
        span = 0
    return {
        "first_date": first, "last_date": last,
        "days": span, "distinct_days": len(set(dates)),
    }


def track_record(conn, user_id: int, months: int = 12, version: str | None = None) -> dict:
    """What was suggested, and what happened next — grouped by action and kind,
    for one model version, gross (`d7`) and net of costs (`d7_net`).

    Buckets report `n_pending` alongside `n`: an entry whose 7-day window hasn't
    elapsed is not a miss, and folding it into the hit rate either way would be
    wrong.
    """
    entries = db.get_suggestion_history(conn, user_id, months=max(1, months))
    versions = _versions([e.model_dump() for e in entries], "for_date")
    picked = _pick_version(versions, version)
    rows = suggestion_history.with_outcomes(
        conn, [e for e in entries if e.model_version == picked])

    def bucket(label: str, subset: list[dict]) -> dict:
        out = {"label": label, "n": len(subset)}
        for days in suggestion_history.HORIZONS:
            key = f"d{days}"
            realised = [r for r in subset if r["outcomes"].get(key) is not None]
            out[key] = _stats([r["outcomes"][key] for r in realised])
            out[f"{key}_net"] = _stats([r["outcomes_net"][key] for r in realised])
            out[f"{key}_pending"] = len(subset) - len(realised)
        return out

    by_action: dict[str, list[dict]] = {}
    by_kind: dict[str, list[dict]] = {}
    by_ticker: dict[str, list[dict]] = {}
    for r in rows:
        by_action.setdefault(r.get("action") or "(none)", []).append(r)
        by_kind.setdefault(r.get("kind") or "(none)", []).append(r)
        if r.get("ticker"):
            by_ticker.setdefault(r["ticker"], []).append(r)

    return {
        "model_version": picked,
        "versions": versions,
        "cost_bps_per_side": config.COST_BPS_PER_SIDE,
        "min_sample": config.MIN_TRACK_SAMPLE,
        "coverage": _coverage([r["for_date"] for r in rows]),
        "overall": bucket("All suggestions", rows),
        "by_action": [bucket(k, v) for k, v in sorted(by_action.items())],
        "by_kind": [bucket(k, v) for k, v in sorted(by_kind.items())],
        # Per-ticker, busiest first: "which stock is this about" is the first
        # question the aggregate rows provoke, and the data was already here —
        # with_outcomes carries the ticker, track_record just discarded it.
        "by_ticker": [
            bucket(k, v) for k, v in
            sorted(by_ticker.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        ],
        "benchmark": _benchmark(conn, rows),
        # Stated in the payload, not just the UI, so the caveat travels with
        # the numbers wherever they end up.
        "caveat": (
            "History starts when the app began recording; there is no backfill. "
            "One model version at a time; net figures pay "
            f"{config.COST_BPS_PER_SIDE:g} bps a side. Small samples are small — "
            "read the coverage block first."
        ),
    }


def _benchmark(conn, rows: list[dict]) -> dict:
    """Same-window SPY return, or an explicit reason it isn't available.

    A track record without a market comparison flatters itself in a rising
    market, so if SPY bars aren't stored the answer is "we can't say", not
    silence.
    """
    if not rows:
        return {"available": False, "reason": "no suggestions in range"}
    bars = db.get_ohlc(conn, "SPY", "daily")
    if not bars:
        return {
            "available": False,
            "reason": "SPY daily bars are not stored — add SPY to a watchlist to enable this",
        }
    closes = {b.date: b.close for b in bars}
    first = min(r["for_date"] for r in rows)
    start = next((closes[d] for d in sorted(closes) if d >= first), None)
    end = bars[-1].close
    if not start:
        return {"available": False, "reason": "no SPY bar on or after the first suggestion"}
    return {
        "available": True,
        "from": first, "to": bars[-1].date,
        "return_pct": round((end / start - 1) * 100, 2),
    }


def signal_replay(
    conn, horizon_days: int = 7, months: int = 12,
    thresholds: tuple[int, ...] = REPLAY_THRESHOLDS, version: str | None = None,
) -> dict:
    """Every upward boom-score threshold crossing, against what price did next.

    This is the closest thing to a real backtest the stored data supports: the
    scores were computed live, and the forward returns come from stored bars.
    It answers "is the alert threshold set at a useful level?" — which nothing
    else in the app does.
    """
    since = (date.today() - timedelta(days=30 * max(1, months))).isoformat()
    today = date.today().isoformat()
    history = db.get_boom_score_history_all(conn, since)
    versions = _versions(history, "computed_at")
    picked = _pick_version(versions, version)

    # Group by ticker, ordered in time, so a "crossing" is a real transition
    # rather than two unrelated rows — and within one model version only.
    by_ticker: dict[str, list[tuple[str, int]]] = {}
    for row in history:
        if (row["model_version"] or model_version.LEGACY) == picked:
            by_ticker.setdefault(row["ticker"], []).append((row["computed_at"], row["score"]))

    closes_cache: dict[str, dict[str, float]] = {}

    def closes_for(ticker: str) -> dict[str, float]:
        if ticker not in closes_cache:
            closes_cache[ticker] = {b.date: b.close for b in db.get_ohlc(conn, ticker, "daily")}
        return closes_cache[ticker]

    results = []
    all_dates: list[str] = []
    for threshold in thresholds:
        returns: list[float] = []
        net: list[float] = []
        pending = 0
        crossings = 0
        seen_days: set[tuple[str, str]] = set()

        for ticker, series in by_ticker.items():
            series.sort(key=lambda x: x[0])
            prev = None
            for stamp, score in series:
                day = stamp[:10]
                if prev is not None and prev < threshold <= score:
                    # One crossing per ticker per day: the score is recomputed
                    # every few minutes and would otherwise count dozens of
                    # times for a single move.
                    if (ticker, day) in seen_days:
                        prev = score
                        continue
                    seen_days.add((ticker, day))
                    crossings += 1
                    all_dates.append(day)

                    entry_close = _close_on_or_after(closes_for(ticker), day)
                    target_day = (date.fromisoformat(day) + timedelta(days=horizon_days)).isoformat()
                    if target_day > today:
                        pending += 1
                    else:
                        exit_close = _close_on_or_after(closes_for(ticker), target_day)
                        if entry_close and exit_close:
                            returns.append((exit_close / entry_close - 1) * 100)
                            net.append(costs.round_trip(entry_close, exit_close))
                        else:
                            pending += 1  # no bar to measure against
                prev = score

        stats = _stats(returns)
        stats.update({"threshold": threshold, "crossings": crossings, "pending": pending,
                      "net": _stats(net)})
        results.append(stats)

    return {
        "model_version": picked,
        "versions": versions,
        "cost_bps_per_side": config.COST_BPS_PER_SIDE,
        "min_sample": config.MIN_TRACK_SAMPLE,
        "horizon_days": horizon_days,
        "coverage": _coverage(all_dates),
        "thresholds": results,
        "caveat": (
            "Boom-score history only exists from when scoring started, and a "
            "crossing is counted once per ticker per day. Forward returns whose "
            "window hasn't elapsed are reported as pending, not as zero."
        ),
    }


def _close_on_or_after(closes: dict[str, float], target: str) -> float | None:
    """First close on or after a date — markets are shut on plenty of days."""
    for d in sorted(closes):
        if d >= target:
            return closes[d]
    return None
