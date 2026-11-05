"""Forming patterns: shown, explained, and never allowed to move conviction."""
from app import analysis
from app.models import PatternHit


def _pivot(index, price, kind, date="2026-07-01"):
    return {"index": index, "price": price, "kind": kind, "date": date}


def _double_top_pivots():
    """Two level peaks at 100 with a trough (the neckline) at 90 between them."""
    return [
        _pivot(0, 100.0, "high", "2026-06-01"),
        _pivot(5, 90.0, "low", "2026-06-10"),
        _pivot(10, 100.5, "high", "2026-06-20"),
    ]


def test_double_top_is_forming_while_the_neckline_holds():
    # Rolled off the peaks but still above the neckline: the shape exists, the
    # break has not happened.
    hit = analysis.detect_double_top(_double_top_pivots(), price=95.0)
    assert hit is not None
    assert hit.status == "forming"
    unmet = [c.name for c in hit.criteria if not c.met]
    assert "neckline broken" in unmet
    # The reading is stated, not just the verdict.
    neck_c = next(c for c in hit.criteria if c.name == "neckline broken")
    assert "90" in neck_c.detail and "95" in neck_c.detail


def test_double_top_confirms_below_the_neckline():
    hit = analysis.detect_double_top(_double_top_pivots(), price=85.0)
    assert hit is not None
    assert hit.status == "confirmed"
    assert all(c.met for c in hit.criteria)


def test_double_top_absent_while_price_still_presses_the_highs():
    """No topping shape to describe while price is at the peaks."""
    assert analysis.detect_double_top(_double_top_pivots(), price=101.0) is None


def test_forming_scores_lower_than_confirmed():
    forming = analysis.detect_double_top(_double_top_pivots(), price=95.0)
    confirmed = analysis.detect_double_top(_double_top_pivots(), price=85.0)
    assert forming.confidence < confirmed.confidence


def _double_bottom_pivots():
    return [
        _pivot(0, 100.0, "low", "2026-06-01"),
        _pivot(5, 110.0, "high", "2026-06-10"),
        _pivot(10, 100.5, "low", "2026-06-20"),
    ]


def test_double_bottom_is_forming_below_the_neckline():
    hit = analysis.detect_double_bottom(_double_bottom_pivots(), price=105.0)
    assert hit is not None
    assert hit.status == "forming"
    assert "neckline broken" in [c.name for c in hit.criteria if not c.met]


def test_double_bottom_confirms_above_the_neckline():
    hit = analysis.detect_double_bottom(_double_bottom_pivots(), price=115.0)
    assert hit is not None
    assert hit.status == "confirmed"


# ---- the regression that matters -------------------------------------------

def test_confirmed_patterns_filters_forming():
    hits = [
        PatternHit(name="a", label="A", direction="bullish", confidence=0.8,
                   pivots=[], status="confirmed"),
        PatternHit(name="b", label="B", direction="bearish", confidence=0.9,
                   pivots=[], status="forming"),
    ]
    assert [h.name for h in analysis.confirmed_patterns(hits)] == ["a"]


def test_detect_patterns_ranks_confirmed_above_forming():
    """A higher-confidence forming shape must not outrank a confirmed one."""
    hits = [
        PatternHit(name="conf", label="C", direction="bullish", confidence=0.5,
                   pivots=[], status="confirmed"),
        PatternHit(name="form", label="F", direction="bullish", confidence=0.95,
                   pivots=[], status="forming"),
    ]
    hits.sort(key=lambda h: (h.status != "confirmed", -h.confidence))
    assert [h.name for h in hits] == ["conf", "form"]


def test_forming_patterns_do_not_change_conviction():
    """The guard: adding a forming hit must leave the score byte-identical.

    Conviction feeds the recommendation, which feeds recommendation_change
    alerts — a forming pattern leaking into the score would silently retune
    every one of them.
    """
    confirmed_only = [
        PatternHit(name="dt", label="Double Top", direction="bearish",
                   confidence=0.8, pivots=[], status="confirmed"),
    ]
    with_forming = confirmed_only + [
        PatternHit(name="db", label="Double Bottom", direction="bullish",
                   confidence=0.95, pivots=[], status="forming"),
    ]
    assert analysis.confirmed_patterns(with_forming) == confirmed_only
    # ...and the one that would be scored is unchanged.
    assert analysis.confirmed_patterns(with_forming)[0].name == "dt"


def test_head_shoulders_reports_neckline_state():
    """H&S keeps its long-standing confirmed status (changing it would move
    conviction for every ticker carrying one), but now says whether the
    neckline it depends on has actually broken."""
    pivots = [
        _pivot(0, 100.0, "high", "2026-06-01"),
        _pivot(3, 90.0, "low", "2026-06-05"),
        _pivot(6, 115.0, "high", "2026-06-10"),
        _pivot(9, 91.0, "low", "2026-06-15"),
        _pivot(12, 100.5, "high", "2026-06-20"),
    ]
    intact = analysis.detect_head_shoulders(pivots, price=95.0)
    assert intact is not None
    assert intact.status == "confirmed"
    assert not next(c for c in intact.criteria if c.name == "neckline broken").met

    broken = analysis.detect_head_shoulders(pivots, price=85.0)
    assert next(c for c in broken.criteria if c.name == "neckline broken").met
