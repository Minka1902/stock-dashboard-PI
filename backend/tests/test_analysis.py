"""Unit tests for the pure technical-analysis engine (app/analysis.py)."""
from app import analysis
from app.models import OHLCBar, SRLevel


def _d(i: int) -> str:
    # deterministic ascending YYYY-MM-DD-ish label (only ordering matters)
    return f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}"


def series(closes, hi_pad=0.3, lo_pad=0.3, vol=1_000_000):
    """Build OHLC bars from a close series; open = prior close."""
    bars = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        bars.append(OHLCBar(date=_d(i), open=o, high=max(o, c) + hi_pad,
                            low=min(o, c) - lo_pad, close=c, volume=vol))
    return bars


# ---------- ATR ----------

def test_atr_constant_range():
    # each bar spans 2.0 with close at the midpoint -> true range is 2.0 throughout
    bars = [OHLCBar(date=_d(i), open=10, high=11, low=9, close=10, volume=1) for i in range(20)]
    assert analysis.atr(bars, 14) == 2.0


def test_atr_none_when_too_short():
    bars = [OHLCBar(date=_d(i), open=10, high=11, low=9, close=10, volume=1) for i in range(5)]
    assert analysis.atr(bars, 14) is None


# ---------- gaps ----------

def _bar(i, o, c):
    return OHLCBar(date=_d(i), open=o, high=max(o, c) + 0.1, low=min(o, c) - 0.1, close=c, volume=1_000)


def test_gap_up_detected_and_unfilled():
    # 5 bars at 100, then a bar that OPENS at 110 (+10% gap) and never trades back to 100
    bars = [_bar(i, 100, 100) for i in range(5)] + [_bar(5, 110, 110)] + [_bar(i, 110, 110) for i in range(6, 11)]
    g = [x for x in analysis.gaps(bars) if x.kind == "up"]
    assert len(g) == 1
    assert g[0].filled is False
    assert round(g[0].pct) == 10


def test_gap_down_and_fill():
    # gap down (opens at 90), then later a bar trades back up through 100 -> filled
    bars = [_bar(i, 100, 100) for i in range(5)] + [_bar(5, 90, 90)] + [_bar(i, 95 + i, 96 + i) for i in range(6, 11)]
    g = [x for x in analysis.gaps(bars) if x.kind == "down"]
    assert len(g) == 1 and g[0].filled is True


# ---------- MA alignment ----------

def test_ma_alignment_bull_and_bear():
    assert analysis.ma_alignment({"ma20": 4, "ma50": 3, "ma150": 2, "ma200": 1}) == "stacked_bull"
    assert analysis.ma_alignment({"ma20": 1, "ma50": 2, "ma150": 3, "ma200": 4}) == "stacked_bear"
    assert analysis.ma_alignment({"ma20": 2, "ma50": 4, "ma150": 1, "ma200": 3}) == "mixed"


# ---------- support / resistance ----------

def test_support_resistance_clusters_and_splits():
    pivots = [
        {"index": 1, "date": "2025-01-02", "price": 90.0, "kind": "low"},
        {"index": 2, "date": "2025-01-05", "price": 90.5, "kind": "low"},   # clusters with 90
        {"index": 3, "date": "2025-01-09", "price": 110.0, "kind": "high"},
    ]
    support, resistance = analysis.support_resistance(pivots, price=100.0)
    assert len(support) == 1 and support[0].touches == 2
    assert support[0].kind == "support" and 90 <= support[0].price <= 91
    assert len(resistance) == 1 and resistance[0].kind == "resistance"


# ---------- stop selection (tighter of ATR & structure) ----------

def test_compute_stop_prefers_tighter_structure():
    # Structure buffer is now a full 1 ATR below support (stop-hunt protection, B4).
    support = [SRLevel(price=98.0, kind="support", touches=3, last_touch="2025-01-01")]
    stop, s_atr, s_struct, basis = analysis.compute_stop(100.0, atr_val=4.0, support=support)
    assert s_atr == 92.0                       # 100 - 2*4
    assert s_struct == 94.0                    # 98 - 1.0*4 buffer
    assert stop == 94.0 and basis == "structure"   # higher stop = tighter risk


def test_compute_stop_atr_when_no_support():
    stop, s_atr, s_struct, basis = analysis.compute_stop(100.0, atr_val=4.0, support=[])
    assert s_struct is None and stop == 92.0 and basis == "atr"


# ---------- target ladder ----------

