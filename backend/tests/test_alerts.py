"""Tests for the alert detector + alert DB layer."""
import sqlite3
from datetime import datetime, timezone

import pytest

from app import alerts, db, notify
from app.models import (
    AnalystSignal, BoomScore, BreakoutState, CongressTrade, Evidence,
    StockAnalysis, WatchItem,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    db.init_schema(c)
    yield c
    c.close()


def _watch(conn, ticker="GME"):
    db.add_watch(conn, 0, WatchItem(ticker=ticker, note="", added_at="t"))


def _boom(conn, ticker="GME", score=40, **flags):
    base = dict(
        golden_cross=False, rsi_recovery=False, insider_cluster_buy=False,
        congress_buy=False, short_squeeze=False, wsb_rising=False, analyst_upgrade=False,
    )
    base.update(flags)
    db.upsert_boom_scores(conn, [BoomScore(
        ticker=ticker, computed_at="2026-06-28T00:00:00+00:00", score=score,
        components="{}", **base,
    )])


def test_baseline_run_is_silent_then_cross_fires_once(conn):
    _watch(conn)
    _boom(conn, score=40)
    assert alerts.detect(conn) == []          # no prior state → baseline only

    _boom(conn, score=70)                       # crosses 60
    fired = alerts.detect(conn)
    assert len(fired) == 1
    assert fired[0].type == "boom_cross"
    assert fired[0].severity == "high"
    db.upsert_alerts(conn, fired)

    # Re-run with unchanged state → no new alert (transition gone + dedup).
    assert alerts.detect(conn) == []


def test_golden_cross_transition(conn):
    _watch(conn)
    _boom(conn, score=20, golden_cross=False)
    alerts.detect(conn)                          # baseline
    _boom(conn, score=20, golden_cross=True)
    fired = alerts.detect(conn)
    assert [a.type for a in fired] == ["golden_cross"]


def test_earnings_alert_dedups_by_date(conn):
    _watch(conn)
    _boom(conn, score=20, earnings_soon=True)
    db.upsert_analyst_signals(conn, [AnalystSignal(
        ticker="GME", fetched_at="t", next_earnings="2026-07-01",
        rec_strong_buy=1, rec_buy=1, rec_hold=1, rec_sell=0,
        recent_upgrades=0, recent_downgrades=0,
        latest_action=None, latest_firm=None, latest_to_grade=None,
    )])
    fired = alerts.detect(conn)
    assert any(a.type == "earnings_soon" for a in fired)
    db.upsert_alerts(conn, fired)
    # Same earnings date → no duplicate.
    again = [a for a in alerts.detect(conn) if a.type == "earnings_soon"]
    assert again == []


def test_large_congress_purchase_fires(conn):
    _watch(conn)
    _boom(conn, score=20)
    today = datetime.now(timezone.utc).date().isoformat()
    db.upsert_congress_trades(conn, [CongressTrade(
        trade_hash="h1", representative="Rep X", party="D", state="CA",
        ticker="GME", asset_description="Stock", transaction_date=today,
        transaction_type="Purchase", amount_range="> $500,000",
        filed_at=today, chamber="house",
    )])
    fired = [a for a in alerts.detect(conn) if a.type == "congress_buy"]
    assert len(fired) == 1
    assert fired[0].severity == "medium"


def test_small_congress_purchase_ignored(conn):
    _watch(conn)
    _boom(conn, score=20)
    today = datetime.now(timezone.utc).date().isoformat()
    db.upsert_congress_trades(conn, [CongressTrade(
        trade_hash="h2", representative="Rep Y", party="R", state="TX",
        ticker="GME", asset_description="Stock", transaction_date=today,
        transaction_type="Purchase", amount_range="$1,001 - $15,000",
        filed_at=today, chamber="house",
    )])
    assert [a for a in alerts.detect(conn) if a.type == "congress_buy"] == []


# ---------- TA transition alerts (Phase 3) ----------

def _ta_analysis(ticker="AAPL", recommendation="hold", breakout_status=None,
                 direction="up", ma_state="mixed", conviction=0, level=100.0):
    bo = (BreakoutState(direction=direction, level=level, level_source="horizontal",
                        status=breakout_status) if breakout_status else None)
    return StockAnalysis(
        ticker=ticker, computed_at="t", price=100.0, recommendation=recommendation,
        ma_state=ma_state, conviction=conviction, breakout=bo,
        entry=100.0, stop=95.0, target=115.0, rr=3.0,
        evidence=[Evidence(component="pattern", signal="bullish", weight=10,
                           detail="Cup & Handle detected — bullish.")],
    )


def test_ta_first_run_seeds_without_firing(conn):
    _watch(conn, "AAPL")
    db.upsert_analyses(conn, [_ta_analysis(recommendation="buy",
                                           breakout_status="confirmed")])
    # No prior TA snapshot → the first pass only records state.
    assert alerts.detect(conn) == []
    state = db.get_alert_state(conn, "AAPL")
    assert state["ta_recommendation"] == "buy"
    assert state["ta_breakout_status"] == "confirmed"


def test_ta_recommendation_change_fires_once(conn):
    _watch(conn, "AAPL")
    db.upsert_analyses(conn, [_ta_analysis(recommendation="hold")])
    alerts.detect(conn)                                    # seed
    db.upsert_analyses(conn, [_ta_analysis(recommendation="buy")])
    fired = alerts.detect(conn)
    assert [f.type for f in fired] == ["recommendation_change"]
    assert fired[0].severity == "high"                     # buy → high
    db.upsert_alerts(conn, fired)
    assert alerts.detect(conn) == []                       # transition gone + dedup


def test_ta_breakout_confirmed_transition(conn):
    _watch(conn, "AAPL")
    db.upsert_analyses(conn, [_ta_analysis(breakout_status="approaching", direction="up")])
    alerts.detect(conn)                                    # seed
    db.upsert_analyses(conn, [_ta_analysis(breakout_status="confirmed", direction="up")])
    fired = [f.type for f in alerts.detect(conn)]
    assert "breakout_confirmed" in fired


def test_ta_breakdown_warning_is_high_and_pushed(conn, monkeypatch):
    _watch(conn, "AAPL")
    db.upsert_analyses(conn, [_ta_analysis(breakout_status=None)])
    alerts.detect(conn)                                    # seed
    db.upsert_analyses(conn, [_ta_analysis(breakout_status="approaching",
                                           direction="down", level=90.0)])
    pushed = []
    monkeypatch.setattr(notify, "push_alert", lambda c, al: pushed.append(al))
    fired = alerts.detect(conn)
    assert any(f.type == "breakdown_warning" and f.severity == "high" for f in fired)
    assert any(al.type == "breakdown_warning" for al in pushed)


def test_ta_topping_formation_transition(conn):
    _watch(conn, "AAPL")
    db.upsert_analyses(conn, [_ta_analysis(ma_state="healthy_uptrend")])
    alerts.detect(conn)                                    # seed
    db.upsert_analyses(conn, [_ta_analysis(ma_state="topping")])
    fired = [f.type for f in alerts.detect(conn)]
    assert "topping_formation" in fired


def test_mark_read_and_unread_count(conn):
    _watch(conn)
    _boom(conn, score=40); alerts.detect(conn)
    _boom(conn, score=70)
    db.upsert_alerts(conn, alerts.detect(conn))
    assert db.count_unread_alerts(conn, 0) == 1
    db.mark_alerts_read(conn, 0)  # all
    assert db.count_unread_alerts(conn, 0) == 0
    assert db.get_alerts(conn, 0)[0].read is True
