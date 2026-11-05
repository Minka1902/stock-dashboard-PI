"""Portfolio performance and risk analytics (STC-32).

Returns are time-weighted: each day's return strips out the external flows
(deposits, withdrawals, unfunded buys/sells, bot allocations) recorded on the
snapshot, so moving money in or out never looks like gaining or losing it.

Every block of the report says where it came from and as of when. Below
MIN_SNAPSHOTS daily snapshots the performance metrics are None — a Sharpe
ratio off a fortnight is noise with a decimal point.
"""
import math
import time
from datetime import date, timedelta

import numpy as np

from app import currency, db, quotes
from app.models import EquitySnapshot

TRADING_DAYS = 252
MIN_SNAPSHOTS = 20
CORRELATION_SESSIONS = 126       # ~6 months of daily returns
CORRELATION_FLAG = 0.8
MIN_OVERLAP = 20
BENCHMARK = {"USD": "SPY", "ILS": "^TA125.TA"}
_CACHE_SECONDS = 120
_cache: dict[tuple[int, str], tuple[float, dict]] = {}


# ---------- time-weighted series ----------

def current_base_run(snaps: list[EquitySnapshot]) -> list[EquitySnapshot]:
    """The trailing run of snapshots in the latest base currency. Values in two
    currencies can't be chained, so a base switch starts the series afresh."""
    if not snaps:
        return []
    base = snaps[-1].base_currency
    i = len(snaps)
    while i > 0 and snaps[i - 1].base_currency == base:
        i -= 1
    return snaps[i:]


def twr_index(snaps: list[EquitySnapshot]) -> list[tuple[str, float]]:
    """[(date, growth of 1)] through `snaps` (oldest first, one base currency).

    Day t's return is (value_t − flow_t) / value_{t−1} − 1. A previous value of
    zero (an empty account) carries the index forward unchanged."""
    out: list[tuple[str, float]] = []
    level, prev = 1.0, None
    for s in snaps:
        if prev is not None and prev > 0:
            level *= (s.value_base - s.flow_base) / prev
        out.append((s.date, level))
        prev = s.value_base
    return out


def drawdown_from_peak(index: list[tuple[str, float]]) -> dict | None:
    """The latest point's drawdown from the highest point before it:
    {pct (positive = below the peak), peak_date, as_of}. None for an empty or
    single-point series, where there is nothing to fall from."""
    if len(index) < 2:
        return None
    peak_date, peak = max(index, key=lambda p: p[1])
    as_of, last = index[-1]
    pct = (1 - last / peak) * 100 if peak > 0 else 0.0
    return {"pct": round(max(pct, 0.0), 2), "peak_date": peak_date, "as_of": as_of}


def drawdown_series(index: list[tuple[str, float]]) -> list[float]:
    """Drawdown from the running peak at every point, in % (0 at a new high)."""
    if not index:
        return []
    levels = np.array([v for _, v in index])
    peak = np.maximum.accumulate(levels)
    return [round(float(x), 2) for x in (1 - levels / peak) * 100]


# ---------- metrics ----------

def _round(v, digits=2):
    return None if v is None or not math.isfinite(v) else round(float(v), digits)


def performance_metrics(index: list[tuple[str, float]], rf_annual_pct: float | None,
                        bench_closes: dict[str, float] | None) -> dict:
    """TWR, max drawdown, annualized volatility, Sharpe, Sortino and beta.
    All None below MIN_SNAPSHOTS; Sharpe/Sortino None without a risk-free
    rate; beta None without MIN_OVERLAP benchmark returns on the same days."""
    out = dict.fromkeys(("twr_pct", "max_drawdown_pct", "volatility_pct", "sharpe", "sortino", "beta"))
    if len(index) < MIN_SNAPSHOTS:
        return out
    dates = [d for d, _ in index]
    levels = np.array([v for _, v in index])
    r = levels[1:] / levels[:-1] - 1
    sd = r.std(ddof=1)
    out["twr_pct"] = _round((levels[-1] / levels[0] - 1) * 100)
    out["max_drawdown_pct"] = _round(float(np.max(1 - levels / np.maximum.accumulate(levels))) * 100)
    out["volatility_pct"] = _round(sd * math.sqrt(TRADING_DAYS) * 100)
    if rf_annual_pct is not None and sd > 0:
        excess = r - ((1 + rf_annual_pct / 100) ** (1 / TRADING_DAYS) - 1)
        out["sharpe"] = _round(excess.mean() / sd * math.sqrt(TRADING_DAYS))
        downside = math.sqrt(float(np.mean(np.minimum(excess, 0) ** 2)))
        out["sortino"] = _round(excess.mean() / downside * math.sqrt(TRADING_DAYS)) if downside > 0 else None
    if bench_closes:
        b = np.array([bench_closes.get(d, np.nan) for d in dates])
        rb = b[1:] / b[:-1] - 1
        ok = np.isfinite(rb)
        if ok.sum() >= MIN_OVERLAP and np.var(rb[ok], ddof=1) > 0:
            out["beta"] = _round(np.cov(r[ok], rb[ok], ddof=1)[0, 1] / np.var(rb[ok], ddof=1))
    return out


