"""Tests for the suggestion engine + renderers."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import db
from app.models import (
    BoomScore, Evidence, Seasonality, StockAnalysis, TechnicalSignal, WatchItem,
)
from app import suggestions
from tests.conftest import buy


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    db.init_schema(c)
    yield c
    c.close()


def _boom(ticker, score, **flags):
    base = dict(
        golden_cross=False, rsi_recovery=False, insider_cluster_buy=False,
        congress_buy=False, short_squeeze=False, wsb_rising=False, analyst_upgrade=False,
    )
    base.update(flags)
    return BoomScore(ticker=ticker, computed_at="2026-06-28T00:00:00+00:00",
                     score=score, components="{}", **base)


def _seed_seasonality(conn, ticker, returns):
    windows = [{"key": "fwd_week", "label": "Next Week", "kind": "forward",
                "per_year": [{"year": 2016 + i, "return": r} for i, r in enumerate(returns)]}]
    db.upsert_seasonality(conn, [Seasonality(
        ticker=ticker, computed_at="2026-06-28T00:00:00+00:00",
        as_of="06-29", history_years=len(returns), windows_json=json.dumps(windows),
    )])


def _setup(conn):
    for t in ("AAPL", "PLTR", "NVDA"):
        db.add_watch(conn, 0, WatchItem(ticker=t, note="", added_at="2026-06-28T00:00:00+00:00"))
    # Hold AAPL, bought at 100.
    buy(conn, 0, "AAPL", 10, 100.0, executed_at="2026-06-28T00:00:00+00:00")
    # AAPL bearish (overbought), PLTR strong bullish, NVDA mild.
    db.upsert_boom_scores(conn, [
        _boom("AAPL", -10, overbought_rsi=True),
        _boom("PLTR", 78, golden_cross=True, near_52w_high=True),
        _boom("NVDA", 30, macd_crossover=True),
    ])
    db.upsert_technical_signals(conn, [TechnicalSignal(
        ticker="AAPL", fetched_at="2026-06-28T00:00:00+00:00", price=130.0,
        change_pct=1.0, ma50=120.0, ma200=110.0, golden_cross=True, rsi14=75.0,
        high_52w=135.0, low_52w=90.0, prices_json="[130.0]",
    )])
    _seed_seasonality(conn, "PLTR", [0.05] * 8)   # strong tailwind
    _seed_seasonality(conn, "AAPL", [0.001] * 8)  # neutral


def test_holdings_alert_trim_on_bearish_with_gain(conn):
    _setup(conn)
    d = suggestions.build_digest(conn, "2026-06-29")
    aapl = next(a for a in d["holdings_alerts"] if a["ticker"] == "AAPL")
    assert aapl["pl_pct"] == 30.0
    assert "Trim" in aapl["action"]
    assert "overbought RSI" in aapl["reasons"]


def test_opportunities_exclude_held_and_rank_by_score(conn):
    _setup(conn)
    d = suggestions.build_digest(conn, "2026-06-29")
    tickers = [o["ticker"] for o in d["opportunities"]]
    assert "AAPL" not in tickers          # held → not an "opportunity"
    assert tickers[0] == "PLTR"           # highest Boom Score first
    pltr = d["opportunities"][0]
    assert "golden cross" in pltr["signals"]


def test_seasonality_tailwind_flagged(conn):
    _setup(conn)
    d = suggestions.build_digest(conn, "2026-06-29")
    pltr = next((s for s in d["seasonality"] if s["ticker"] == "PLTR"), None)
    assert pltr is not None
    assert pltr["kind"] == "tailwind"
    assert pltr["avg_pct"] == 5.0
    assert pltr["win_rate"] == 1.0


def test_market_context_present(conn):
    _setup(conn)
    d = suggestions.build_digest(conn, "2026-06-29")
    assert d["market_context"]["summary"].startswith("Market:")
    assert d["disclaimer"]


def test_earnings_soon_drives_action(conn):
    _setup(conn)
    from app.models import AnalystSignal
    soon = (datetime.now(timezone.utc).date() + timedelta(days=3)).isoformat()
    db.upsert_analyst_signals(conn, [AnalystSignal(
        ticker="AAPL", fetched_at="2026-06-28T00:00:00+00:00", next_earnings=soon,
        rec_strong_buy=1, rec_buy=1, rec_hold=1, rec_sell=0,
        recent_upgrades=0, recent_downgrades=0, latest_action=None,
        latest_firm=None, latest_to_grade=None,
    )])
    # for_date = today so "in 3d" lands inside the 7-day window.
    today = datetime.now(timezone.utc).date().isoformat()
    d = suggestions.build_digest(conn, today)
    aapl = next(a for a in d["holdings_alerts"] if a["ticker"] == "AAPL")
    assert "Earnings" in aapl["action"]
    assert any("earnings in" in r for r in aapl["reasons"])


def test_render_email_and_sms(conn):
    _setup(conn)
    d = suggestions.build_digest(conn, "2026-06-29")
    subject, text, html = suggestions.render_email(d)
    assert "2026-06-29" in subject
    assert "PLTR" in text
    assert "<html" not in html  # body fragment only; smtp wrapper adds <html>
    sms = suggestions.render_sms(d)
    assert len(sms) <= 320
    assert "app" in sms.lower()


def test_empty_state_builds_without_error(conn):
    d = suggestions.build_digest(conn, "2026-06-29")
    assert d["holdings_alerts"] == []
    assert d["opportunities"] == []
    assert d["seasonality"] == []


# ---------- TA-driven suggestions (Phase 4) ----------

def _seed_analysis(conn, ticker, reco, conviction=0, rr=3.5, rr_pass=True, evidence=None):
    db.upsert_analyses(conn, [StockAnalysis(
        ticker=ticker, computed_at="t", price=100.0, recommendation=reco,
        conviction=conviction, rr=rr, rr_pass=rr_pass,
        entry=100.0, stop=95.0, target=115.0, evidence=evidence or [])])


def test_opportunities_ta_ranked_by_conviction_and_rr(conn):
    for t in ("PLTR", "NVDA", "SNOW"):
        db.add_watch(conn, 0, WatchItem(ticker=t, note="", added_at="t"))
    db.upsert_boom_scores(conn, [_boom("PLTR", 55, golden_cross=True)])
    _seed_analysis(conn, "PLTR", "buy", conviction=60, rr=3.5)
    _seed_analysis(conn, "NVDA", "buy", conviction=40, rr=4.0)
    _seed_analysis(conn, "SNOW", "sell", conviction=-30, rr=None, rr_pass=False)
    d = suggestions.build_digest(conn, "2026-06-29")
    tickers = [o["ticker"] for o in d["opportunities"]]
    assert tickers[:2] == ["PLTR", "NVDA"]      # ranked by conviction desc
    assert "SNOW" not in tickers                 # not a Buy → not an opportunity
    top = d["opportunities"][0]
    assert top["recommendation"] == "buy" and top["rr"] == 3.5
    assert "ta_pending" not in top
    assert "golden cross" in top["signals"]      # Boom demoted to secondary evidence


def test_opportunities_rr_gate_excludes_failing_buy(conn):
    db.add_watch(conn, 0, WatchItem(ticker="PLTR", note="", added_at="t"))
    _seed_analysis(conn, "PLTR", "buy", conviction=60, rr=2.0, rr_pass=False)
    d = suggestions.build_digest(conn, "2026-06-29")
    assert d["opportunities"] == []              # buy but rr_pass False → skipped


def test_opportunities_ta_pending_fallback(conn):
    db.add_watch(conn, 0, WatchItem(ticker="PLTR", note="", added_at="t"))
    db.upsert_boom_scores(conn, [_boom("PLTR", 70, golden_cross=True)])
    d = suggestions.build_digest(conn, "2026-06-29")   # no stored analysis yet
    assert d["opportunities"][0]["ticker"] == "PLTR"
    assert d["opportunities"][0]["ta_pending"] is True


def test_holdings_ta_sell_overrides_action(conn):
    buy(conn, 0, "AAPL", 10, 100.0)
    _seed_analysis(conn, "AAPL", "sell", conviction=-40, rr_pass=False,
                   evidence=[Evidence(component="ma_structure", signal="bearish",
                                      weight=-15, detail="Topping formation — trim/exit.")])
    d = suggestions.build_digest(conn, "2026-06-29")
    aapl = next(a for a in d["holdings_alerts"] if a["ticker"] == "AAPL")
    assert aapl["action"].startswith("Reduce — TA breakdown")
    assert aapl["ta_recommendation"] == "sell"


def test_render_leads_with_ta(conn):
    db.add_watch(conn, 0, WatchItem(ticker="PLTR", note="", added_at="t"))
    _seed_analysis(conn, "PLTR", "buy", conviction=62, rr=3.4)
    d = suggestions.build_digest(conn, "2026-06-29")
    subject, text, html = suggestions.render_email(d)
    assert "BUY" in text and "PLTR" in text
    assert "BUY" in html
    sms = suggestions.render_sms(d)
    assert len(sms) <= 320 and "PLTR" in sms


# ---- multi-watchlist coverage + new-idea split (Task 7) ----

def test_suggestions_span_every_watchlist(conn):
    """A ticker on a second list must be visible to the digest — it used to be
    invisible because build_digest resolved to the user's first list only."""
    first = db.get_watchlists(conn, 0)[0].id
    second = db.create_watchlist(conn, 0, "Speculative", "2026-06-28T00:00:00+00:00").id
    db.add_watch(conn, 0, WatchItem(ticker="AAPL", note="", added_at="t"), first)
    db.add_watch(conn, 0, WatchItem(ticker="PLTR", note="", added_at="t"), second)
    db.upsert_boom_scores(conn, [_boom("AAPL", 20), _boom("PLTR", 78, golden_cross=True)])

    d = suggestions.build_digest(conn, "2026-06-29")
    tickers = [o["ticker"] for o in d["opportunities"]]
    assert "PLTR" in tickers, "second list was not scanned"
    assert "AAPL" in tickers

    # each row says which list it came from
    pltr = next(o for o in d["opportunities"] if o["ticker"] == "PLTR")
    assert pltr["lists"] == ["Speculative"]


