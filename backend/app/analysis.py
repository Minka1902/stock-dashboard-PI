"""Explainable, rule-based technical analysis for a single ticker.

Pure functions only — no network, no DB. Takes OHLCV bars (oldest first) and
returns a StockAnalysis with a Buy/Sell/Hold recommendation, every signal shown
as its own Evidence row (source + reasoning), never a black-box score and never
fabricated data — a component with insufficient data is omitted, not invented.

This engine implements a complete technical-analysis methodology; each item maps
to an explicit component here (see the plan `docs/fixes_batch.md`):

  A1 support/resistance   -> support_resistance()   (pivot clustering)
  A2 trendlines           -> trendline_levels()
  A3 channels             -> detect_channel() + parallel trendline evidence
  A4 gaps (roles/S-R)     -> gaps() + classify_gaps() + gap_levels()
  A5 round numbers        -> round_number_levels()
  A6 moving averages       -> moving_averages() + ma_extension (MA magnet)
  A7 healthy uptrend      -> ma_structure() -> "healthy_uptrend"
  A8 topping formation    -> ma_structure() -> "topping"
  A9 reclaim progression  -> ma_structure() -> "reclaiming"
  B1 false breakouts      -> detect_breakout() -> "failed"
  B2 confirmation candle  -> detect_breakout() -> broke_unconfirmed/confirmed
  B3 staged entry         -> staging_guidance()
  B4 ATR volatility gate  -> atr() + 1-ATR stop buffer
  B5 structure stops      -> compute_stop() (+ trendline basis)
  B6 R/R >= 3:1 gate      -> rr_pass (demotes Buy -> Hold)
  C  chart patterns       -> detect_patterns()
  D  candlesticks         -> detect_candles()
  E  volume confirmation  -> volume_read()
  F  confluence + plan    -> build() evidence pipeline + recommendation
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from app.models import (
    BreakoutState, CandleSignal, Evidence, GapEvent, OHLCBar, PatternCriterion,
    PatternHit, SRLevel, StockAnalysis, TargetRung, TrendlineLevel, VolumeRead,
)
from app.sources.technical import compute_ma, compute_macd, compute_rsi

# --- tunables ---
_ATR_PERIOD = 14
# Decision thresholds. They are part of the scoring model's version
# (app/model_version.py): change one and new calls are tracked as a new model.
CONVICTION_ACCUMULATE = 45
CONVICTION_AVOID = -45
CONVICTION_REDUCE = -15
RR_THRESHOLD = 3.0
_ATR_STOP_MULT = 2.0
_STRUCT_STOP_ATR_BUFFER = 1.0   # stop ~1 ATR below structure so stop-hunts can't tag it (B4)
_PIVOT_K = 5            # bars on each side to qualify a swing pivot
_SR_TOL = 0.02         # cluster pivots within 2% into one level
_GAP_MIN_PCT = 0.02    # open vs prior close beyond 2% is a gap
_LEVEL_TOL = 0.02      # "similar price" tolerance for pattern tops/bottoms
_TRENDLINE_TOL = 0.02  # a pivot within 2% of a line's projection counts as a touch
_ROUND_TOL = 0.004     # a bar within 0.4% of a round number counts as a stall/touch
_CANDLE_LOOKBACK = 5   # scan the last N bars for candlestick signals
_BREAKOUT_APPROACH_ATR = 1.0   # within 1 ATR of a level = "approaching" (warn before)
_VOL_CONFIRM_RATIO = 1.3       # break bar volume >= 1.3x avg confirms
_EXTENSION_WARN_ATR = 2.5      # |price - ma20| beyond this many ATR = mean-reversion risk


# ============================ indicators ============================

def true_ranges(bars: list[OHLCBar]) -> list[float]:
    out: list[float] = []
    for i, b in enumerate(bars):
        if i == 0:
            out.append(b.high - b.low)
        else:
            pc = bars[i - 1].close
            out.append(max(b.high - b.low, abs(b.high - pc), abs(b.low - pc)))
    return out


def atr(bars: list[OHLCBar], period: int = _ATR_PERIOD) -> float | None:
    """Wilder's Average True Range."""
    if len(bars) < period + 1:
        return None
    tr = true_ranges(bars)
    a = sum(tr[1:period + 1]) / period  # seed on first `period` true ranges
    for t in tr[period + 1:]:
        a = (a * (period - 1) + t) / period
    return round(a, 4)


# ============================ swing structure ============================

def swing_pivots(bars: list[OHLCBar], k: int = _PIVOT_K) -> list[dict]:
    """Local swing highs/lows: extreme within +/-k bars. Oldest first.

    Adjacent same-kind pivots (plateaus / ties) are merged into one, keeping the
    most extreme, so a flat top does not register as two peaks.
    """
    raw: list[dict] = []
    n = len(bars)
    for i in range(k, n - k):
        window = bars[i - k:i + k + 1]
        hi = bars[i].high
        lo = bars[i].low
        if hi >= max(b.high for b in window):
            raw.append({"index": i, "date": bars[i].date, "price": round(hi, 4), "kind": "high"})
        if lo <= min(b.low for b in window):
            raw.append({"index": i, "date": bars[i].date, "price": round(lo, 4), "kind": "low"})

    merged: list[dict] = []
    for p in sorted(raw, key=lambda x: x["index"]):
        dup = next((r for r in merged if r["kind"] == p["kind"] and abs(r["index"] - p["index"]) <= k), None)
        if dup is None:
            merged.append(dict(p))
        elif (p["kind"] == "high" and p["price"] > dup["price"]) or \
             (p["kind"] == "low" and p["price"] < dup["price"]):
            dup.update(p)  # keep the more extreme point as the single pivot
    return merged


def support_resistance(pivots: list[dict], price: float, tol: float = _SR_TOL) -> tuple[list[SRLevel], list[SRLevel]]:
    """Cluster swing pivots into horizontal levels, split by current price."""
    clusters: list[dict] = []
    for p in sorted(pivots, key=lambda x: x["price"]):
        placed = False
        for c in clusters:
            if abs(p["price"] - c["avg"]) / c["avg"] <= tol:
                c["prices"].append(p["price"])
                c["touches"] += 1
                c["last"] = max(c["last"], p["date"])
                c["avg"] = sum(c["prices"]) / len(c["prices"])
                placed = True
                break
        if not placed:
            clusters.append({"avg": p["price"], "prices": [p["price"]], "touches": 1, "last": p["date"]})

    support, resistance = [], []
    for c in clusters:
        lvl = SRLevel(price=round(c["avg"], 4), kind="", touches=c["touches"], last_touch=c["last"])
        if c["avg"] < price:
            lvl.kind = "support"
            support.append(lvl)
        else:
            lvl.kind = "resistance"
            resistance.append(lvl)
    # strongest (most-touched) first, then nearest to price
    support.sort(key=lambda l: (-l.touches, price - l.price))
    resistance.sort(key=lambda l: (-l.touches, l.price - price))
    return support, resistance


def nearest_support(support: list[SRLevel], price: float) -> SRLevel | None:
    below = [l for l in support if l.price < price]
    return max(below, key=lambda l: l.price) if below else None


def nearest_resistance(resistance: list[SRLevel], price: float) -> SRLevel | None:
    above = [l for l in resistance if l.price > price]
    return min(above, key=lambda l: l.price) if above else None