def benchmark_index(dates: list[str], closes: dict[str, float]) -> list[float | None]:
    """The benchmark as growth of 1 from the first date it has a close for."""
    start = next((closes[d] for d in dates if d in closes), None)
    return [round(closes[d] / start, 6) if start and d in closes else None for d in dates]


def exposure(positions: list[dict]) -> dict:
    """Weights by holding, sector and currency; top-3 weight and HHI (Σw², 0–1;
    1/HHI is the effective number of positions). Unknown sector stays
    'unknown' rather than being dropped."""
    total = sum(p["value"] for p in positions)
    if total <= 0:
        return {"total": 0.0, "positions": [], "by_sector": [], "by_currency": [], "top3_weight": None,
                "hhi": None, "effective_n": None}
    rows = sorted(({**p, "weight": p["value"] / total} for p in positions), key=lambda p: -p["weight"])

    def group(key):
        acc: dict[str, float] = {}
        for p in rows:
            acc[p[key] or "unknown"] = acc.get(p[key] or "unknown", 0.0) + p["weight"]
        return [{"name": k, "weight": round(v, 4)} for k, v in sorted(acc.items(), key=lambda kv: -kv[1])]

    w = np.array([p["weight"] for p in rows])
    hhi = float(np.sum(w ** 2))
    return {
        "total": round(total, 2),
        "positions": [{**p, "weight": round(p["weight"], 4)} for p in rows],
        "by_sector": group("sector"),
        "by_currency": group("currency"),
        "top3_weight": round(float(w[:3].sum()), 4),
        "hhi": round(hhi, 4),
        "effective_n": round(1 / hhi, 1),
    }


def correlation(series: dict[str, list[tuple[str, float]]], sessions: int = CORRELATION_SESSIONS) -> dict:
    """Pairwise daily-return correlations over the last `sessions` closes,
    each pair aligned on the days both traded (NYSE and TASE calendars
    differ). Pairs above CORRELATION_FLAG are flagged as moving together."""
    tickers = sorted(series)
    rets: dict[str, dict[str, float]] = {}
    for t in tickers:
        pts = series[t][-(sessions + 1):]
        rets[t] = {pts[i][0]: pts[i][1] / pts[i - 1][1] - 1 for i in range(1, len(pts)) if pts[i - 1][1]}
    matrix: list[list[float | None]] = [[None] * len(tickers) for _ in tickers]
    pairs = []
    for i, a in enumerate(tickers):
        matrix[i][i] = 1.0
        for j in range(i + 1, len(tickers)):
            b = tickers[j]
            common = sorted(set(rets[a]) & set(rets[b]))
            if len(common) < MIN_OVERLAP:
                continue
            x = np.array([rets[a][d] for d in common])
            y = np.array([rets[b][d] for d in common])
            if x.std() == 0 or y.std() == 0:
                continue
            rho = round(float(np.corrcoef(x, y)[0, 1]), 3)
            matrix[i][j] = matrix[j][i] = rho
            if rho > CORRELATION_FLAG:
                pairs.append({"a": a, "b": b, "rho": rho, "days": len(common)})
    return {"tickers": tickers, "matrix": matrix, "pairs": sorted(pairs, key=lambda p: -p["rho"]),
            "flag_above": CORRELATION_FLAG, "sessions": sessions}


