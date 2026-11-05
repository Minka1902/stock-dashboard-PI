"""On-demand analysis for arbitrary tickers."""
import json

import pytest

from app import analyze, chart_data, db, quotes
from app.models import LiveQuote, OHLCBar, OHLCSeries


def _fake_bars(n=80, start=100.0):
    bars, px = [], start
    for i in range(n):
        px *= 1.002
        bars.append({
            "time": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
            "open": round(px * 0.99, 4), "high": round(px * 1.01, 4),
            "low": round(px * 0.98, 4), "close": round(px, 4), "volume": 1000,
        })
    return bars


@pytest.fixture(autouse=True)
def _clear_caches():
    analyze._cache.clear()
    yield
    analyze._cache.clear()


def test_live_analysis_built_from_chart_bars(conn, monkeypatch):
    requested = []

    def stub_get_bars(ticker, interval):
        requested.append((ticker, interval))
        return {"ticker": ticker, "interval": interval, "as_of": "t",
                "bars": _fake_bars()}

    monkeypatch.setattr(chart_data, "get_bars", stub_get_bars)
    monkeypatch.setattr(quotes, "get_quotes", lambda tickers, **kw: [
        LiveQuote(ticker=tickers[0], price=120.0, change_pct=0.5, previous_close=119.4,
                  market_state="REGULAR", fetched_at="t"),
    ])

    result = analyze.analyze(conn, "zzzq")
    assert result["source"] == "live"
    a = result["analysis"]
    assert a is not None and a.ticker == "ZZZQ"
    assert a.price == 120.0  # live quote wins over last close
    assert a.suggested_shares is None  # unsized until apply_sizing
    assert len(result["daily"]) == 80
    assert ("ZZZQ", "1d") in requested and ("ZZZQ", "1wk") in requested

    # Second call hits the TTL cache — no new chart fetches.
    n = len(requested)
    again = analyze.analyze(conn, "ZZZQ")
    assert len(requested) == n
    assert again["analysis"].ticker == "ZZZQ"


def test_short_history_returns_no_analysis(conn, monkeypatch):
    monkeypatch.setattr(chart_data, "get_bars", lambda t, i: {"bars": _fake_bars(5)})
    result = analyze.analyze(conn, "NEWIPO")
    assert result["analysis"] is None  # never fabricate from 5 bars
    assert len(result["daily"]) == 5


def test_stored_analysis_fast_path(conn, monkeypatch):
    from app import analysis as analysis_engine
    bars = [OHLCBar(**{**b, "date": b.pop("time")}) for b in _fake_bars()]
    db.upsert_ohlc(conn, [OHLCSeries(
        ticker="NOC", interval="daily",
        bars_json=json.dumps([b.model_dump() for b in bars]), fetched_at="t",
    )])
    a = analysis_engine.build("NOC", bars, bars[-1].close, None, None)
    db.upsert_analyses(conn, [a])

    def boom(*args, **kwargs):
        raise AssertionError("stored path must not fetch")

    monkeypatch.setattr(chart_data, "get_bars", boom)
    result = analyze.analyze(conn, "NOC")
    assert result["source"] == "stored"
    assert result["analysis"].ticker == "NOC"
    assert len(result["daily"]) == 80
