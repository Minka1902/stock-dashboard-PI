"""Backtesting over recorded history.

The recurring theme: never let a small or incomplete sample read like a result.
Pending horizons stay pending, empty buckets report None rather than 0, and a
missing benchmark says why.
"""
import json
from datetime import date, datetime, timedelta, timezone

from app import backtest, db
from app.models import OHLCSeries


def _iso(days_ago):
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _seed_bars(conn, ticker, days=120, start=100.0, step=0.5):
    bars = []
    for i in range(days, -1, -1):
        close = round(start + (days - i) * step, 2)
        bars.append({
            "date": _iso(i), "open": close, "high": close, "low": close,
            "close": close, "volume": 1_000_000,
        })
    db.upsert_ohlc(conn, [OHLCSeries(
        ticker=ticker, interval="daily", bars_json=json.dumps(bars),
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))])


def _seed_suggestion(conn, ticker, days_ago, action="BUY", kind="watchlist", price=100.0):
    conn.execute(
        "INSERT OR REPLACE INTO suggestion_history "
        "(user_id, ticker, for_date, kind, action, price, created_at) "
        "VALUES (1, ?, ?, ?, ?, ?, ?)",
        (ticker, _iso(days_ago), kind, action, price, _iso(days_ago)),
    )
    conn.commit()


# ---- track record ----------------------------------------------------------

def test_track_record_reports_coverage_even_when_empty(conn):
    out = backtest.track_record(conn, user_id=1)
    assert out["coverage"]["distinct_days"] == 0
    assert out["overall"]["n"] == 0
    # Empty means unknown, not zero.
    assert out["overall"]["d7"]["hit_rate"] is None
    assert "caveat" in out


def test_track_record_separates_pending_from_resolved(conn):
    """A 7-day window that hasn't elapsed is not a miss."""
    _seed_bars(conn, "AAPL")
    _seed_suggestion(conn, "AAPL", days_ago=30)   # d7 elapsed
    _seed_suggestion(conn, "MSFT", days_ago=2)    # d7 has NOT elapsed
    _seed_bars(conn, "MSFT")

    out = backtest.track_record(conn, user_id=1)
    assert out["overall"]["n"] == 2
    assert out["overall"]["d7"]["n"] == 1          # only the resolved one scored
    assert out["overall"]["d7_pending"] == 1


def test_track_record_hit_rate_uses_only_resolved_entries(conn):
    _seed_bars(conn, "AAPL")                       # rising series -> a win
    _seed_suggestion(conn, "AAPL", days_ago=40)
    out = backtest.track_record(conn, user_id=1)
    assert out["overall"]["d7"]["n"] == 1
    assert out["overall"]["d7"]["hit_rate"] == 100.0
    assert out["overall"]["d7"]["median"] > 0


def test_track_record_groups_by_action_and_kind(conn):
    _seed_bars(conn, "AAPL")
    _seed_bars(conn, "MSFT")
    _seed_suggestion(conn, "AAPL", days_ago=40, action="BUY", kind="watchlist")
    _seed_suggestion(conn, "MSFT", days_ago=40, action="TRIM", kind="holding")

    out = backtest.track_record(conn, user_id=1)
    assert {b["label"] for b in out["by_action"]} == {"BUY", "TRIM"}
    assert {b["label"] for b in out["by_kind"]} == {"watchlist", "holding"}


def test_track_record_groups_by_ticker(conn):
    """The aggregate rows say "BUY · conv 3" but not which stock; by_ticker is
    what lets the table name the company."""
    _seed_bars(conn, "AAPL")
    _seed_bars(conn, "MSFT")
    _seed_suggestion(conn, "AAPL", days_ago=40)
    _seed_suggestion(conn, "AAPL", days_ago=30)
    _seed_suggestion(conn, "MSFT", days_ago=40)

    out = backtest.track_record(conn, user_id=1)
    labels = [b["label"] for b in out["by_ticker"]]
    assert labels == ["AAPL", "MSFT"]  # busiest first, then alphabetical
    aapl = out["by_ticker"][0]
    assert aapl["n"] == 2
    # Same bucket shape as every other grouping, so the UI can share a row.
    assert set(aapl) >= {"label", "n", "d7", "d7_pending", "d30", "d30_pending"}