def test_target_ladder_base_and_capping():
    res = [SRLevel(price=122.0, kind="resistance", touches=2, last_touch="2025-01-01")]
    rungs = analysis.target_ladder(entry=100.0, risk=5.0, resistance=res, trend_dir="up", measured_move=None)
    by_r = {r.r: r for r in rungs}
    assert by_r[3].price == 115.0 and by_r[3].feasibility == "base"
    assert by_r[4].price == 120.0 and by_r[4].feasibility == "likely"   # runway, uptrend
    assert by_r[5].feasibility == "unlikely"                            # 125 capped by 122
    assert "122" in by_r[5].why


# ---------- patterns ----------

def test_double_top_detected():
    # up to 100, pull to 90, back to 100, then roll over to 84
    closes = (list(range(80, 101)) + list(range(99, 89, -1)) + list(range(91, 101))
              + list(range(99, 83, -1)))
    bars = series(closes)
    pivots = analysis.swing_pivots(bars)
    hit = analysis.detect_double_top(pivots, price=bars[-1].close)
    assert hit is not None and hit.direction == "bearish"
    assert {p["role"] for p in hit.pivots} == {"peak 1", "neckline", "peak 2"}


def test_double_bottom_detected():
    closes = (list(range(100, 79, -1)) + list(range(81, 91)) + list(range(89, 79, -1))
              + list(range(81, 101)))
    bars = series(closes)
    pivots = analysis.swing_pivots(bars)
    hit = analysis.detect_double_bottom(pivots, price=bars[-1].close)
    assert hit is not None and hit.direction == "bullish"


# ---------- end-to-end build ----------

def test_build_uptrend_is_constructive():
    closes = [50 + i * 0.5 for i in range(220)]     # long, clean uptrend
    bars = series(closes)
    a = analysis.build("TEST", bars, price=closes[-1], account_size=50_000, risk_pct=1.0)
    assert a.ma_alignment == "stacked_bull"
    assert a.trend == "up"
    assert a.conviction > 0
    assert a.directive in ("Accumulate", "Hold")
    assert a.entry is not None and a.stop is not None and a.stop < a.entry
    assert a.target > a.entry
    assert a.rr == 3.0
    assert a.suggested_shares and a.suggested_shares > 0
    assert len(a.targets) == 4 and a.targets[0].feasibility == "base"


# ---------- trade-plan invariants ----------

def _uptrend_bars(n=260):
    # gentle uptrend with periodic pullbacks so pivots/support exist
    closes = []
    px = 50.0
    for i in range(n):
        px *= 1.004 if (i // 10) % 3 != 2 else 0.997
        closes.append(round(px, 2))
    return series(closes)


def test_build_trade_plan_invariants():
    bars = _uptrend_bars()
    price = bars[-1].close
    a = analysis.build("TEST", bars, price, account_size=50_000, risk_pct=1.0)

    assert a.entry == round(price, 4)
    if a.stop is not None:
        # long setup: stop below entry, 3R target above, R math consistent
        assert a.stop < a.entry
        assert a.risk_per_share == round(a.entry - a.stop, 4)
        assert a.target == round(a.entry + 3 * a.risk_per_share, 2)
        assert a.rr == round((a.target - a.entry) / a.risk_per_share, 2)
        # ladder: rungs strictly increasing at 3/4/5/6R with the base first
        assert [r.r for r in a.targets] == [3, 4, 5, 6]
        assert all(a.targets[i].price < a.targets[i + 1].price for i in range(3))
        assert a.targets[0].feasibility == "base"
        # position size = risk$ / risk-per-share (int-truncated)
        assert a.suggested_shares == int(50_000 * 0.01 / a.risk_per_share)


def test_build_without_account_size_has_no_share_count():
    bars = _uptrend_bars()
    a = analysis.build("TEST", bars, bars[-1].close, account_size=None, risk_pct=1.0)
    assert a.suggested_shares is None


def test_swing_pivots_merge_plateau_as_one_peak():
    # A flat 3-bar top must register as a single high pivot, not several.
    closes = [10, 11, 12, 13, 14, 15, 15, 15, 14, 13, 12, 11, 10, 9, 8]
    bars = series([float(c) for c in closes], hi_pad=0.0, lo_pad=0.0)
    pivots = analysis.swing_pivots(bars, k=3)
    highs = [p for p in pivots if p["kind"] == "high"]
    assert len(highs) == 1
    assert highs[0]["price"] == 15.0
