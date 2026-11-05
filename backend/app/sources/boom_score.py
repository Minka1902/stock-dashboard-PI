"""Composite Boom Score — pure DB computation, no external API.

Must run AFTER all other sources have stored their data (registered last in SOURCES).
Dict insertion order (Python 3.7+) guarantees this when boom_score is the final entry.

Score is an open-ended sum of the weights below (negative = net bearish
evidence). Theoretical bounds are −110 … +230 (the congress weight scales up
to 2× by trade amount), but in practice scores rarely leave −60 … +120; the UI
treats +60 as the "boom" alert threshold and caps its bar at +100.
"""
import json
from datetime import datetime, timezone

from app import config, currency, db
from app.models import BoomScore
from app.sources import margin_debt

WEIGHTS: dict[str, int] = {
    # Bullish
    "golden_cross":           20,
    "insider_cluster_buy":    20,
    "congress_buy":           15,
    "analyst_upgrade":        15,
    "near_52w_high":          10,
    "macd_crossover":         10,
    "volume_confirmed":       10,
    "short_squeeze":          10,
    "wsb_rising":             10,
    "rsi_recovery":           10,
    "fear_greed_contrarian":  10,
    "yield_uninversion":      15,
    "contracts_catalyst":     10,
    "seasonal_tailwind":      10,
    "vix_spike_contrarian":   10,
    "aaii_bearish_extreme":   10,
    "put_call_fear":          10,
    "margin_debt_deleveraging": 10,
    # Bearish (negative values)
    "death_cross":                -20,
    "insider_cluster_sell":       -20,
    "overbought_rsi":             -10,
    "congress_sale":              -15,
    "analyst_downgrade_cluster":  -15,
    "extreme_greed":              -10,
    "aaii_bullish_euphoria":      -10,
    "margin_debt_euphoria":       -10,
}

# Components whose data only exists for US listings: SEC Form 4 insider
# filings, congressional disclosures, US federal contract awards and FINRA /
# Yahoo short interest. For a Tel Aviv (.TA) listing these can never fire, so
# they are excluded and the rest of the score is rescaled to the full range —
# otherwise every TASE stock would be structurally capped below US peers.
TASE_NOT_APPLICABLE: tuple[str, ...] = (
    "insider_cluster_buy", "insider_cluster_sell",
    "congress_buy", "congress_sale",
    "contracts_catalyst", "short_squeeze",
)

_NA_LABELS = {
    "insider": "SEC Form 4 insider trades",
    "congress": "congressional trades",
    "contracts": "US federal contracts",
    "short": "short interest",
}


def not_applicable_for(ticker: str) -> tuple[str, ...]:
    """Component keys that cannot apply to `ticker`'s market."""
    if currency.market_for_symbol(ticker) == "TASE":
        return TASE_NOT_APPLICABLE
    return ()


def renormalize(components: dict[str, int], excluded: tuple[str, ...]) -> tuple[int, str]:
    """(score, note) with bullish and bearish sides each rescaled so the
    maximum reachable over the applicable components equals the full-weight
    maximum. Raw component points are left as they are for display."""
    raw = sum(components.values())
    if not excluded:
        return raw, ""
    bull_all = sum(v for v in WEIGHTS.values() if v > 0)
    bear_all = -sum(v for v in WEIGHTS.values() if v < 0)
    bull_ok = bull_all - sum(WEIGHTS[k] for k in excluded if WEIGHTS.get(k, 0) > 0)
    bear_ok = bear_all + sum(WEIGHTS[k] for k in excluded if WEIGHTS.get(k, 0) < 0)
    bull_scale = bull_all / bull_ok if bull_ok > 0 else 1.0
    bear_scale = bear_all / bear_ok if bear_ok > 0 else 1.0
    pos = sum(v for v in components.values() if v > 0)
    neg = sum(v for v in components.values() if v < 0)
    score = round(pos * bull_scale + neg * bear_scale)
    note = (
        "TASE listing: SEC Form 4 insider, congressional-trade, US federal-contract "
        "and short-interest signals don't exist for Tel Aviv stocks, so they are "
        f"excluded and the score is renormalized over the remaining components "
        f"(bullish points ×{bull_scale:.2f}, bearish ×{bear_scale:.2f}; raw sum {raw:+d})."
    )
    return score, note