def gaps(bars: list[OHLCBar], min_pct: float = _GAP_MIN_PCT) -> list[GapEvent]:
    """Unfilled-aware up/down gaps (open vs prior close beyond min_pct)."""
    out: list[GapEvent] = []
    for i in range(1, len(bars)):
        pc = bars[i - 1].close
        op = bars[i].open
        if pc <= 0:
            continue
        change = (op - pc) / pc
        if abs(change) < min_pct:
            continue
        kind = "up" if change > 0 else "down"
        # filled if any later bar trades back through the prior close
        later = bars[i + 1:]
        if kind == "up":
            filled = any(b.low <= pc for b in later)
        else:
            filled = any(b.high >= pc for b in later)
        out.append(GapEvent(
            date=bars[i].date, from_price=round(pc, 4), to_price=round(op, 4),
            pct=round(change * 100, 2), kind=kind, filled=filled,
        ))
    return out


# ============================ trend / MAs ============================

def moving_averages(closes: list[float]) -> dict[str, float | None]:
    return {
        "ma20": compute_ma(closes, 20),
        "ma50": compute_ma(closes, 50),
        "ma150": compute_ma(closes, 150),
        "ma200": compute_ma(closes, 200),
    }


def ma_alignment(mas: dict[str, float | None]) -> str:
    vals = [mas["ma20"], mas["ma50"], mas["ma150"], mas["ma200"]]
    if any(v is None for v in vals):
        # fall back to whatever is present
        present = [v for v in vals if v is not None]
        if len(present) < 2:
            return "mixed"
        if all(present[i] > present[i + 1] for i in range(len(present) - 1)):
            return "stacked_bull"
        if all(present[i] < present[i + 1] for i in range(len(present) - 1)):
            return "stacked_bear"
        return "mixed"
    if vals[0] > vals[1] > vals[2] > vals[3]:
        return "stacked_bull"
    if vals[0] < vals[1] < vals[2] < vals[3]:
        return "stacked_bear"
    return "mixed"


def trend(closes: list[float], mas: dict[str, float | None]) -> str:
    price = closes[-1]
    ma50, ma200 = mas["ma50"], mas["ma200"]
    slope = None
    if len(closes) >= 70 and ma50 is not None:
        prior = compute_ma(closes[:-20], 50)
        if prior:
            slope = ma50 - prior
    if ma50 and ma200:
        if price > ma50 > ma200 and (slope is None or slope > 0):
            return "up"
        if price < ma50 < ma200 and (slope is None or slope < 0):
            return "down"
    return "sideways"


# ============================ patterns ============================

def _line(points: list[tuple[int, float]]) -> tuple[float, float]:
    """Least-squares slope, intercept for (x, y) points."""
    n = len(points)
    sx = sum(x for x, _ in points)
    sy = sum(y for _, y in points)
    sxx = sum(x * x for x, _ in points)
    sxy = sum(x * y for x, y in points)
    denom = n * sxx - sx * sx
    if denom == 0:
        return 0.0, sy / n
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


def _piv(kind: str, pivots: list[dict]) -> list[dict]:
    return [p for p in pivots if p["kind"] == kind]


def _pivot_ref(p: dict, role: str) -> dict:
    return {"date": p["date"], "price": p["price"], "role": role}


def _criterion(name: str, met: bool, detail: str = "") -> PatternCriterion:
    return PatternCriterion(name=name, met=met, detail=detail)


def detect_double_top(pivots: list[dict], price: float) -> PatternHit | None:
    highs = _piv("high", pivots)
    lows = _piv("low", pivots)
    if len(highs) < 2 or not lows:
        return None
    a, b = highs[-2], highs[-1]
    if abs(a["price"] - b["price"]) / a["price"] > _LEVEL_TOL:
        return None
    trough = [lo for lo in lows if a["index"] < lo["index"] < b["index"]]
    if not trough:
        return None
    neck = min(trough, key=lambda x: x["price"])
    top = (a["price"] + b["price"]) / 2
    conf = 0.55 + 0.25 * (1 - abs(a["price"] - b["price"]) / a["price"] / _LEVEL_TOL)

    # The shape is on the chart. Whether it has *done* anything depends on price
    # rolling over off the second peak and then losing the neckline.
    rolling_over = price < min(a["price"], b["price"])
    broke_neck = price < neck["price"]
    criteria = [
        _criterion("two peaks at the same level", True,
                   f"{a['price']:.2f} and {b['price']:.2f}"),
        _criterion("price back below the peaks", rolling_over,
                   f"{price:.2f} vs {min(a['price'], b['price']):.2f}"),
        _criterion("neckline broken", broke_neck,
                   f"{price:.2f} vs neckline {neck['price']:.2f}"),
    ]
    if not rolling_over:
        return None  # still pressing the highs: no topping shape to speak of yet

    return PatternHit(
        name="double_top", label="Double Top", direction="bearish",
        # A shape that hasn't broken its neckline is worth less than one that has.
        confidence=round(min(conf, 0.9) * (1.0 if broke_neck else 0.6), 2),
        pivots=[_pivot_ref(a, "peak 1"), _pivot_ref(neck, "neckline"), _pivot_ref(b, "peak 2")],
        measured_move=round(neck["price"] - (top - neck["price"]), 2),
        note=("Two failed pushes at the same ceiling."
              if broke_neck
              else "Two failed pushes at the same ceiling; the neckline still holds."),
        status="confirmed" if broke_neck else "forming",
        criteria=criteria,
    )


def detect_double_bottom(pivots: list[dict], price: float) -> PatternHit | None:
    highs = _piv("high", pivots)
    lows = _piv("low", pivots)
    if len(lows) < 2 or not highs:
        return None
    a, b = lows[-2], lows[-1]
    if abs(a["price"] - b["price"]) / a["price"] > _LEVEL_TOL:
        return None
    peak = [hi for hi in highs if a["index"] < hi["index"] < b["index"]]
    if not peak:
        return None
    neck = max(peak, key=lambda x: x["price"])
    bottom = (a["price"] + b["price"]) / 2
    conf = 0.55 + 0.25 * (1 - abs(a["price"] - b["price"]) / a["price"] / _LEVEL_TOL)

    lifting = price > max(a["price"], b["price"])
    broke_neck = price > neck["price"]
    criteria = [
        _criterion("two lows at the same level", True,
                   f"{a['price']:.2f} and {b['price']:.2f}"),
        _criterion("price back above the lows", lifting,
                   f"{price:.2f} vs {max(a['price'], b['price']):.2f}"),
        _criterion("neckline broken", broke_neck,
                   f"{price:.2f} vs neckline {neck['price']:.2f}"),
    ]
    if not lifting:
        return None  # still sitting on the lows: nothing has turned yet

    return PatternHit(
        name="double_bottom", label="Double Bottom", direction="bullish",
        confidence=round(min(conf, 0.9) * (1.0 if broke_neck else 0.6), 2),
        pivots=[_pivot_ref(a, "low 1"), _pivot_ref(neck, "neckline"), _pivot_ref(b, "low 2")],
        measured_move=round(neck["price"] + (neck["price"] - bottom), 2),
        note=("Two successful defenses of the same floor."
              if broke_neck
              else "Two successful defenses of the same floor; the neckline is still overhead."),
        status="confirmed" if broke_neck else "forming",
        criteria=criteria,
    )


