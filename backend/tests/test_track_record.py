"""Honest track record: model versions never mix, costs are one symmetric
model, and a hit rate carries its interval and a small-sample flag."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app import backtest, costs, db, model_version, suggestion_history
from app.models import BoomScore, OHLCSeries


def _iso(days_ago):
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _seed_bars(conn, ticker, days=120, start=100.0, step=0.5):
    bars = [{"date": _iso(i), "open": start + (days - i) * step, "high": start + (days - i) * step,
             "low": start + (days - i) * step, "close": start + (days - i) * step, "volume": 1e6}
            for i in range(days, -1, -1)]
    db.upsert_ohlc(conn, [OHLCSeries(ticker=ticker, interval="daily", bars_json=json.dumps(bars),
                                     fetched_at=datetime.now(timezone.utc).isoformat())])


def _seed(conn, ticker, days_ago, version, price=100.0):
    conn.execute(
        "INSERT INTO suggestion_history (user_id, ticker, for_date, kind, action, price, created_at, "
        "model_version) VALUES (1, ?, ?, 'watchlist', 'BUY', ?, ?, ?)",
        (ticker, _iso(days_ago), price, _iso(days_ago), version))
    conn.commit()


# ---------- model version ----------

def test_the_version_is_a_stable_hash_of_weights_and_thresholds():
    fp = model_version.fingerprint()
    assert model_version.compute(fp) == model_version.current()
    reordered = {k: fp[k] for k in reversed(list(fp))}
    assert model_version.compute(reordered) == model_version.current()
    changed = json.loads(json.dumps(fp))
    changed["weights"]["golden_cross"] += 1
    assert model_version.compute(changed) != model_version.current()
    assert len(model_version.current()) == 8


def test_new_suggestions_are_stamped_with_the_running_version(conn):
    digest = {"for_date": "2026-10-28", "holdings_alerts": [
        {"ticker": "AAPL", "action": "Hold", "price": 100.0}], "opportunities": []}
    [row] = suggestion_history.collect(conn, digest, user_id=1)
    assert row.model_version == model_version.current()
    db.record_suggestion_history(conn, [row])
    assert db.get_suggestion_history(conn, 1)[0].model_version == model_version.current()


def test_versions_never_mix(conn):
    _seed_bars(conn, "AAPL")
    _seed(conn, "AAPL", 40, "old11111")
    _seed(conn, "AAPL", 30, "old11111")
    _seed(conn, "AAPL", 20, "new22222")
    out = backtest.track_record(conn, user_id=1)
    assert out["model_version"] == "new22222"            # the most recently seen
    assert out["overall"]["n"] == 1
    assert [v["version"] for v in out["versions"]] == ["new22222", "old11111"]
    old = backtest.track_record(conn, user_id=1, version="old11111")
    assert old["model_version"] == "old11111" and old["overall"]["n"] == 2


def test_the_running_version_is_preferred_when_it_has_data(conn):
    _seed_bars(conn, "AAPL")
    _seed(conn, "AAPL", 20, model_version.current())
    _seed(conn, "AAPL", 10, "other333")
    assert backtest.track_record(conn, user_id=1)["model_version"] == model_version.current()


def test_signal_replay_stays_inside_one_version(conn):
    _seed_bars(conn, "NVDA")
    for days_ago, score, version in [(40, 45, "aaaa1111"), (39, 65, "bbbb2222")]:
        conn.execute("INSERT INTO boom_score_history (ticker, score, computed_at, model_version) "
                     "VALUES ('NVDA', ?, ?, ?)", (score, f"{_iso(days_ago)}T12:00:00+00:00", version))
    conn.commit()
    out = backtest.signal_replay(conn, horizon_days=7, thresholds=(60,))
    assert out["model_version"] == "bbbb2222"
    assert out["thresholds"][0]["crossings"] == 0         # 45 → 65 spans two models


# ---------- costs ----------

def test_costs_are_symmetric_for_long_and_short():
    assert costs.apply(100.0, "BUY", 10) - 100 == pytest.approx(100 - costs.apply(100.0, "SELL", 10))
    assert costs.round_trip(100, 110, "long", 10) == pytest.approx(costs.round_trip(110, 100, "short", 10))
    flat = costs.round_trip(100, 100, "long", 10)
    assert flat == pytest.approx(costs.round_trip(100, 100, "short", 10)) == pytest.approx(-0.1998, abs=1e-4)


def test_half_the_spread_is_added_only_when_known():
    assert costs.apply(100.0, "BUY", 0, half_spread=0.05) == 100.05
    assert costs.apply(100.0, "SELL", 0) == 100.0


def test_outcomes_come_gross_and_net(conn):
    _seed_bars(conn, "AAPL")
    _seed(conn, "AAPL", 30, model_version.current(), price=100.0)
    row = suggestion_history.with_outcomes(conn, db.get_suggestion_history(conn, 1))[0]
    assert row["outcomes_net"]["d7"] < row["outcomes"]["d7"]
    out = backtest.track_record(conn, user_id=1)["overall"]
    assert out["d7_net"]["median"] < out["d7"]["median"]


# ---------- confidence ----------

@pytest.mark.parametrize("wins,n,expected", [
    (5, 10, (23.7, 76.3)), (0, 10, (0.0, 27.8)), (50, 100, (40.4, 59.6)), (10, 10, (72.2, 100.0)),
])
def test_wilson_interval_known_values(wins, n, expected):
    assert backtest.wilson(wins, n) == expected


def test_small_samples_are_flagged_insufficient():
    small = backtest._stats([1.0] * 12)
    assert small["insufficient"] is True and small["hit_rate_ci"] == (75.7, 100.0)
    assert backtest._stats([1.0] * 30)["insufficient"] is False
    assert backtest._stats([])["hit_rate"] is None and backtest._stats([])["insufficient"] is True


# ---------- component history ----------

def test_each_run_stores_the_components_that_fired_and_is_pruned(conn):
    flags = {k: False for k in ("golden_cross", "rsi_recovery", "insider_cluster_buy", "congress_buy",
                                "short_squeeze", "wsb_rising", "analyst_upgrade", "death_cross",
                                "insider_cluster_sell", "overbought_rsi", "congress_sale",
                                "analyst_downgrade_cluster", "extreme_greed")}
    score = BoomScore(ticker="AAPL", score=35, computed_at="2020-01-01T00:00:00+00:00",
                      components=json.dumps({"golden_cross": 20, "congress_buy": 15}),
                      **{k: v for k, v in flags.items() if k in BoomScore.model_fields})
    db.insert_boom_score_history(conn, [score], "v1")
    rows = db.get_boom_component_history(conn, "2000-01-01")
    assert sorted((r["component"], r["weight"]) for r in rows) == [("congress_buy", 15), ("golden_cross", 20)]
    assert db.prune_history(conn, 30, 90, 400)["boom_component_history"] == 2