# Weight multipliers for congressional trade amount ranges.
_AMOUNT_WEIGHTS: dict[str, float] = {
    "< $15,000":               0.5,
    "$1,001 - $15,000":        0.5,
    "$15,001 - $50,000":       1.0,
    "$50,001 - $100,000":      1.5,
    "$100,001 - $250,000":     1.5,
    "$250,001 - $500,000":     2.0,
    "> $500,000":               2.0,
    "$500,001 - $1,000,000":   2.0,
    "$1,000,001 - $5,000,000": 2.0,
}


def _decay(date_str: str, window_days: int = 30) -> float:
    """1.0 for today → 0.0 at window_days ago."""
    try:
        then = datetime.fromisoformat(date_str).date()
        days_ago = (datetime.now(timezone.utc).date() - then).days
        return max(0.0, 1.0 - days_ago / window_days)
    except Exception:
        return 1.0


def _parse_amount_weight(amount_range: str) -> float:
    return _AMOUNT_WEIGHTS.get(amount_range.strip(), 1.0)


def _seasonal_tailwind(conn, ticker: str, primary: str = "fwd_week", lookback: int = 10) -> bool:
    """True when the ticker has a strong historical edge for the coming week.

    Mirrors the suggestions engine threshold: avg >= +2% and win-rate >= 60% over
    the last `lookback` years of the primary forward window, with a >=3-sample
    guard so a short fluke can't fire it.
    """
    season = db.get_seasonality_for(conn, ticker)
    if season is None:
        return False
    try:
        windows = json.loads(season.windows_json)
    except (json.JSONDecodeError, TypeError):
        return False
    win = next((w for w in windows if w.get("key") == primary), None)
    if not win:
        return False
    per_year = win.get("per_year", [])[-lookback:]
    if len(per_year) < 3:
        return False
    returns = [e["return"] for e in per_year]
    avg = sum(returns) / len(returns)
    win_rate = sum(1 for r in returns if r > 0) / len(returns)
    return avg >= 0.02 and win_rate >= 0.6


def _is_earnings_soon(next_earnings: str | None, days: int = 7) -> bool:
    if not next_earnings:
        return False
    try:
        then = datetime.fromisoformat(next_earnings).date()
        days_until = (then - datetime.now(timezone.utc).date()).days
        return 0 <= days_until <= days
    except Exception:
        return False