def detect_head_shoulders(pivots: list[dict], price: float) -> PatternHit | None:
    highs = _piv("high", pivots)
    lows = _piv("low", pivots)
    if len(highs) >= 3 and len(lows) >= 2:
        ls, head, rs = highs[-3], highs[-2], highs[-1]
        if head["price"] > ls["price"] and head["price"] > rs["price"] \
           and abs(ls["price"] - rs["price"]) / ls["price"] <= _LEVEL_TOL * 1.5:
            necks = [lo for lo in lows if ls["index"] < lo["index"] < rs["index"]]
            if necks:
                neck = sum(n["price"] for n in necks) / len(necks)
                return PatternHit(
                    name="head_shoulders", label="Head & Shoulders", direction="bearish",
                    confidence=0.6,
                    pivots=[_pivot_ref(ls, "left shoulder"), _pivot_ref(head, "head"),
                            _pivot_ref(rs, "right shoulder")],
                    measured_move=round(neck - (head["price"] - neck), 2),
                    note="Topping structure; break of neckline confirms.",
                    # NOTE: status stays "confirmed" even with the neckline
                    # intact, which is what this detector has always done.
                    # Reclassifying it would change conviction for every ticker
                    # carrying an unbroken H&S, so it is left alone here and the
                    # neckline state is surfaced as a criterion instead.
                    criteria=[
                        _criterion("head above both shoulders", True,
                                   f"head {head['price']:.2f}"),
                        _criterion("shoulders roughly level", True,
                                   f"{ls['price']:.2f} and {rs['price']:.2f}"),
                        _criterion("neckline broken", price < neck,
                                   f"{price:.2f} vs neckline {neck:.2f}"),
                    ],
                )
    # inverse
    if len(lows) >= 3 and len(highs) >= 2:
        ls, head, rs = lows[-3], lows[-2], lows[-1]
        if head["price"] < ls["price"] and head["price"] < rs["price"] \
           and abs(ls["price"] - rs["price"]) / ls["price"] <= _LEVEL_TOL * 1.5:
            necks = [hi for hi in highs if ls["index"] < hi["index"] < rs["index"]]
            if necks:
                neck = sum(n["price"] for n in necks) / len(necks)
                return PatternHit(
                    name="inverse_head_shoulders", label="Inverse Head & Shoulders",
                    direction="bullish", confidence=0.6,
                    pivots=[_pivot_ref(ls, "left shoulder"), _pivot_ref(head, "head"),
                            _pivot_ref(rs, "right shoulder")],
                    measured_move=round(neck + (neck - head["price"]), 2),
                    note="Bottoming structure; break of neckline confirms.",
                )
    return None


def detect_cup_handle(bars: list[OHLCBar], pivots: list[dict]) -> PatternHit | None:
    """Rounded bottom between two similar rims, then a shallow handle near the right rim."""
    highs = _piv("high", pivots)
    if len(highs) < 2 or len(bars) < 40:
        return None
    left_rim, right_rim = highs[-2], highs[-1]
    if abs(left_rim["price"] - right_rim["price"]) / left_rim["price"] > _LEVEL_TOL * 1.5:
        return None
    lo, hi = left_rim["index"], right_rim["index"]
    if hi - lo < 20:
        return None
    cup = bars[lo:hi + 1]
    cup_low = min(cup, key=lambda b: b.low)
    depth = left_rim["price"] - cup_low.low
    if depth <= 0:
        return None
    # bottom should sit near the middle (rounded, not a V or a spike at an edge)
    low_pos = (cup.index(cup_low)) / len(cup)
    if not 0.3 <= low_pos <= 0.7:
        return None
    # handle: mild pullback after the right rim
    handle = bars[hi:]
    if len(handle) < 3:
        return None
    handle_low = min(b.low for b in handle)
    if not (right_rim["price"] * 0.85 <= handle_low < right_rim["price"]):
        return None
    return PatternHit(
        name="cup_handle", label="Cup & Handle", direction="bullish", confidence=0.62,
        pivots=[_pivot_ref(left_rim, "left rim"),
                {"date": cup_low.date, "price": round(cup_low.low, 4), "role": "cup low"},
                _pivot_ref(right_rim, "right rim")],
        measured_move=round(right_rim["price"] + depth, 2),
        note="Rounded base with a shallow handle; breakout over the rim targets the cup depth.",
    )


def _trendlines(pivots: list[dict]) -> tuple[float, float] | None:
    highs = _piv("high", pivots)[-4:]
    lows = _piv("low", pivots)[-4:]
    if len(highs) < 2 or len(lows) < 2:
        return None
    hs, _ = _line([(p["index"], p["price"]) for p in highs])
    ls, _ = _line([(p["index"], p["price"]) for p in lows])
    return hs, ls


def detect_triangle(pivots: list[dict]) -> PatternHit | None:
    tl = _trendlines(pivots)
    if tl is None:
        return None
    hs, ls = tl
    highs = _piv("high", pivots)[-4:]
    lows = _piv("low", pivots)[-4:]
    avg = (highs[-1]["price"] + lows[-1]["price"]) / 2
    flat = 0.0005 * avg  # near-zero slope threshold, scaled to price
    piv = [_pivot_ref(highs[-1], "upper"), _pivot_ref(lows[-1], "lower")]
    if hs < -flat and ls > flat:
        return PatternHit(name="triangle_sym", label="Symmetrical Triangle", direction="neutral",
                          confidence=0.5, pivots=piv, note="Converging range; trade the breakout.")
    if abs(hs) <= flat and ls > flat:
        return PatternHit(name="triangle_asc", label="Ascending Triangle", direction="bullish",
                          confidence=0.55, pivots=piv, note="Flat highs, rising lows — usually resolves up.")
    if hs < -flat and abs(ls) <= flat:
        return PatternHit(name="triangle_desc", label="Descending Triangle", direction="bearish",
                          confidence=0.55, pivots=piv, note="Falling highs, flat lows — usually resolves down.")
    return None


def detect_channel(pivots: list[dict]) -> PatternHit | None:
    tl = _trendlines(pivots)
    if tl is None:
        return None
    hs, ls = tl
    highs = _piv("high", pivots)[-4:]
    lows = _piv("low", pivots)[-4:]
    avg = (highs[-1]["price"] + lows[-1]["price"]) / 2
    flat = 0.0008 * avg
    # parallel = similar slope, same sign
    if hs * ls <= 0 or abs(hs - ls) > abs(hs) * 0.6 + flat:
        return None
    piv = [_pivot_ref(highs[-1], "upper rail"), _pivot_ref(lows[-1], "lower rail")]
    if hs > flat:
        return PatternHit(name="channel_up", label="Ascending Channel", direction="bullish",
                          confidence=0.5, pivots=piv, note="Higher highs and higher lows in a parallel channel.")
    if hs < -flat:
        return PatternHit(name="channel_down", label="Descending Channel", direction="bearish",
                          confidence=0.5, pivots=piv, note="Lower highs and lower lows in a parallel channel.")
    return PatternHit(name="channel_flat", label="Horizontal Channel", direction="neutral",
                      confidence=0.45, pivots=piv, note="Sideways range between support and resistance.")


