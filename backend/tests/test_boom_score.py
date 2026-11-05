"""Unit tests for the boom_score source module."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import db
from app.models import (
    AaiiSentiment, AnalystSignal, BoomScore, CongressTrade, InsiderTrade,
    MarginDebtPoint, PutCallPoint, Seasonality, ShortInterest, SocialSentiment,
    TechnicalSignal, VixPoint, WatchItem,
)
from app.sources import boom_score as boom_score_source


# Dates anchored to "now" so time-decay / earnings-window tests stay valid as the
# wall clock advances (the boom score decays congress/insider signals over 30 days
# and flags earnings within 7 days, both relative to the current date).
_TODAY = datetime.now(timezone.utc).date()


def _days_ago(n: int) -> str:
    return (_TODAY - timedelta(days=n)).isoformat()


def _days_ahead(n: int) -> str:
    return (_TODAY + timedelta(days=n)).isoformat()


@pytest.fixture
def score_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_schema(conn)
    yield conn
    conn.close()


def _add_ticker(conn, ticker="GME"):
    db.add_watch(conn, 0, WatchItem(ticker=ticker, note="", added_at="2026-06-22T10:00:00+00:00"))


def _add_technical(conn, ticker="GME", golden_cross=None, rsi14=55.0,
                   price=100.0, high_52w=150.0, rel_volume=None, change_pct=1.0):
    if golden_cross is True:
        ma50, ma200 = 101.0, 100.0
    elif golden_cross is False:
        ma50, ma200 = 99.0, 100.0
    else:
        ma50, ma200 = None, None
    db.upsert_technical_signals(conn, [TechnicalSignal(
        ticker=ticker, fetched_at="2026-06-22T10:00:00+00:00",
        price=price, change_pct=change_pct, ma50=ma50,
        ma200=ma200, golden_cross=golden_cross, rsi14=rsi14,
        high_52w=high_52w, low_52w=80.0, prices_json="[100.0]",
        rel_volume=rel_volume,
    )])


def _add_insider_buy(conn, ticker="GME", accession="A1", date=None):
    date = date or _days_ago(7)
    db.upsert_trades(conn, [InsiderTrade(
        accession=accession, ticker=ticker, company="Co", owner="Owner",
        role="Director", transaction_date=date, transaction_type="Buy",
        shares=100.0, value=10000.0, filing_url="https://sec.gov/x",
        filed_at="2026-06-10T10:00:00+00:00",
    )])


def _add_congress_purchase(conn, ticker="GME", date=None, amount_range="$15,001 - $50,000"):
    date = date or _TODAY.isoformat()  # today → time-decay multiplier = 1.0
    db.upsert_congress_trades(conn, [CongressTrade(
        trade_hash="hash001", representative="Rep Smith", party="D",
        state="CA", ticker=ticker, asset_description="Stock",
        transaction_date=date, transaction_type="Purchase",
        amount_range=amount_range, filed_at="2026-06-22", chamber="house",
    )])


def _add_short(conn, ticker="GME", squeeze=False, pct=0.10):
    db.upsert_short_interest(conn, [ShortInterest(
        ticker=ticker, fetched_at="2026-06-22T10:00:00+00:00",
        shares_short=1_000_000, short_pct_float=pct, days_to_cover=2.0,
        prior_month_shares=900_000, squeeze_flag=squeeze,
    )])


def _add_social(conn, ticker="GME", rank_change=0):
    db.upsert_social_sentiment(conn, [SocialSentiment(
        ticker=ticker, fetched_at="2026-06-22T10:00:00+00:00",
        mentions=500, upvotes=200, rank=5, rank_24h_ago=5 + rank_change,
        rank_change=rank_change,
    )])


def _add_analyst(conn, ticker="GME", upgrades=0):
    db.upsert_analyst_signals(conn, [AnalystSignal(
        ticker=ticker, fetched_at="2026-06-22T10:00:00+00:00",
        next_earnings=_days_ahead(34), rec_strong_buy=5, rec_buy=10,
        rec_hold=3, rec_sell=1, recent_upgrades=upgrades, recent_downgrades=0,
        latest_action="up" if upgrades > 0 else None, latest_firm="GS", latest_to_grade="Buy",
    )])


def _seed_seasonality(conn, ticker="GME", returns=(0.05,) * 8):
    windows = [{"key": "fwd_week", "label": "Next Week", "kind": "forward",
                "per_year": [{"year": 2016 + i, "return": r} for i, r in enumerate(returns)]}]
    db.upsert_seasonality(conn, [Seasonality(
        ticker=ticker, computed_at="2026-06-28T00:00:00+00:00",
        as_of="06-29", history_years=len(returns), windows_json=json.dumps(windows),
    )])


def test_zero_score_no_signals(score_conn):
    _add_ticker(score_conn)
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert len(scores) == 1
    assert scores[0].score == 0
    assert scores[0].golden_cross is False
    assert json.loads(scores[0].components) == {}


def test_golden_cross_adds_20(score_conn):
    _add_ticker(score_conn)
    _add_technical(score_conn, golden_cross=True, rsi14=60.0)
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].golden_cross is True
    assert scores[0].score >= 20
    assert "golden_cross" in json.loads(scores[0].components)


def test_rsi_recovery_zone_adds_10(score_conn):
    _add_ticker(score_conn)
    # golden_cross=None avoids triggering death_cross so score reflects rsi_recovery only.
    _add_technical(score_conn, golden_cross=None, rsi14=40.0)
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].rsi_recovery is True
    assert scores[0].score >= 10


def test_rsi_outside_zone_does_not_fire(score_conn):
    _add_ticker(score_conn)
    _add_technical(score_conn, golden_cross=None, rsi14=55.0)
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].rsi_recovery is False


def test_insider_cluster_two_buys_adds_20(score_conn):
    _add_ticker(score_conn)
    # Relative to today: the cluster check looks back a fixed window, so fixed
    # dates silently age out and the test starts failing on a calendar date.
    _add_insider_buy(score_conn, accession="A1", date=_days_ago(7))
    _add_insider_buy(score_conn, accession="A2", date=_days_ago(6))
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].insider_cluster_buy is True
    assert scores[0].score >= 20


def test_insider_cluster_one_buy_does_not_fire(score_conn):
    _add_ticker(score_conn)
    _add_insider_buy(score_conn, accession="A1", date=_days_ago(7))
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].insider_cluster_buy is False


def test_congress_buy_adds_15(score_conn):
    _add_ticker(score_conn)
    _add_congress_purchase(score_conn)
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].congress_buy is True
    assert scores[0].score >= 15


def test_short_squeeze_adds_10(score_conn):
    _add_ticker(score_conn)
    _add_short(score_conn, squeeze=True, pct=0.25)
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].short_squeeze is True
    assert scores[0].score >= 10


def test_wsb_rising_rank_change_gte5_adds_10(score_conn):
    _add_ticker(score_conn)
    _add_social(score_conn, rank_change=5)
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].wsb_rising is True
    assert scores[0].score >= 10


def test_wsb_rising_rank_change_lt5_does_not_fire(score_conn):
    _add_ticker(score_conn)
    _add_social(score_conn, rank_change=4)
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].wsb_rising is False


def test_analyst_upgrade_adds_15(score_conn):
    _add_ticker(score_conn)
    _add_analyst(score_conn, upgrades=2)
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].analyst_upgrade is True
    assert scores[0].score >= 15


def test_max_score_all_signals(score_conn):
    _add_ticker(score_conn)
    _add_technical(score_conn, golden_cross=True, rsi14=40.0)
    _add_insider_buy(score_conn, accession="A1")
    _add_insider_buy(score_conn, accession="A2")
    _add_congress_purchase(score_conn)
    _add_short(score_conn, squeeze=True, pct=0.25)
    _add_social(score_conn, rank_change=10)
    _add_analyst(score_conn, upgrades=3)
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].score == 100  # 20+20+15+15+10+10+10


def test_compute_all_empty_tickers(score_conn):
    assert boom_score_source.compute_all([], score_conn) == []


def test_compute_all_multiple_tickers(score_conn):
    _add_ticker(score_conn, "GME")
    _add_ticker(score_conn, "AAPL")
    _add_technical(score_conn, "GME", golden_cross=True, rsi14=60.0)
    scores = boom_score_source.compute_all(["GME", "AAPL"], score_conn)
    assert len(scores) == 2
    gme_score = next(s for s in scores if s.ticker == "GME")
    aapl_score = next(s for s in scores if s.ticker == "AAPL")
    assert gme_score.score > aapl_score.score


# ---- Phase 3: bearish signals ----

def test_death_cross_subtracts_20(score_conn):
    _add_ticker(score_conn)
    _add_technical(score_conn, golden_cross=False, rsi14=55.0)  # neutral RSI
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].death_cross is True
    assert scores[0].score == -20


def test_overbought_rsi_subtracts_10(score_conn):
    _add_ticker(score_conn)
    _add_technical(score_conn, golden_cross=None, rsi14=75.0)
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].overbought_rsi is True
    assert scores[0].score == -10


def test_insider_cluster_sell_subtracts_20(score_conn):
    _add_ticker(score_conn)
    # Add two sell transactions.
    db.upsert_trades(score_conn, [InsiderTrade(
        accession="S1", ticker="GME", company="Co", owner="Owner",
        role="CEO", transaction_date=_days_ago(5), transaction_type="Sell",
        shares=500.0, value=50000.0, filing_url="https://sec.gov/s1",
        filed_at=f"{_days_ago(5)}T10:00:00+00:00",
    )])
    db.upsert_trades(score_conn, [InsiderTrade(
        accession="S2", ticker="GME", company="Co", owner="Owner2",
        role="CFO", transaction_date=_days_ago(4), transaction_type="Sell",
        shares=300.0, value=30000.0, filing_url="https://sec.gov/s2",
        filed_at=f"{_days_ago(4)}T10:00:00+00:00",
    )])
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].insider_cluster_sell is True
    assert scores[0].score <= -20


def test_congress_sale_subtracts_15(score_conn):
    _add_ticker(score_conn)
    db.upsert_congress_trades(score_conn, [CongressTrade(
        trade_hash="sale001", representative="Rep Jones", party="R",
        state="TX", ticker="GME", asset_description="Stock",
        transaction_date=_days_ago(5), transaction_type="Sale",
        amount_range="$50,001 - $100,000", filed_at=_days_ago(3), chamber="senate",
    )])
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].congress_sale is True
    assert scores[0].score == -15


def test_analyst_downgrade_cluster_subtracts_15(score_conn):
    _add_ticker(score_conn)
    db.upsert_analyst_signals(score_conn, [AnalystSignal(
        ticker="GME", fetched_at="2026-06-22T10:00:00+00:00",
        next_earnings="2026-09-30", rec_strong_buy=2, rec_buy=5,
        rec_hold=4, rec_sell=3, recent_upgrades=0, recent_downgrades=2,
        latest_action="down", latest_firm="MS", latest_to_grade="Hold",
    )])
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].analyst_downgrade_cluster is True
    assert scores[0].score <= -15


# ---- Phase 3: new bullish signals ----

def test_near_52w_high_adds_10(score_conn):
    _add_ticker(score_conn)
    # Price 98, high_52w = 100; 98 >= 100 * 0.97 = 97 → fires
    _add_technical(score_conn, golden_cross=None, rsi14=55.0, price=98.0, high_52w=100.0)
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].near_52w_high is True
    assert scores[0].score >= 10


def test_near_52w_high_does_not_fire_when_far(score_conn):
    _add_ticker(score_conn)
    # Price 85, high_52w=100; 85 < 97 → does not fire
    _add_technical(score_conn, golden_cross=None, rsi14=55.0, price=85.0, high_52w=100.0)
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].near_52w_high is False


def test_volume_confirmed_adds_10(score_conn):
    _add_ticker(score_conn)
    # rel_volume > 1.5 AND change_pct > 0
    _add_technical(score_conn, golden_cross=None, rsi14=55.0, rel_volume=2.0, change_pct=1.5)
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].volume_confirmed is True
    assert scores[0].score >= 10


def test_volume_confirmed_does_not_fire_on_down_day(score_conn):
    _add_ticker(score_conn)
    _add_technical(score_conn, golden_cross=None, rsi14=55.0, rel_volume=2.0, change_pct=-0.5)
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].volume_confirmed is False


# ---- Phase 3: macro signals ----

def test_fear_greed_contrarian_fires_on_extreme_fear(score_conn):
    _add_ticker(score_conn)
    db.upsert_fear_greed(score_conn, [__import__("app.models", fromlist=["FearGreedSnapshot"]).FearGreedSnapshot(
        captured_at="2026-06-22T10:00:00+00:00", score=20.0, rating="Extreme Fear"
    )])
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].fear_greed_contrarian is True
    assert scores[0].score >= 10


def test_extreme_greed_subtracts_on_high_fg(score_conn):
    _add_ticker(score_conn)
    db.upsert_fear_greed(score_conn, [__import__("app.models", fromlist=["FearGreedSnapshot"]).FearGreedSnapshot(
        captured_at="2026-06-22T10:00:00+00:00", score=85.0, rating="Extreme Greed"
    )])
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].extreme_greed is True
    assert scores[0].score <= -10


# ---- market sentiment extremes (VIX / AAII / put-call) ----

def test_vix_spike_contrarian_fires_at_30(score_conn):
    _add_ticker(score_conn)
    db.upsert_vix(score_conn, [VixPoint(date=_TODAY.isoformat(), close=31.0)])
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].vix_spike_contrarian is True
    assert scores[0].score == 10


def test_vix_below_30_does_not_fire(score_conn):
    _add_ticker(score_conn)
    db.upsert_vix(score_conn, [VixPoint(date=_TODAY.isoformat(), close=25.0)])
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].vix_spike_contrarian is False
    assert scores[0].score == 0


def test_aaii_bearish_extreme_adds_10(score_conn):
    _add_ticker(score_conn)
    db.upsert_aaii(score_conn, [AaiiSentiment(
        week_ending=_days_ago(2), bullish=20.0, neutral=25.0,
        bearish=55.0, fetched_at="2026-07-02T10:00:00+00:00",
    )])
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].aaii_bearish_extreme is True
    assert scores[0].score == 10


def test_aaii_bullish_euphoria_subtracts_10(score_conn):
    _add_ticker(score_conn)
    db.upsert_aaii(score_conn, [AaiiSentiment(
        week_ending=_days_ago(2), bullish=55.0, neutral=25.0,
        bearish=20.0, fetched_at="2026-07-02T10:00:00+00:00",
    )])
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].aaii_bullish_euphoria is True
    assert scores[0].score == -10


def test_aaii_balanced_fires_nothing(score_conn):
    _add_ticker(score_conn)
    db.upsert_aaii(score_conn, [AaiiSentiment(
        week_ending=_days_ago(2), bullish=35.0, neutral=35.0,
        bearish=30.0, fetched_at="2026-07-02T10:00:00+00:00",
    )])
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].aaii_bearish_extreme is False
    assert scores[0].aaii_bullish_euphoria is False


def test_put_call_fear_adds_10(score_conn):
    _add_ticker(score_conn)
    db.upsert_put_call(score_conn, [PutCallPoint(date=_TODAY.isoformat(), ratio=1.1)])
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].put_call_fear is True
    assert scores[0].score == 10


def test_put_call_below_threshold_does_not_fire(score_conn):
    _add_ticker(score_conn)
    db.upsert_put_call(score_conn, [PutCallPoint(date=_TODAY.isoformat(), ratio=0.9)])
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].put_call_fear is False


def _seed_margin_yoy(conn, yoy_pct):
    prev = 100_000.0
    db.upsert_margin_debt(conn, [
        MarginDebtPoint(month="2025-05", debit_balances=prev),
        MarginDebtPoint(month="2026-05", debit_balances=prev * (1 + yoy_pct / 100)),
    ])


def test_margin_debt_deleveraging_adds_10(score_conn):
    _add_ticker(score_conn)
    _seed_margin_yoy(score_conn, -25.0)
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].margin_debt_deleveraging is True
    assert scores[0].score == 10


def test_margin_debt_euphoria_subtracts_10(score_conn):
    _add_ticker(score_conn)
    _seed_margin_yoy(score_conn, 50.0)
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].margin_debt_euphoria is True
    assert scores[0].score == -10


def test_margin_debt_extreme_range_still_euphoria(score_conn):
    _add_ticker(score_conn)
    _seed_margin_yoy(score_conn, 65.0)
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].margin_debt_euphoria is True
    assert scores[0].margin_debt_deleveraging is False


def test_margin_debt_moderate_fires_nothing(score_conn):
    _add_ticker(score_conn)
    _seed_margin_yoy(score_conn, 20.0)
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].margin_debt_euphoria is False
    assert scores[0].margin_debt_deleveraging is False


# ---- Phase 3: mixed signals + earnings soon ----

def test_mixed_signals_flag_set_when_both_bullish_and_bearish(score_conn):
    _add_ticker(score_conn)
    # golden_cross=True (bullish) + overbought RSI (bearish)
    _add_technical(score_conn, golden_cross=True, rsi14=80.0)
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].mixed_signals is True
    assert scores[0].golden_cross is True
    assert scores[0].overbought_rsi is True


def test_earnings_soon_flag_set_within_7_days(score_conn):
    _add_ticker(score_conn)
    db.upsert_analyst_signals(score_conn, [AnalystSignal(
        ticker="GME", fetched_at="2026-06-22T10:00:00+00:00",
        next_earnings=_days_ahead(3),  # 3 days out
        rec_strong_buy=3, rec_buy=5, rec_hold=2, rec_sell=1,
        recent_upgrades=0, recent_downgrades=0,
        latest_action=None, latest_firm=None, latest_to_grade=None,
    )])
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].earnings_soon is True


def test_earnings_soon_not_set_when_far(score_conn):
    _add_ticker(score_conn)
    _add_analyst(score_conn, upgrades=0)  # next_earnings = 2026-07-30, which is > 7 days
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].earnings_soon is False


# ---- Phase 3: history written ----

def test_compute_all_writes_to_history(score_conn):
    _add_ticker(score_conn)
    _add_technical(score_conn, golden_cross=True, rsi14=40.0)
    boom_score_source.compute_all(["GME"], score_conn)
    history = db.get_boom_score_history(score_conn, "GME")
    assert len(history) >= 1
    assert "score" in history[0]
    assert "computed_at" in history[0]


# ---- Phase 3: congress amount weighting ----

def test_congress_buy_larger_amount_scores_higher(score_conn):
    _add_ticker(score_conn)
    _add_congress_purchase(score_conn, amount_range="> $500,000")  # weight 2.0
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].congress_buy is True
    # Base weight 15 × 2.0 = 30
    assert scores[0].score >= 25


# ---- seasonal tailwind ----

def test_seasonal_tailwind_adds_10(score_conn):
    _add_ticker(score_conn)
    _seed_seasonality(score_conn, returns=(0.05,) * 8)  # avg 5%, 100% win
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].seasonal_tailwind is True
    assert scores[0].score == 10


def test_seasonal_tailwind_not_fired_when_weak(score_conn):
    _add_ticker(score_conn)
    _seed_seasonality(score_conn, returns=(0.001,) * 8)  # ~flat, fails avg threshold
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].seasonal_tailwind is False


def test_seasonal_tailwind_not_fired_with_too_few_years(score_conn):
    _add_ticker(score_conn)
    _seed_seasonality(score_conn, returns=(0.05, 0.05))  # strong but only 2 samples
    scores = boom_score_source.compute_all(["GME"], score_conn)
    assert scores[0].seasonal_tailwind is False


# ---------- TASE listings: non-applicable components + renormalization ----------

def test_us_ticker_has_nothing_excluded(score_conn):
    _add_ticker(score_conn, "GME")
    s = boom_score_source.compute_all(["GME"], score_conn)[0]
    assert json.loads(s.not_applicable) == []
    assert s.score_note == ""


def test_tase_ticker_excludes_us_only_components_and_explains(score_conn):
    t = "TEVA.TA"
    _add_ticker(score_conn, t)
    # Stray US-only rows must not score for a Tel Aviv listing.
    _add_insider_buy(score_conn, t, accession="A1")
    _add_insider_buy(score_conn, t, accession="A2")
    _add_congress_purchase(score_conn, t)
    _add_short(score_conn, t, squeeze=True)
    s = boom_score_source.compute_all([t], score_conn)[0]
    na = json.loads(s.not_applicable)
    assert set(na) >= {"insider_cluster_buy", "insider_cluster_sell", "congress_buy",
                       "congress_sale", "contracts_catalyst", "short_squeeze"}
    comps = json.loads(s.components)
    assert not (set(comps) & set(na))
    assert s.insider_cluster_buy is False and s.congress_buy is False
    assert s.score == 0
    assert "TASE" in s.score_note and "renormal" in s.score_note.lower()


def test_tase_score_is_rescaled_to_the_full_range(score_conn):
    """Golden cross alone (+20) on a TASE stock is worth proportionally more,
    because fewer bullish components can ever fire there."""
    t = "LUMI.TA"
    _add_ticker(score_conn, t)
    _add_technical(score_conn, ticker=t, golden_cross=True, rsi14=60.0, high_52w=500.0)
    s = boom_score_source.compute_all([t], score_conn)[0]
    comps = json.loads(s.components)
    assert comps == {"golden_cross": 20}            # raw points are kept for display
    bull_all = sum(v for v in boom_score_source.WEIGHTS.values() if v > 0)
    bull_ok = bull_all - sum(boom_score_source.WEIGHTS[k]
                             for k in boom_score_source.TASE_NOT_APPLICABLE
                             if boom_score_source.WEIGHTS[k] > 0)
    assert s.score == round(20 * bull_all / bull_ok)
    assert s.score > 20