def _score_ticker(
    ticker: str,
    conn,
    now: str,
    fg_score: float | None,
    has_uninversion: bool,
    vix_close: float | None,
    aaii: "tuple[float, float] | None",
    pc_ratio: float | None,
    md_yoy: float | None,
    muted: frozenset[str] = frozenset(),
) -> BoomScore:
    components: dict[str, int] = {}

    # --- Technical signals ---
    sig = db.get_technical_signal_for(conn, ticker)
    if sig:
        if sig.golden_cross is True:
            components["golden_cross"] = WEIGHTS["golden_cross"]
        elif sig.golden_cross is False:
            components["death_cross"] = WEIGHTS["death_cross"]

        if sig.rsi14 is not None:
            if 30 <= sig.rsi14 <= 50:
                components["rsi_recovery"] = WEIGHTS["rsi_recovery"]
            elif sig.rsi14 > 70:
                components["overbought_rsi"] = WEIGHTS["overbought_rsi"]

        if sig.macd_crossover:
            components["macd_crossover"] = WEIGHTS["macd_crossover"]

        if (
            sig.rel_volume is not None
            and sig.rel_volume > 1.5
            and sig.change_pct is not None
            and sig.change_pct > 0
        ):
            components["volume_confirmed"] = WEIGHTS["volume_confirmed"]

        if sig.price is not None and sig.high_52w is not None and sig.high_52w > 0:
            if sig.price >= sig.high_52w * 0.97:
                components["near_52w_high"] = WEIGHTS["near_52w_high"]

    # --- Insider trades ---
    buy_count = db.count_insider_buys(conn, ticker)
    sell_count = db.count_insider_sells(conn, ticker)
    if buy_count >= 2:
        components["insider_cluster_buy"] = WEIGHTS["insider_cluster_buy"]
    if sell_count >= 2:
        components["insider_cluster_sell"] = WEIGHTS["insider_cluster_sell"]

    # --- Congress (weighted by amount + time decay) ---
    congress_buys = db.get_congress_buys_for(conn, ticker)
    if congress_buys:
        best = max(
            _parse_amount_weight(b["amount_range"]) * _decay(b["transaction_date"])
            for b in congress_buys
        )
        if best > 0:
            components["congress_buy"] = round(WEIGHTS["congress_buy"] * best)
    if db.has_congress_sale(conn, ticker):
        components["congress_sale"] = WEIGHTS["congress_sale"]

    # --- Short interest ---
    si = db.get_short_interest_for(conn, ticker)
    if si and si.squeeze_flag:
        components["short_squeeze"] = WEIGHTS["short_squeeze"]

    # --- Social sentiment ---
    soc = db.get_social_for(conn, ticker)
    if soc and soc.rank_change is not None and soc.rank_change >= 5:
        components["wsb_rising"] = WEIGHTS["wsb_rising"]

    # --- Analyst ---
    analyst = db.get_analyst_for(conn, ticker)
    earnings_soon = False
    if analyst:
        if analyst.recent_upgrades > 0:
            components["analyst_upgrade"] = WEIGHTS["analyst_upgrade"]
        if analyst.recent_downgrades >= 2:
            components["analyst_downgrade_cluster"] = WEIGHTS["analyst_downgrade_cluster"]
        earnings_soon = _is_earnings_soon(analyst.next_earnings)

    # --- Macro: Fear & Greed (market-wide, applied once per compute_all call) ---
    # Same thresholds as sentiment.py so the score and the sentiment panel agree.
    if fg_score is not None:
        if fg_score < config.SENT_FG_BUY:
            components["fear_greed_contrarian"] = WEIGHTS["fear_greed_contrarian"]
        elif fg_score > config.SENT_FG_SELL:
            components["extreme_greed"] = WEIGHTS["extreme_greed"]

    # --- Macro: Yield curve un-inversion ---
    if has_uninversion:
        components["yield_uninversion"] = WEIGHTS["yield_uninversion"]

    # --- Macro: sentiment extremes (market-wide, same thresholds as sentiment.py) ---
    if vix_close is not None and vix_close >= config.SENT_VIX_EXTREME:
        components["vix_spike_contrarian"] = WEIGHTS["vix_spike_contrarian"]
    if aaii is not None:
        bullish, bearish = aaii
        if bearish >= config.SENT_AAII_EXTREME_PCT or bearish - bullish >= config.SENT_AAII_SPREAD:
            components["aaii_bearish_extreme"] = WEIGHTS["aaii_bearish_extreme"]
        elif bullish >= config.SENT_AAII_EXTREME_PCT or bullish - bearish >= config.SENT_AAII_SPREAD:
            components["aaii_bullish_euphoria"] = WEIGHTS["aaii_bullish_euphoria"]
    if pc_ratio is not None and pc_ratio >= config.SENT_PC_BUY:
        components["put_call_fear"] = WEIGHTS["put_call_fear"]
    if md_yoy is not None:
        if md_yoy <= config.SENT_MARGIN_BUY:
            components["margin_debt_deleveraging"] = WEIGHTS["margin_debt_deleveraging"]
        elif md_yoy >= config.SENT_MARGIN_SELL:
            # Covers the >=60 "extreme" range too — EXTREME is a badge, not a score.
            components["margin_debt_euphoria"] = WEIGHTS["margin_debt_euphoria"]

    # --- Federal contracts catalyst ---
    if db.has_major_contract_for(conn, ticker):
        components["contracts_catalyst"] = WEIGHTS["contracts_catalyst"]

    # --- Seasonal tailwind (historical edge for the coming week) ---
    if _seasonal_tailwind(conn, ticker):
        components["seasonal_tailwind"] = WEIGHTS["seasonal_tailwind"]

    # --- Components that can't apply to this listing's market ---
    excluded = not_applicable_for(ticker)
    for key in excluded:
        components.pop(key, None)

    # --- Auto-muted components (app/signals.py) fired but don't count ---
    fired_muted = sorted(k for k in components if k in muted)
    counted = {k: v for k, v in components.items() if k not in muted}

    # --- Mixed signal detection ---
    bullish_keys = {k for k, v in counted.items() if v > 0}
    bearish_keys = {k for k, v in counted.items() if v < 0}
    mixed_signals = bool(bullish_keys and bearish_keys)

    score, score_note = renormalize(counted, excluded)
    if fired_muted:
        mute_note = (f"Muted for a poor recent hit rate, so not counted: {', '.join(fired_muted)}.")
        score_note = f"{score_note} {mute_note}".strip()

    return BoomScore(
        ticker=ticker,
        computed_at=now,
        score=score,
        components=json.dumps(components),
        # bullish
        golden_cross="golden_cross" in counted,
        rsi_recovery="rsi_recovery" in counted,
        insider_cluster_buy="insider_cluster_buy" in counted,
        congress_buy="congress_buy" in counted,
        short_squeeze="short_squeeze" in counted,
        wsb_rising="wsb_rising" in counted,
        analyst_upgrade="analyst_upgrade" in counted,
        near_52w_high="near_52w_high" in counted,
        macd_crossover="macd_crossover" in counted,
        volume_confirmed="volume_confirmed" in counted,
        fear_greed_contrarian="fear_greed_contrarian" in counted,
        yield_uninversion="yield_uninversion" in counted,
        contracts_catalyst="contracts_catalyst" in counted,
        seasonal_tailwind="seasonal_tailwind" in counted,
        vix_spike_contrarian="vix_spike_contrarian" in counted,
        aaii_bearish_extreme="aaii_bearish_extreme" in counted,
        put_call_fear="put_call_fear" in counted,
        margin_debt_deleveraging="margin_debt_deleveraging" in counted,
        # bearish
        death_cross="death_cross" in counted,
        insider_cluster_sell="insider_cluster_sell" in counted,
        overbought_rsi="overbought_rsi" in counted,
        congress_sale="congress_sale" in counted,
        analyst_downgrade_cluster="analyst_downgrade_cluster" in counted,
        extreme_greed="extreme_greed" in counted,
        aaii_bullish_euphoria="aaii_bullish_euphoria" in counted,
        margin_debt_euphoria="margin_debt_euphoria" in counted,
        # flags
        earnings_soon=earnings_soon,
        mixed_signals=mixed_signals,
        not_applicable=json.dumps(list(excluded)),
        score_note=score_note,
        muted=json.dumps(fired_muted),
    )


def compute_all(tickers: list[str], conn, model_version: str = "legacy",
                muted: frozenset[str] = frozenset()) -> list[BoomScore]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fg_score = db.get_latest_fear_greed_score(conn)
    has_uninversion = db.has_yield_uninversion(conn)
    vix_closes = db.get_latest_vix_closes(conn, n=1)
    vix_close = vix_closes[-1] if vix_closes else None
    latest_aaii = db.get_latest_aaii(conn)
    aaii = (latest_aaii.bullish, latest_aaii.bearish) if latest_aaii else None
    latest_pc = db.get_latest_put_call(conn)
    pc_ratio = latest_pc.ratio if latest_pc else None
    md_series = margin_debt.compute_yoy(db.get_margin_debt(conn))
    md_yoy = next((r["yoy_pct"] for r in reversed(md_series) if r["yoy_pct"] is not None), None)
    scores = [
        _score_ticker(t, conn, now, fg_score, has_uninversion, vix_close, aaii, pc_ratio, md_yoy, muted)
        for t in tickers
    ]
    db.insert_boom_score_history(conn, scores, model_version)
    return scores