def detect_flag(bars: list[OHLCBar]) -> PatternHit | None:
    """Sharp pole then a tight, shallow counter-trend consolidation."""
    if len(bars) < 20:
        return None
    pole = bars[-15:-5]
    flag = bars[-5:]
    if len(pole) < 5 or len(flag) < 3:
        return None
    pole_move = (pole[-1].close - pole[0].close) / pole[0].close
    flag_hi = max(b.high for b in flag)
    flag_lo = min(b.low for b in flag)
    flag_range = (flag_hi - flag_lo) / flag[-1].close
    if abs(pole_move) < 0.08 or flag_range > 0.06:
        return None
    direction = "bullish" if pole_move > 0 else "bearish"
    return PatternHit(
        name="flag", label=("Bull Flag" if pole_move > 0 else "Bear Flag"),
        direction=direction, confidence=0.5,
        pivots=[{"date": pole[0].date, "price": round(pole[0].close, 4), "role": "pole start"},
                {"date": pole[-1].date, "price": round(pole[-1].close, 4), "role": "pole end"}],
        measured_move=round(flag[-1].close + (pole[-1].close - pole[0].close), 2),
        note="Tight pause after a sharp move; usually continues in the pole's direction.",
    )


def detect_patterns(bars: list[OHLCBar], pivots: list[dict], price: float) -> list[PatternHit]:
    hits = [
        detect_head_shoulders(pivots, price),
        detect_double_top(pivots, price),
        detect_double_bottom(pivots, price),
        detect_cup_handle(bars, pivots),
        detect_triangle(pivots),
        detect_channel(pivots),
        detect_flag(bars),
    ]
    found = [h for h in hits if h is not None]
    # Confirmed first, then by confidence: a shape that has actually triggered
    # outranks one that merely might, whatever their raw confidence numbers say.
    found.sort(key=lambda h: (h.status != "confirmed", -h.confidence))
    return found


def confirmed_patterns(hits: list[PatternHit]) -> list[PatternHit]:
    """Only the patterns that have actually triggered.

    The conviction pipeline must use this rather than the full list. A forming
    pattern is a description of what price is heading towards; scoring it would
    credit the stock for something that hasn't happened, which is exactly the
    kind of thing this app is supposed to refuse to do.
    """
    return [h for h in hits if h.status == "confirmed"]


# ============================ candlesticks (methodology D) ============================

def _candle_parts(b: OHLCBar) -> tuple[float, float, float, float]:
    """(range, body, upper_shadow, lower_shadow)."""
    rng = b.high - b.low
    body = abs(b.close - b.open)
    upper = b.high - max(b.open, b.close)
    lower = min(b.open, b.close) - b.low
    return rng, body, upper, lower


def detect_candles(bars: list[OHLCBar], lookback: int = _CANDLE_LOOKBACK) -> list[CandleSignal]:
    """Reversal/indecision candles in the last `lookback` bars: hammer,
    shooting star, doji, bullish/bearish harami. Context-aware — a hammer only
    counts after a decline, a shooting star only after an advance."""
    out: list[CandleSignal] = []
    n = len(bars)
    if n < 3:
        return out
    for i in range(max(2, n - lookback), n):
        b, prev = bars[i], bars[i - 1]
        rng, body, upper, lower = _candle_parts(b)
        if rng <= 0:
            continue
        pre = [bars[j].close for j in range(max(0, i - 4), i)]
        declining = len(pre) >= 2 and pre[-1] < pre[0]
        advancing = len(pre) >= 2 and pre[-1] > pre[0]
        gap_open = prev.close > 0 and abs(b.open - prev.close) / prev.close >= _GAP_MIN_PCT

        if body <= 0.1 * rng:
            out.append(CandleSignal(name="doji", label="Doji", date=b.date,
                direction="neutral", confidence=0.4,
                note="Indecision — the body is a sliver of the range."))
            continue
        if lower >= 2 * body and upper <= body and body <= 0.4 * rng and declining:
            out.append(CandleSignal(name="hammer", label="Hammer", date=b.date,
                direction="bullish", confidence=0.55,
                note="Long lower wick after a decline — sellers were rejected."))
            continue
        if upper >= 2 * body and lower <= body and body <= 0.4 * rng and advancing:
            out.append(CandleSignal(name="shooting_star", label="Shooting Star", date=b.date,
                direction="bearish", confidence=0.55,
                note="Long upper wick after an advance — buyers were rejected."))
            continue
        # harami: a small body sitting inside the prior larger, opposite body
        _, pbody, _, _ = _candle_parts(prev)
        if pbody > 0 and 0.1 * pbody <= body <= 0.6 * pbody:
            child_hi, child_lo = max(b.open, b.close), min(b.open, b.close)
            par_hi, par_lo = max(prev.open, prev.close), min(prev.open, prev.close)
            if child_hi <= par_hi and child_lo >= par_lo:
                conf = round(0.5 + (0.1 if gap_open else 0.0), 2)
                if prev.close < prev.open and b.close > b.open:
                    out.append(CandleSignal(name="bullish_harami", label="Bullish Harami",
                        date=b.date, direction="bullish", confidence=conf,
                        note="Small up-body inside a prior down-body — selling momentum stalling."))
                elif prev.close > prev.open and b.close < b.open:
                    out.append(CandleSignal(name="bearish_harami", label="Bearish Harami",
                        date=b.date, direction="bearish", confidence=conf,
                        note="Small down-body inside a prior up-body — buying momentum stalling."))
    return out


# ============================ trendlines (methodology A2/A3) ============================

def trendline_levels(pivots: list[dict], bars: list[OHLCBar]) -> list[TrendlineLevel]:
    """Diagonal support/resistance from swing lows/highs. Valid at >=2 touches;
    confidence grows per touch. `broken` when a recent close crossed the line."""
    out: list[TrendlineLevel] = []
    if not bars:
        return out
    last_idx = len(bars) - 1
    closes = [b.close for b in bars]
    for kind, want in (("low", "support"), ("high", "resistance")):
        pts = [p for p in pivots if p["kind"] == kind]
        if len(pts) < 2:
            continue
        best = None  # ((touches, recency), slope, intercept, touchers)
        for a in range(len(pts)):
            for b in range(a + 1, len(pts)):
                slope, intercept = _line([(pts[a]["index"], pts[a]["price"]),
                                          (pts[b]["index"], pts[b]["price"])])
                touchers = [p for p in pts
                            if abs((slope * p["index"] + intercept) - p["price"])
                            <= _TRENDLINE_TOL * p["price"]]
                if len(touchers) >= 2:
                    key = (len(touchers), pts[b]["index"])
                    if best is None or key > best[0]:
                        best = (key, slope, intercept, touchers)
        if best is None:
            continue
        _, slope, intercept, touchers = best
        touches = len(touchers)
        current = slope * last_idx + intercept
        conf = min(0.9, 0.4 + 0.15 * (touches - 2))
        broken = False
        for j in range(max(0, last_idx - 2), last_idx + 1):
            proj = slope * j + intercept
            if want == "support" and closes[j] < proj * (1 - _TRENDLINE_TOL):
                broken = True
            if want == "resistance" and closes[j] > proj * (1 + _TRENDLINE_TOL):
                broken = True
        piv = [{"date": p["date"], "price": p["price"], "role": "touch"}
               for p in sorted(touchers, key=lambda x: x["index"])]
        out.append(TrendlineLevel(
            kind=want, touches=touches, confidence=round(conf, 2),
            slope_per_bar=round(slope, 4), pivots=piv,
            current_value=round(current, 4), broken=broken))
    return out