def test_hot_count_counts_every_list(conn):
    first = db.get_watchlists(conn, 0)[0].id
    second = db.create_watchlist(conn, 0, "Spec", "t").id
    db.add_watch(conn, 0, WatchItem(ticker="AAPL", note="", added_at="t"), first)
    db.add_watch(conn, 0, WatchItem(ticker="PLTR", note="", added_at="t"), second)
    db.upsert_boom_scores(conn, [_boom("AAPL", 60), _boom("PLTR", 70)])
    d = suggestions.build_digest(conn, "2026-06-29")
    assert d["market_context"]["hot_count"] == 2


def test_new_ideas_are_separate_from_tracked_names(conn):
    """Unwatched candidates get their own ranked pool so tracked names can't
    crowd them out (and vice versa)."""
    first = db.get_watchlists(conn, 0)[0].id
    db.add_watch(conn, 0, WatchItem(ticker="AAPL", note="", added_at="t"), first)
    db.upsert_boom_scores(conn, [_boom("AAPL", 40), _boom("GME", 90, congress_buy=True)])
    # GME becomes a candidate via a recent congress purchase
    from app.models import CongressTrade
    recent = (datetime.now(timezone.utc).date() - timedelta(days=3)).isoformat()
    db.upsert_congress_trades(conn, [CongressTrade(
        trade_hash="c1", representative="Rep. X", party="D", state="NY",
        ticker="GME", asset_description="GameStop", transaction_date=recent,
        transaction_type="Purchase", amount_range="$1,001 - $15,000",
        filed_at=recent, chamber="house",
    )])

    d = suggestions.build_digest(conn, "2026-06-29")
    tracked = [o["ticker"] for o in d["opportunities"]]
    fresh = [o["ticker"] for o in d["new_ideas"]]
    assert "AAPL" in tracked and "AAPL" not in fresh
    assert "GME" in fresh and "GME" not in tracked
    assert all(o["watched"] is False for o in d["new_ideas"])


def test_watchlist_map_reports_every_list_holding_a_ticker(conn):
    first = db.get_watchlists(conn, 0)[0].id
    second = db.create_watchlist(conn, 0, "Spec", "t").id
    db.add_watch(conn, 0, WatchItem(ticker="AAPL", note="", added_at="t"), first)
    db.add_watch(conn, 0, WatchItem(ticker="AAPL", note="", added_at="t"), second)
    assert db.get_watchlist_map(conn, 0)["AAPL"] == ["My Watchlist", "Spec"]
