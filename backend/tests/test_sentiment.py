"""Boundary tests for the market sentiment summary and its signal rules."""
from datetime import datetime, timedelta, timezone

from app import db, sentiment
from app.models import AaiiSentiment, FearGreedSnapshot, MarginDebtPoint, PutCallPoint, VixPoint

_NOW = datetime.now(timezone.utc)
_TODAY = _NOW.date()


def _iso_now(hours_ago: int = 0) -> str:
    return (_NOW - timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def _day(n_ago: int) -> str:
    return (_TODAY - timedelta(days=n_ago)).isoformat()


def _seed_fg(conn, score: float):
    db.upsert_fear_greed(conn, [FearGreedSnapshot(
        captured_at=_iso_now(), score=score, rating="Test"
    )])


def _seed_vix(conn, closes: list[float]):
    points = [
        VixPoint(date=_day(len(closes) - 1 - i), close=c)
        for i, c in enumerate(closes)
    ]
    db.upsert_vix(conn, points)


def _seed_aaii(conn, bullish: float, bearish: float):
    neutral = max(0.0, 100.0 - bullish - bearish)
    db.upsert_aaii(conn, [AaiiSentiment(
        week_ending=_day(2), bullish=bullish, neutral=neutral,
        bearish=bearish, fetched_at=_iso_now(),
    )])


def _seed_pc(conn, ratio: float):
    db.upsert_put_call(conn, [PutCallPoint(date=_day(0), ratio=ratio)])


def _seed_margin(conn, yoy_pct: float, month: str = "2026-05"):
    """Two rows 12 months apart producing exactly the requested %YoY."""
    prev = 100_000.0
    cur = round(prev * (1 + yoy_pct / 100), 4)
    prior_month = f"{int(month[:4]) - 1}{month[4:]}"
    db.upsert_margin_debt(conn, [
        MarginDebtPoint(month=prior_month, debit_balances=prev),
        MarginDebtPoint(month=month, debit_balances=cur),
    ])


# ---- Fear & Greed thresholds ----

def test_fg_at_25_is_buy(conn):
    _seed_fg(conn, 25.0)
    assert sentiment.build_summary(conn)["indicators"]["fear_greed"]["signal"] == "BUY"


def test_fg_just_above_25_is_neutral(conn):
    _seed_fg(conn, 25.1)
    assert sentiment.build_summary(conn)["indicators"]["fear_greed"]["signal"] == "NEUTRAL"


def test_fg_at_75_is_sell(conn):
    _seed_fg(conn, 75.0)
    assert sentiment.build_summary(conn)["indicators"]["fear_greed"]["signal"] == "SELL"


# ---- VIX thresholds ----

def test_vix_below_19_is_neutral(conn):
    _seed_vix(conn, [18.9])
    vix = sentiment.build_summary(conn)["indicators"]["vix"]
    assert vix["signal"] == "NEUTRAL"
    assert vix["crossed_19"] is False


def test_vix_at_19_is_alert(conn):
    _seed_vix(conn, [19.0])
    assert sentiment.build_summary(conn)["indicators"]["vix"]["signal"] == "ALERT"


def test_vix_at_30_is_extreme(conn):
    _seed_vix(conn, [30.0])
    assert sentiment.build_summary(conn)["indicators"]["vix"]["signal"] == "EXTREME"


def test_vix_crossed_19_flag(conn):
    _seed_vix(conn, [18.5, 19.2])
    vix = sentiment.build_summary(conn)["indicators"]["vix"]
    assert vix["signal"] == "ALERT"
    assert vix["crossed_19"] is True


def test_vix_already_above_19_no_cross_flag(conn):
    _seed_vix(conn, [20.0, 21.0])
    assert sentiment.build_summary(conn)["indicators"]["vix"]["crossed_19"] is False


# ---- AAII thresholds ----

def test_aaii_bearish_45_is_buy(conn):
    _seed_aaii(conn, bullish=30.0, bearish=45.0)
    assert sentiment.build_summary(conn)["indicators"]["aaii"]["signal"] == "BUY"


def test_aaii_bullish_45_is_sell(conn):
    _seed_aaii(conn, bullish=45.0, bearish=30.0)
    assert sentiment.build_summary(conn)["indicators"]["aaii"]["signal"] == "SELL"


def test_aaii_spread_20_is_buy(conn):
    _seed_aaii(conn, bullish=24.0, bearish=44.0)  # neither ≥45, spread = 20
    assert sentiment.build_summary(conn)["indicators"]["aaii"]["signal"] == "BUY"


def test_aaii_balanced_is_neutral(conn):
    _seed_aaii(conn, bullish=40.0, bearish=40.0)
    assert sentiment.build_summary(conn)["indicators"]["aaii"]["signal"] == "NEUTRAL"


# ---- Put/Call thresholds ----

def test_pc_at_1_is_buy(conn):
    _seed_pc(conn, 1.0)
    assert sentiment.build_summary(conn)["indicators"]["put_call"]["signal"] == "BUY"


def test_pc_at_08_is_sell(conn):
    _seed_pc(conn, 0.8)
    assert sentiment.build_summary(conn)["indicators"]["put_call"]["signal"] == "SELL"


def test_pc_between_is_neutral(conn):
    _seed_pc(conn, 0.9)
    assert sentiment.build_summary(conn)["indicators"]["put_call"]["signal"] == "NEUTRAL"


# ---- Margin debt thresholds ----

def test_margin_at_45_is_sell(conn):
    _seed_margin(conn, 45.0)
    assert sentiment.build_summary(conn)["indicators"]["margin_debt"]["signal"] == "SELL"


def test_margin_just_below_45_is_neutral(conn):
    _seed_margin(conn, 44.9)
    assert sentiment.build_summary(conn)["indicators"]["margin_debt"]["signal"] == "NEUTRAL"


def test_margin_at_60_is_extreme(conn):
    _seed_margin(conn, 60.0)
    assert sentiment.build_summary(conn)["indicators"]["margin_debt"]["signal"] == "EXTREME"


def test_margin_at_minus_20_is_buy(conn):
    _seed_margin(conn, -20.0)
    assert sentiment.build_summary(conn)["indicators"]["margin_debt"]["signal"] == "BUY"


def test_margin_just_above_minus_20_is_neutral(conn):
    _seed_margin(conn, -19.9)
    assert sentiment.build_summary(conn)["indicators"]["margin_debt"]["signal"] == "NEUTRAL"


def test_margin_single_month_is_no_data(conn):
    db.upsert_margin_debt(conn, [MarginDebtPoint(month="2026-05", debit_balances=1_000_000.0)])
    md = sentiment.build_summary(conn)["indicators"]["margin_debt"]
    assert md["signal"] == "NO_DATA"
    assert md["value"] is None


def test_margin_extreme_not_counted_in_lean(conn):
    _seed_margin(conn, 65.0)      # EXTREME
    _seed_fg(conn, 80.0)          # SELL (only one SELL)
    summary = sentiment.build_summary(conn)
    assert summary["overall"]["sell_count"] == 1
    assert summary["overall"]["lean"] == "NEUTRAL"


# ---- empty DB / overall lean ----

def test_empty_db_all_no_data_and_neutral_lean(conn):
    summary = sentiment.build_summary(conn)
    for key in ("fear_greed", "vix", "aaii", "put_call", "margin_debt"):
        assert summary["indicators"][key]["signal"] == "NO_DATA"
        assert summary["indicators"][key]["value"] is None
    assert summary["overall"] == {"buy_count": 0, "sell_count": 0, "lean": "NEUTRAL"}


def test_two_buys_lean_buy(conn):
    _seed_fg(conn, 20.0)          # BUY
    _seed_pc(conn, 1.1)           # BUY
    _seed_vix(conn, [15.0])       # NEUTRAL
    summary = sentiment.build_summary(conn)
    assert summary["overall"]["buy_count"] == 2
    assert summary["overall"]["lean"] == "BUY"


def test_single_buy_stays_neutral(conn):
    _seed_fg(conn, 20.0)          # BUY, everything else NO_DATA
    assert sentiment.build_summary(conn)["overall"]["lean"] == "NEUTRAL"


def test_two_sells_lean_sell(conn):
    _seed_fg(conn, 80.0)                       # SELL
    _seed_aaii(conn, bullish=50.0, bearish=20.0)  # SELL
    assert sentiment.build_summary(conn)["overall"]["lean"] == "SELL"


def test_vix_extreme_does_not_count_toward_lean(conn):
    _seed_vix(conn, [35.0])       # EXTREME
    _seed_fg(conn, 20.0)          # BUY (only one BUY)
    summary = sentiment.build_summary(conn)
    assert summary["overall"]["buy_count"] == 1
    assert summary["overall"]["lean"] == "NEUTRAL"