def channel_evidence(trendlines: list[TrendlineLevel]) -> bool:
    """True when a support+resistance trendline pair runs roughly parallel — a
    channel whose rails act as dynamic S/R (methodology A3)."""
    sup = next((t for t in trendlines if t.kind == "support"), None)
    res = next((t for t in trendlines if t.kind == "resistance"), None)
    if not sup or not res:
        return False
    scale = max(abs(sup.slope_per_bar), abs(res.slope_per_bar), 1e-6)
    return abs(sup.slope_per_bar - res.slope_per_bar) <= 0.5 * scale


# ============================ round numbers (methodology A5) ============================

def _round_step(price: float) -> float:
    if price < 100:
        return 5.0
    if price < 500:
        return 10.0
    if price < 2000:
        return 50.0
    return 100.0


def round_number_levels(bars: list[OHLCBar], price: float | None) -> list[SRLevel]:
    """Nearest round-number levels above/below price; touches counted from
    historical stalls at each level. Returned as SRLevel(source='round')."""
    if not price or price <= 0:
        return []
    step = _round_step(price)
    base = math.floor(price / step) * step
    lows = [round(base - step * i, 4) for i in range(0, 3)]
    highs = [round(base + step * i, 4) for i in range(1, 3)]
    supports = [l for l in lows if 0 < l < price][:2]
    resistances = [l for l in highs if l > price][:2]

    def _touches(level: float) -> tuple[int, str]:
        tol = max(_ROUND_TOL * level, step * 0.05)
        hits = [b for b in bars
                if abs(b.high - level) <= tol or abs(b.low - level) <= tol
                or abs(b.close - level) <= tol]
        last = max((b.date for b in hits), default="")
        return len(hits), last

    out: list[SRLevel] = []
    for lvl in supports:
        t, last = _touches(lvl)
        out.append(SRLevel(price=lvl, kind="support", touches=t, last_touch=last, source="round"))
    for lvl in resistances:
        t, last = _touches(lvl)
        out.append(SRLevel(price=lvl, kind="resistance", touches=t, last_touch=last, source="round"))
    return out


# ============================ gap roles / gap S-R (methodology A4) ============================

_GAP_NOTE = "Gaps fill ~90% of the time eventually, though the timing is unknowable."


def classify_gaps(gap_events: list[GapEvent], bars: list[OHLCBar]) -> list[GapEvent]:
    """Assign each gap a role (breakaway/runaway/exhaustion/common) and mark
    unfilled gaps that now act as support/resistance."""
    if not gap_events:
        return gap_events
    idx_by_date = {b.date: i for i, b in enumerate(bars)}
    px = bars[-1].close if bars else None
    out: list[GapEvent] = []
    for g in gap_events:
        i = idx_by_date.get(g.date)
        role = "common"
        acts: str | None = None
        if i is not None:
            pre = [b.close for b in bars[max(0, i - 10):i]]
            post = [b.close for b in bars[i + 1:i + 6]]
            trend_before = (pre[-1] - pre[0]) / pre[0] if len(pre) >= 2 and pre[0] else 0.0
            if post and len(pre) >= 3:
                extended = (g.kind == "up" and trend_before > 0.15) or \
                           (g.kind == "down" and trend_before < -0.15)
                reversed_after = (g.kind == "up" and post[-1] < g.to_price) or \
                                 (g.kind == "down" and post[-1] > g.to_price)
                if extended and reversed_after:
                    role = "exhaustion"
            if role == "common" and len(pre) >= 5:
                hi, lo = max(pre), min(pre)
                if lo > 0 and (hi - lo) / lo < 0.06 and abs(trend_before) < 0.05:
                    role = "breakaway"
            if role == "common":
                if (g.kind == "up" and trend_before > 0.03) or \
                   (g.kind == "down" and trend_before < -0.03):
                    role = "runaway"
        if not g.filled and px is not None:
            if g.kind == "up" and g.from_price < px:
                acts = "support"
            elif g.kind == "down" and g.from_price > px:
                acts = "resistance"
        out.append(g.model_copy(update={"role": role, "acts_as": acts, "note": _GAP_NOTE}))
    return out


def gap_levels(gaps_classified: list[GapEvent]) -> list[SRLevel]:
    """S/R levels contributed by unfilled gaps whose edge acts as support/resistance."""
    out: list[SRLevel] = []
    for g in gaps_classified:
        if g.acts_as == "support":
            out.append(SRLevel(price=g.from_price, kind="support", touches=1,
                               last_touch=g.date, source="gap"))
        elif g.acts_as == "resistance":
            out.append(SRLevel(price=g.from_price, kind="resistance", touches=1,
                               last_touch=g.date, source="gap"))
    return out


# ============================ MA structure (methodology A6/A7/A8/A9) ============================

def _even_spacing(mas: dict[str, float | None]) -> bool:
    vals = [mas["ma20"], mas["ma50"], mas["ma150"], mas["ma200"]]
    if any(v is None for v in vals):
        return False
    gaps_ = [vals[i] - vals[i + 1] for i in range(3)]
    if any(g <= 0 for g in gaps_):
        return False
    return min(gaps_) >= 0.1 * max(gaps_)


def ma_structure(closes: list[float], mas: dict[str, float | None],
                 px: float | None, atr_val: float | None) -> tuple[str, float | None]:
    """Classify the moving-average posture and the price's ATR-extension from the
    20-day (the mean-reversion magnet). Returns (state, ma_extension_atr)."""
    ma20, ma50, ma200 = mas["ma20"], mas["ma50"], mas["ma200"]
    ext = round((px - ma20) / atr_val, 2) if (px is not None and ma20 and atr_val) else None
    state = "mixed"
    if px is not None and ma20 and ma50:
        align = ma_alignment(mas)
        recent_high = max(closes[-40:]) if len(closes) >= 5 else px
        long_up = ma200 is None or ma50 > ma200
        converged = bool(atr_val) and abs(ma20 - ma50) < 0.75 * atr_val
        if align == "stacked_bull" and px >= ma20 and _even_spacing(mas):
            state = "healthy_uptrend"
        elif px < ma20 and px < ma50 and long_up and converged and px < recent_high * 0.985:
            state = "topping"
        elif px > ma20 and px > ma50 and (ma200 is None or px < ma200) and align != "stacked_bull":
            state = "reclaiming"
        elif align == "stacked_bear" or (px < ma20 and px < ma50 and not long_up):
            state = "breaking_down"
    return state, ext


# ============================ volume (methodology E) ============================

def volume_read(bars: list[OHLCBar]) -> VolumeRead | None:
    """20-bar volume average, last/avg ratio, signed directional-close streak and
    expanding/contracting state. Returns None when bars carry no real volume
    (explicit absence — never fabricate a volume read)."""
    vols = [b.volume for b in bars]
    closes = [b.close for b in bars]
    if not vols or not any(v > 0 for v in vols):
        return None
    window = vols[-20:]
    avg20 = sum(window) / len(window)
    if avg20 <= 0:
        return None
    last = vols[-1]
    ratio = round(last / avg20, 2)
    streak = 0
    for i in range(len(closes) - 1, 0, -1):
        d = closes[i] - closes[i - 1]
        if d > 0 and streak >= 0:
            streak += 1
        elif d < 0 and streak <= 0:
            streak -= 1
        else:
            break
    recent, prior = vols[-5:], vols[-15:-5]
    state = "flat"
    if prior:
        r, p = sum(recent) / len(recent), sum(prior) / len(prior)
        if r > p * 1.15:
            state = "expanding"
        elif r < p * 0.85:
            state = "contracting"
    note = {
        "expanding": "Volume expanding — the move is being confirmed.",
        "contracting": "Volume contracting — the move may be exhausting.",
        "flat": "Volume roughly average.",
    }[state]
    return VolumeRead(avg20=round(avg20, 2), last=round(last, 2), ratio=ratio,
                      streak=streak, state=state, note=note)