def test_track_record_by_ticker_is_empty_without_history(conn):
    assert backtest.track_record(conn, user_id=1)["by_ticker"] == []


def test_benchmark_says_why_it_is_missing(conn):
    """Silence would let a track record flatter itself in a rising market."""
    _seed_bars(conn, "AAPL")
    _seed_suggestion(conn, "AAPL", days_ago=40)
    out = backtest.track_record(conn, user_id=1)
    assert out["benchmark"]["available"] is False
    assert "SPY" in out["benchmark"]["reason"]


def test_benchmark_computed_when_spy_bars_exist(conn):
    _seed_bars(conn, "AAPL")
    _seed_bars(conn, "SPY")
    _seed_suggestion(conn, "AAPL", days_ago=40)
    out = backtest.track_record(conn, user_id=1)
    assert out["benchmark"]["available"] is True
    assert out["benchmark"]["return_pct"] > 0


# ---- signal replay ---------------------------------------------------------

def _seed_scores(conn, ticker, points):
    """points = [(days_ago, score)] — written as the scorer would have."""
    for days_ago, score in points:
        stamp = f"{_iso(days_ago)}T12:00:00+00:00"
        conn.execute(
            "INSERT INTO boom_score_history (ticker, score, computed_at) VALUES (?, ?, ?)",
            (ticker, score, stamp),
        )
    conn.commit()


def test_replay_detects_an_upward_crossing(conn):
    _seed_bars(conn, "NVDA")
    _seed_scores(conn, "NVDA", [(40, 45), (39, 65)])   # crosses 60 upward
    out = backtest.signal_replay(conn, horizon_days=7, thresholds=(60,))
    row = out["thresholds"][0]
    assert row["crossings"] == 1
    assert row["n"] == 1
    assert row["hit_rate"] == 100.0                    # rising series


def test_replay_ignores_a_score_that_was_already_above(conn):
    """Staying above a threshold is not a crossing."""
    _seed_bars(conn, "NVDA")
    _seed_scores(conn, "NVDA", [(40, 65), (39, 70), (38, 68)])
    out = backtest.signal_replay(conn, horizon_days=7, thresholds=(60,))
    assert out["thresholds"][0]["crossings"] == 0


def test_replay_dedupes_within_a_day(conn):
    """The score is recomputed every few minutes; one move is one crossing."""
    _seed_bars(conn, "NVDA")
    stamp_day = _iso(40)
    for hour, score in [(9, 50), (10, 65), (11, 50), (12, 66)]:
        conn.execute(
            "INSERT INTO boom_score_history (ticker, score, computed_at) VALUES (?, ?, ?)",
            ("NVDA", score, f"{stamp_day}T{hour:02d}:00:00+00:00"),
        )
    conn.commit()
    out = backtest.signal_replay(conn, horizon_days=7, thresholds=(60,))
    assert out["thresholds"][0]["crossings"] == 1


def test_replay_counts_unelapsed_horizons_as_pending(conn):
    _seed_bars(conn, "NVDA")
    _seed_scores(conn, "NVDA", [(2, 45), (1, 65)])   # crossed yesterday
    out = backtest.signal_replay(conn, horizon_days=30, thresholds=(60,))
    row = out["thresholds"][0]
    assert row["crossings"] == 1
    assert row["pending"] == 1
    assert row["n"] == 0
    assert row["hit_rate"] is None                    # not 0%


def test_replay_returns_a_curve_across_thresholds(conn):
    _seed_bars(conn, "NVDA")
    _seed_scores(conn, "NVDA", [(40, 30), (39, 75)])  # crosses 40, 50, 60 and 70
    out = backtest.signal_replay(conn, horizon_days=7)
    assert [r["threshold"] for r in out["thresholds"]] == list(backtest.REPLAY_THRESHOLDS)
    assert all(r["crossings"] == 1 for r in out["thresholds"])


def test_replay_coverage_and_caveat_always_present(conn):
    out = backtest.signal_replay(conn)
    assert "coverage" in out and "caveat" in out
    assert out["coverage"]["distinct_days"] == 0