def risk_read(expo: dict, corr: dict) -> str:
    """One plain line: the concentrations worth knowing about, or that there are none."""
    bits = []
    sector = next((s for s in expo["by_sector"] if s["name"] != "unknown"), None)
    if sector and sector["weight"] >= 0.4:
        bits.append(f"{sector['weight'] * 100:.0f}% {sector['name']}")
    n = len(expo["positions"])
    # Flag the top 3 only when they're well above an equal-weight share: in a
    # five-stock book the top 3 are 60% by construction.
    if n > 3 and expo["top3_weight"] >= max(0.6, 3 / n + 0.15):
        bits.append(f"top 3 are {expo['top3_weight'] * 100:.0f}% of the book")
    top3 = {p["ticker"] for p in expo["positions"][:3]}
    together = [p for p in corr["pairs"] if p["a"] in top3 and p["b"] in top3]
    if len(top3) == 3 and len(together) == 3:
        bits.append("the top 3 move together")
    elif together:
        bits.append(", ".join(f"{p['a']} & {p['b']} move together" for p in together))
    if bits:
        line = "; ".join(bits)
        return line[0].upper() + line[1:] + "."
    if not expo["positions"]:
        return "No priced holdings to read."
    lead = f"largest sector {sector['name']} {sector['weight'] * 100:.0f}%" if sector else "sectors unknown"
    return f"No concentration flags: {lead}, top 3 {expo['top3_weight'] * 100:.0f}%."


# ---------- the report ----------

def _priced_positions(conn, holdings, base: str) -> tuple[list[dict], list[str]]:
    """Each holding valued in `base` (live quote, else the last close); the
    ones with no price or FX rate are named, never estimated."""
    live = {q.ticker: q.price for q in quotes.get_quotes([h.ticker for h in holdings]) if q.price} \
        if holdings else {}
    rates = currency.rates_to_base(base, [h.currency for h in holdings])["rates"]
    sectors = {f.ticker: f.sector for f in db.get_fundamentals(conn)}
    out, unpriced = [], []
    for h in holdings:
        px = live.get(h.ticker)
        if px is None:
            bars = db.get_ohlc(conn, h.ticker, "daily")
            px = bars[-1].close if bars else None
        rate = rates.get(h.currency.upper())
        if px is None or rate is None:
            unpriced.append(h.ticker)
            continue
        out.append({"ticker": h.ticker, "value": h.shares * px * rate, "sector": sectors.get(h.ticker),
                    "currency": h.currency})
    return out, unpriced


def _account_label(account: str) -> str:
    if account == "manual":
        return "your Portfolio"
    if account == "paper":
        return "the paper account"
    return f"bot {account.split(':', 1)[1]}"


def report(conn, user_id: int, account: str) -> dict:
    """Everything the Performance tab shows for one account, TTL-cached."""
    key = (user_id, account)
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]

    snaps = current_base_run(db.get_equity_snapshots(conn, user_id, account))
    base = snaps[-1].base_currency if snaps else db.get_notify_profile(conn, user_id).base_currency
    index = twr_index(snaps)
    dates = [d for d, _ in index]
    bench_ticker = BENCHMARK.get(base, "SPY")
    bench = {b.date: b.close for b in db.get_ohlc(conn, bench_ticker, "daily")}
    bill = db.get_latest_bill_rate(conn)
    metrics = performance_metrics(index, bill[0] if bill else None, bench)
    bench_idx = benchmark_index(dates, bench)
    dd = drawdown_series(index)

    holdings = db.get_portfolio(conn, user_id, account)
    priced, unpriced = _priced_positions(conn, holdings, base)
    expo = exposure(priced)
    since = (date.today() - timedelta(days=200)).isoformat()
    series = {p["ticker"]: [(b.date, b.close) for b in db.get_ohlc(conn, p["ticker"], "daily")
                            if b.date >= since and b.close] for p in priced}
    corr = correlation(series)

    out = {
        "account": account,
        "base_currency": base,
        "performance": {
            "source": f"daily equity snapshots of {_account_label(account)}, in {base}, "
                      f"net of deposits and withdrawals",
            "as_of": dates[-1] if dates else None,
            "n_snapshots": len(index),
            "min_snapshots": MIN_SNAPSHOTS,
            "series": [{"date": d, "index": round(v, 6), "drawdown": dd[i], "benchmark": bench_idx[i]}
                       for i, (d, v) in enumerate(index)],
            "metrics": metrics,
            "benchmark": {"ticker": bench_ticker,
                          "return_pct": _round((bench_idx[-1] - 1) * 100) if bench_idx and bench_idx[-1] else None},
            "risk_free": {"rate_pct": bill[0], "as_of": bill[1], "source": "US Treasury 3-month bill"}
                         if bill else None,
        },
        "exposure": {
            "source": "holdings at the live price (else the last close), sectors from fundamentals",
            "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "unpriced": unpriced,
            **expo,
        },
        "correlation": {
            "source": f"daily closes, last {CORRELATION_SESSIONS} sessions per pair",
            "as_of": max((pts[-1][0] for pts in series.values() if pts), default=None),
            **corr,
        },
        "risk_read": risk_read(expo, corr),
    }
    _cache[key] = (time.monotonic() + _CACHE_SECONDS, out)
    return out