# ============================ breakouts (methodology B1/B2) ============================

def detect_breakout(bars: list[OHLCBar], support: list[SRLevel], resistance: list[SRLevel],
                    trendlines: list[TrendlineLevel], vol: VolumeRead | None,
                    atr_val: float | None) -> BreakoutState | None:
    """Nearest level within 1 ATR -> approaching (warn before a fall/breakout);
    a close beyond a level -> broke_unconfirmed; a second consecutive close
    beyond -> confirmed (the confirmation candle); a break that snaps back inside
    with a long opposing wick / no volume -> failed (the stop-hunt trap)."""
    if len(bars) < 3 or not atr_val or atr_val <= 0:
        return None
    closes = [b.close for b in bars]
    px = closes[-1]
    levels: list[tuple[float, str]] = []
    for l in support:
        levels.append((l.price, l.source))
    for l in resistance:
        levels.append((l.price, l.source))
    for t in trendlines:
        levels.append((t.current_value, "trendline"))
    if not levels:
        return None
    ratio = vol.ratio if vol else None
    vol_conf = bool(ratio is not None and ratio >= _VOL_CONFIRM_RATIO)

    # 1) failed break (trap): within the last few bars a candle pierced the level
    #    but CLOSED back on the original side with a long opposing wick — the
    #    stop-hunt signature. A big-body genuine break (small wick) is excluded,
    #    so this never fires on a legitimate breakout arriving from the other side.
    trap_look = bars[-3:]
    for L, src in levels:
        for bb in trap_look:
            _, body, upper, lower = _candle_parts(bb)
            if bb.high > L and bb.close < L and px < L and upper >= body:
                return BreakoutState(direction="down", level=round(L, 4), level_source=src,
                    status="failed", volume_confirmed=False,
                    note="Broke above then snapped back inside — likely stop-hunt / false breakout.")
            if bb.low < L and bb.close > L and px > L and lower >= body:
                return BreakoutState(direction="up", level=round(L, 4), level_source=src,
                    status="failed", volume_confirmed=False,
                    note="Broke below then reclaimed — likely stop-hunt / false breakdown.")

    # 2) a level price has crossed within the last few bars
    def _consec_beyond(L: float, up: bool) -> int:
        c = 0
        for cl in reversed(closes):
            if (up and cl > L) or (not up and cl < L):
                c += 1
            else:
                break
        return c

    prior = closes[-4:-1] if len(closes) >= 4 else closes[:-1]
    best = None
    for L, src in levels:
        up = px > L
        crossed = (up and prior and min(prior) <= L) or ((not up) and prior and max(prior) >= L)
        if not crossed:
            continue
        dist = abs(px - L)
        if best is None or dist < best[0]:
            best = (dist, L, src, up)
    if best is not None:
        _, L, src, up = best
        beyond = _consec_beyond(L, up)
        status = "confirmed" if beyond >= 2 else "broke_unconfirmed"
        note = ("Confirmed break — a second close held beyond the level."
                if status == "confirmed" else "Broke the level; needs a confirmation close.")
        if status != "confirmed" and not vol_conf:
            note += " Volume did not confirm the break — treat it as suspect."
        return BreakoutState(direction="up" if up else "down", level=round(L, 4),
            level_source=src, status=status, volume_confirmed=vol_conf, note=note)

    # 3) approaching a level (warn before the move)
    above = [(L, src) for L, src in levels if L > px]
    below = [(L, src) for L, src in levels if L < px]
    na = min(above, key=lambda x: x[0]) if above else None
    nb = max(below, key=lambda x: x[0]) if below else None
    cand = None
    if na and (na[0] - px) <= _BREAKOUT_APPROACH_ATR * atr_val:
        cand = ("up", na[0], na[1], na[0] - px)
    if nb and (px - nb[0]) <= _BREAKOUT_APPROACH_ATR * atr_val:
        if cand is None or (px - nb[0]) < cand[3]:
            cand = ("down", nb[0], nb[1], px - nb[0])
    if cand:
        direction, L, src, _ = cand
        note = ("Approaching resistance — a confirmed break could run."
                if direction == "up"
                else "Approaching support — a breakdown here would warn of a fall.")
        return BreakoutState(direction=direction, level=round(L, 4), level_source=src,
            status="approaching", volume_confirmed=vol_conf, note=note)
    return None


def staging_guidance(breakout: BreakoutState | None, rr: float | None,
                     atr_pct: float | None) -> str:
    """The methodology's staged-entry text (B3)."""
    pct = f"~{atr_pct:.0f}%" if atr_pct else "~1 ATR"
    where = f" around {breakout.level}" if breakout is not None else ""
    return (f"Start ~20% size at the break{where}; place the stop ~1 ATR ({pct}) below the "
            f"breakout level; add after a confirmation close.")


# ============================ trade plan / advice ============================

def compute_stop(entry: float, atr_val: float | None, support: list[SRLevel],
                 trendlines: list[TrendlineLevel] | None = None) -> tuple[float | None, float | None, float | None, str]:
    """Return (stop, stop_atr, stop_structure, basis) — the tightest (closest)
    of the ATR floor, the horizontal-structure stop and any rising-trendline
    stop. Structure stops sit ~1 ATR below the level (B4/B5) so a stop-hunt wick
    can't tag them; the trendline candidate trails up with the line."""
    stop_atr = round(entry - _ATR_STOP_MULT * atr_val, 4) if atr_val else None
    buffer = (atr_val * _STRUCT_STOP_ATR_BUFFER) if atr_val else entry * 0.005
    sup = nearest_support(support, entry)
    stop_structure = round(sup.price - buffer, 4) if sup else None
    stop_trend = None
    for t in (trendlines or []):
        if t.kind == "support" and not t.broken and t.current_value < entry:
            cand = round(t.current_value - buffer, 4)
            if stop_trend is None or cand > stop_trend:
                stop_trend = cand
    # structure first so on an exact tie the meaningful basis wins over raw ATR
    candidates = [(s, b) for s, b in
                  [(stop_structure, "structure"), (stop_trend, "trendline"), (stop_atr, "atr")]
                  if s is not None and s < entry]
    if not candidates:
        return None, stop_atr, stop_structure, ""
    stop, basis = max(candidates, key=lambda x: x[0])  # highest stop = tightest risk
    return stop, stop_atr, stop_structure, basis


def target_ladder(entry: float, risk: float, resistance: list[SRLevel], trend_dir: str,
                  measured_move: float | None) -> list[TargetRung]:
    """3R base plus 4/5/6R feasibility from runway to resistance, trend and measured move."""
    res_prices = sorted(l.price for l in resistance if l.price > entry)
    rungs: list[TargetRung] = []
    for r in (3, 4, 5, 6):
        price = round(entry + r * risk, 2)
        if r == 3:
            feas, why = "base", "Standard 3:1 objective."
        else:
            capping = next((rp for rp in res_prices if rp < price), None)
            runway = capping is None
            if runway and (trend_dir == "up" or (measured_move and measured_move >= price)):
                feas = "likely"
                why = "Clear runway above; trend and structure support the extension."
            elif runway:
                feas = "possible"
                why = "Open air to this level, but momentum must keep up."
            else:
                feas = "unlikely"
                why = f"Resistance near {capping} is likely to cap this before {price}."
        rungs.append(TargetRung(r=r, price=price, feasibility=feas, why=why))
    return rungs


