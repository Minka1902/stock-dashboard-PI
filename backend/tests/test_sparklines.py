"""Sparkline series for the watchlist/portfolio row charts (Task 4)."""
from app import chart_data


def test_spark_ranges_reuse_known_bar_intervals():
    # Every sparkline range must map to a real chart interval so it shares the
    # TTL-cached fetch rather than inventing a new upstream call.
    for interval, tail in chart_data.SPARK_RANGES.values():
        assert interval in chart_data.INTERVALS
        assert tail > 1


def test_get_sparkline_trims_and_computes_change(monkeypatch):
    monkeypatch.setattr(chart_data, "get_bars",
                        lambda t, i: {"bars": [{"close": 100.0}, {"close": 110.0}, {"close": 105.0}]})
    s = chart_data.get_sparkline("AAPL", "1m")
    assert s["closes"] == [100.0, 110.0, 105.0]
    assert s["change_pct"] == 5.0  # (105 - 100) / 100 * 100


def test_get_sparkline_single_bar_has_no_change(monkeypatch):
    monkeypatch.setattr(chart_data, "get_bars", lambda t, i: {"bars": [{"close": 42.0}]})
    s = chart_data.get_sparkline("AAPL", "1d")
    assert s["closes"] == [42.0]
    assert s["change_pct"] is None


def test_get_sparklines_batch_flags_per_ticker_errors(monkeypatch):
    def fake(t, i):
        if t == "BAD":
            raise RuntimeError("upstream down")
        return {"bars": [{"close": 1.0}, {"close": 2.0}]}

    monkeypatch.setattr(chart_data, "get_bars", fake)
    out = chart_data.get_sparklines(["AAPL", "BAD"], "1w")
    assert out["AAPL"]["closes"] == [1.0, 2.0]
    assert out["AAPL"]["change_pct"] == 100.0
    assert out["BAD"]["error"] is True
    assert out["BAD"]["closes"] == []


def test_get_sparklines_empty_input():
    assert chart_data.get_sparklines([], "1m") == {}