_DIRECTIVE_TO_RECO = {"Accumulate": "buy", "Reduce": "sell", "Avoid": "sell", "Hold": "hold"}


def build(ticker: str, daily: list[OHLCBar], price: float | None,
          account_size: float | None, risk_pct: float | None) -> StockAnalysis:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    closes = [b.close for b in daily]
    px = price if price is not None else (closes[-1] if closes else None)

    mas = moving_averages(closes)
    align = ma_alignment(mas)
    tr = trend(closes, mas)
    atr_val = atr(daily)
    atr_pct = round(atr_val / px * 100, 2) if atr_val and px else None
    pivots = swing_pivots(daily)
    support, resistance = support_resistance(pivots, px) if px else ([], [])
    gap_events = classify_gaps(gaps(daily), daily)
    patterns = detect_patterns(daily, pivots, px) if px else []
    trendlines = trendline_levels(pivots, daily) if px else []
    candles = detect_candles(daily)
    vol = volume_read(daily)
    ma_state, ma_ext = ma_structure(closes, mas, px, atr_val)
    rsi = compute_rsi(closes)
    _, _, macd_cross = compute_macd(closes)

    # Merge round-number + gap-edge levels into the structure BEFORE stop/target,
    # re-ranking strongest-then-nearest exactly like support_resistance.
    if px:
        for lvl in round_number_levels(daily, px) + gap_levels(gap_events):
            (support if lvl.kind == "support" else resistance).append(lvl)
        support.sort(key=lambda l: (-l.touches, px - l.price))
        resistance.sort(key=lambda l: (-l.touches, l.price - px))

    breakout = detect_breakout(daily, support, resistance, trendlines, vol, atr_val) if px else None

    evidence: list[Evidence] = []

    def ev(component: str, signal: str, weight: int, detail: str, data: dict | None = None) -> None:
        evidence.append(Evidence(component=component, signal=signal, weight=weight,
                                 detail=detail, data=data or {}))

    # --- trend / MA alignment ---
    if align == "stacked_bull":
        ev("ma_alignment", "bullish", 30, "Moving averages stacked bullishly (20>50>150>200).")
    elif align == "stacked_bear":
        ev("ma_alignment", "bearish", -30, "Moving averages stacked bearishly (20<50<150<200).")
    if px and mas["ma200"]:
        if px > mas["ma200"]:
            ev("ma200", "bullish", 10, "Price is above the 200-day average (long-term uptrend).")
        else:
            ev("ma200", "bearish", -10, "Price is below the 200-day average (long-term downtrend).")
    if tr == "up":
        ev("trend", "bullish", 12, "Trend is up (price over a rising 50-day, above the 200-day).")
    elif tr == "down":
        ev("trend", "bearish", -12, "Trend is down (price under a falling 50-day, below the 200-day).")

    # --- MA structure (A7/A8/A9) + MA-magnet extension (A6) ---
    if ma_state == "healthy_uptrend":
        ev("ma_structure", "bullish", 10,
           "Healthy uptrend — the four MAs are stacked and evenly spread; the 150/200 are the long-term anchor.")
    elif ma_state == "topping":
        ev("ma_structure", "bearish", -15,
           "Topping formation — MAs converging after a run and price slipping below the 20/50. Trim/exit.")
    elif ma_state == "reclaiming":
        ev("ma_structure", "bullish", 8,
           "Reclaim in progress — price recrossed the 20 and 50 day; stage entries toward the 150.")
    elif ma_state == "breaking_down":
        ev("ma_structure", "bearish", -10,
           "Breaking down — price below the short-term MAs with a weak long-term structure.")
    if ma_ext is not None and abs(ma_ext) >= _EXTENSION_WARN_ATR:
        if ma_ext > 0:
            ev("ma_extension", "bearish", -8,
               f"Stretched {ma_ext} ATR above the 20-day — mean-reversion (MA magnet) risk.",
               {"ma_extension_atr": ma_ext})
        else:
            ev("ma_extension", "bullish", 4,
               f"Stretched {abs(ma_ext)} ATR below the 20-day — snap-back potential toward the mean.",
               {"ma_extension_atr": ma_ext})

    # --- strongest chart pattern (C) ---
    # Confirmed only. Forming shapes are shown on the page but must never move
    # conviction, or the score would credit a break that hasn't happened.
    scoring_patterns = confirmed_patterns(patterns)
    if scoring_patterns:
        p = scoring_patterns[0]
        w = round(p.confidence * 25)
        if p.direction == "bullish":
            ev("pattern", "bullish", w, f"{p.label} detected ({int(p.confidence*100)}% confidence) — bullish.")
        elif p.direction == "bearish":
            ev("pattern", "bearish", -w, f"{p.label} detected ({int(p.confidence*100)}% confidence) — bearish.")
        else:
            ev("pattern", "neutral", 0, f"{p.label} forming ({int(p.confidence*100)}% confidence) — direction pending breakout.")

    # --- channel: parallel trendlines are dynamic rails (A3) ---
    if channel_evidence(trendlines):
        ev("channel", "neutral", 0,
           "Parallel trendlines form a channel — the rails act as dynamic support/resistance.")

    # --- candlesticks (D): at most one per direction ---
    bull_c = next((c for c in reversed(candles) if c.direction == "bullish"), None)
    bear_c = next((c for c in reversed(candles) if c.direction == "bearish"), None)
    if bull_c:
        ev("candle", "bullish", 6, f"{bull_c.label} ({bull_c.date}) — {bull_c.note}")
    if bear_c:
        ev("candle", "bearish", -6, f"{bear_c.label} ({bear_c.date}) — {bear_c.note}")

    # --- volume confirmation (E) ---
    if vol is not None:
        if vol.ratio >= _VOL_CONFIRM_RATIO and vol.streak > 0:
            ev("volume", "bullish", 8,
               f"Volume expanding ({vol.ratio}× the 20-day average) behind an up move — confirms it.",
               {"ratio": vol.ratio, "streak": vol.streak})
        elif vol.ratio >= _VOL_CONFIRM_RATIO and vol.streak < 0:
            ev("volume", "bearish", -8,
               f"Heavy volume ({vol.ratio}× average) into a down move — distribution.",
               {"ratio": vol.ratio, "streak": vol.streak})
        elif vol.state == "contracting" and vol.streak > 0:
            ev("volume", "bearish", -8,
               "The advance is on shrinking volume — the move is running out of fuel.",
               {"ratio": vol.ratio, "streak": vol.streak})
    # No volume evidence when bars carry no real volume — explicit absence.

    # --- breakout / breakdown (B1/B2) ---
    if breakout is not None:
        if breakout.status == "confirmed":
            if breakout.direction == "up":
                ev("breakout", "bullish", 12, f"Confirmed breakout above {breakout.level}. {breakout.note}")
            else:
                ev("breakout", "bearish", -12, f"Confirmed breakdown below {breakout.level}. {breakout.note}")
        elif breakout.status == "failed":
            if breakout.direction == "down":
                ev("breakout", "bearish", -12, f"False breakout at {breakout.level}. {breakout.note}")
            else:
                ev("breakout", "bullish", 12, f"False breakdown at {breakout.level}. {breakout.note}")
        elif breakout.status == "broke_unconfirmed":
            ev("breakout", "neutral", 0, f"Unconfirmed break at {breakout.level}. {breakout.note}")
        elif breakout.status == "approaching":
            ev("breakout", "neutral", 0, f"{breakout.note} (level {breakout.level}).")

    # --- momentum ---
    if rsi is not None:
        if rsi > 70:
            ev("rsi", "bearish", -8, f"RSI {rsi} is overbought (>70) — pullback risk.")
        elif 30 <= rsi <= 50:
            ev("rsi", "bullish", 8, f"RSI {rsi} is in the oversold-recovery zone (30–50).")
        elif rsi < 30:
            ev("rsi", "bullish", 4, f"RSI {rsi} is oversold (<30).")
    if macd_cross:
        ev("macd", "bullish", 10, "MACD crossed up within the last few sessions (momentum turning).")

    # --- position vs horizontal support, trendline support, unfilled gaps ---
    sup_tl = next((t for t in trendlines if t.kind == "support"), None)
    if px:
        ns = nearest_support(support, px)
        if ns and (px - ns.price) / px < 0.03:
            ev("support", "bullish", 6,
               f"Sitting on support near {ns.price} ({ns.touches} touch(es), {ns.source}).")
        if sup_tl and not sup_tl.broken and sup_tl.current_value < px \
                and (px - sup_tl.current_value) / px < 0.03:
            ev("trendline", "bullish", 6,
               f"Rising trendline support near {sup_tl.current_value} ({sup_tl.touches} touches).")
    unfilled_up = [g for g in gap_events if g.kind == "up" and not g.filled]
    if unfilled_up:
        ev("gap", "neutral", 0,
           f"{len(unfilled_up)} unfilled up-gap(s) below act as support ({_GAP_NOTE.lower()})")

    conviction = max(-100, min(100, sum(e.weight for e in evidence)))
    if conviction >= CONVICTION_ACCUMULATE:
        directive = "Accumulate"
    elif conviction <= CONVICTION_AVOID:
        directive = "Avoid"
    elif conviction <= CONVICTION_REDUCE:
        directive = "Reduce"
    else:
        directive = "Hold"
    recommendation = _DIRECTIVE_TO_RECO[directive]

    # trade plan
    stop = stop_atr = stop_structure = risk_ps = target = reward_ps = rr = None
    basis = ""
    rungs: list[TargetRung] = []
    shares = None
    if px:
        sup_trendlines = [t for t in trendlines if t.kind == "support"]
        stop, stop_atr, stop_structure, basis = compute_stop(px, atr_val, support, sup_trendlines)
        if stop is not None:
            risk_ps = round(px - stop, 4)
            if risk_ps > 0:
                mm = patterns[0].measured_move if patterns else None
                rungs = target_ladder(px, risk_ps, resistance, tr, mm)
                target = rungs[0].price
                reward_ps = round(target - px, 4)
                rr = round(reward_ps / risk_ps, 2)
                if account_size and risk_pct and risk_pct > 0:
                    shares = int((account_size * (risk_pct / 100.0)) / risk_ps)
                    ev("sizing", "neutral", 0,
                       f"Sized for {risk_pct}% of ${account_size:,.0f} = "
                       f"${account_size * risk_pct / 100:,.0f} risk -> {shares} shares.")
        if stop is None:
            ev("stop", "neutral", 0,
               "No stop below price yet (need a nearby support or ATR floor) — plan pending.")

    # R/R gate (B6): a sub-3:1 setup is a known skip in advance — demote Buy to Hold.
    rr_threshold = RR_THRESHOLD
    rr_pass = rr is not None and rr >= rr_threshold
    if recommendation == "buy" and not rr_pass:
        ev("rr_gate", "neutral", 0,
           f"R/R {rr if rr is not None else 'n/a'} < 3:1 — a known skip in advance; demoting Buy to Hold.")
        recommendation = "hold"

    staging = ""
    if recommendation == "buy" or (breakout and breakout.status in
                                   ("approaching", "broke_unconfirmed", "confirmed")):
        staging = staging_guidance(breakout, rr, atr_pct)

    reasons = [e.detail for e in evidence]

    return StockAnalysis(
        ticker=ticker.upper(), computed_at=now, price=round(px, 4) if px else None,
        ma20=_r(mas["ma20"]), ma50=_r(mas["ma50"]), ma150=_r(mas["ma150"]), ma200=_r(mas["ma200"]),
        ma_alignment=align, trend=tr,
        atr14=atr_val, atr_pct=atr_pct,
        support=support[:8], resistance=resistance[:8], gaps=gap_events[-6:], patterns=patterns,
        trendlines=trendlines, candles=candles, volume=vol,
        ma_state=ma_state, ma_extension_atr=ma_ext, breakout=breakout,
        recommendation=recommendation, directive=directive, conviction=conviction,
        entry=round(px, 4) if px else None, stop=stop, stop_basis=basis,
        stop_atr=stop_atr, stop_structure=stop_structure, risk_per_share=risk_ps,
        target=target, reward_per_share=reward_ps, rr=rr,
        rr_threshold=rr_threshold, rr_pass=rr_pass, targets=rungs, staging_note=staging,
        suggested_shares=shares, account_size=account_size, risk_pct=risk_pct,
        evidence=evidence, reasons=reasons,
    )


def apply_sizing(a: StockAnalysis, account_size: float | None,
                 risk_pct: float | None, account_currency: str | None = None,
                 rate_fn=None) -> StockAnalysis:
    """Fill per-user position sizing into a globally-computed analysis.

    Analyses are stored unsized (shared across users); the requesting user's
    account size / risk tolerance is applied at read time.

    Like with like: `account_size` is in `account_currency` (the user's base
    currency) while `risk_per_share` is in the ticker's trading currency. When
    they differ the risk budget is converted with `rate_fn(from, to)`
    (default: live Yahoo FX via app.currency.fx_rate). No rate means no
    sizing — the note says so rather than sizing on a guessed rate.
    """
    from app import currency as ccy_mod  # local: currency -> quotes -> ... cycle-safe

    out = a.model_copy(deep=True)
    out.account_size = account_size
    out.risk_pct = risk_pct
    out.suggested_shares = None
    out.sizing_note = ""
    ticker_ccy = out.currency or ccy_mod.currency_for_symbol(out.ticker)
    out.currency = ticker_ccy
    out.account_currency = account_currency or ticker_ccy
    if not (account_size and risk_pct and risk_pct > 0
            and out.risk_per_share and out.risk_per_share > 0):
        return out

    budget = account_size
    if account_currency and ticker_ccy and account_currency != ticker_ccy:
        rate = (rate_fn or ccy_mod.fx_rate)(account_currency, ticker_ccy)
        if not rate:
            out.sizing_note = (
                f"FX unavailable ({account_currency}→{ticker_ccy}) — not sized: the account "
                f"is in {account_currency} and {out.ticker} trades in {ticker_ccy}.")
            return out
        budget = account_size * rate
        out.sizing_note = (
            f"Account {account_size:,.0f} {account_currency} ≈ {budget:,.0f} {ticker_ccy} "
            f"at 1 {account_currency} = {rate:.4f} {ticker_ccy} (live Yahoo FX).")
    out.suggested_shares = int((budget * (risk_pct / 100.0)) / out.risk_per_share)
    return out


def _r(v: float | None) -> float | None:
    return round(v, 4) if v is not None else None
