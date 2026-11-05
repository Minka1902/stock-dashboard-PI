"""The rule schema, the bar backtester (no look-ahead, costs, stops and gaps,
overfitting guards), saved rules as a live signal source and a rule bot."""
import json
import math
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import bar_backtest, bots, db, ledger, rule_source, rules, suggestion_history
from app.models import LiveQuote, OHLCBar, OHLCSeries, SavedRule, WatchItem
from tests.conftest import authenticate

CROSS = {"conditions": [{"left": {"name": "sma", "period": 5}, "op": "crosses_above",
                         "right": {"name": "sma", "period": 20}}]}
UNCROSS = {"conditions": [{"left": {"name": "sma", "period": 5}, "op": "crosses_below",
                           "right": {"name": "sma", "period": 20}}]}


def _bars(closes, start=date(2018, 1, 1), opens=None, lows=None, highs=None):
    out = []
    for i, c in enumerate(closes):
        o = opens[i] if opens else c
        out.append(OHLCBar(date=(start + timedelta(days=i)).isoformat(), open=o,
                           high=highs[i] if highs else max(o, c), low=lows[i] if lows else min(o, c),
                           close=c, volume=1e6))
    return out


def _wave(n=900, period=25):
    return [100 + 15 * math.sin(i / period * 2 * math.pi) + i * 0.01 for i in range(n)]


# ---------- schema ----------

def test_non_point_in_time_inputs_are_refused():
    with pytest.raises(ValidationError, match="isn't point-in-time"):
        rules.RuleSpec(name="x", entry={"conditions": [{"left": {"name": "pe_ratio"}, "op": "<",
                                                        "right": {"kind": "value", "value": 15}}]})


def test_periods_and_sizes_are_bounded():
    with pytest.raises(ValidationError, match="period must be"):
        rules.RuleSpec(name="x", entry={"conditions": [{"left": {"name": "rsi", "period": 1}, "op": "<",
                                                        "right": {"kind": "value", "value": 30}}]})
    many = {"conditions": [CROSS["conditions"][0]] * (rules.MAX_CONDITIONS + 1)}
    with pytest.raises(ValidationError, match="at most"):
        rules.RuleSpec(name="x", entry=many)


def test_the_version_ignores_the_name_but_not_the_logic():
    a = rules.RuleSpec(name="a", entry=CROSS)
    assert rules.version(a) == rules.version(rules.RuleSpec(name="b", entry=CROSS))
    assert rules.version(a) != rules.version(rules.RuleSpec(name="a", entry=CROSS, stop_pct=5))


# ---------- no look-ahead ----------

@pytest.mark.parametrize("name,period", [("sma", 20), ("ema", 20), ("rsi", 14), ("atr", 14), ("macd", None),
                                         ("macd_signal", None), ("bb_upper", 20), ("rvol", 20),
                                         ("high_n", 20), ("low_n", 20), ("gap_pct", None), ("change_pct", None)])
def test_perturbing_future_bars_never_changes_past_values(name, period):
    closes = _wave(300)
    a = rules.arrays(_bars(closes))
    future = closes[:200] + [c * 3 + 50 for c in closes[200:]]
    b = rules.arrays(_bars(future))
    sa, sb = rules.series(name, period, a)[:200], rules.series(name, period, b)[:200]
    assert np.allclose(sa, sb, equal_nan=True)


def test_perturbing_future_bars_never_changes_past_trades():
    spec = rules.RuleSpec(name="x", entry=CROSS, exit=UNCROSS)
    closes = _wave()
    before = bar_backtest.run(_bars(closes), spec)
    after = bar_backtest.run(_bars(closes[:600] + [50.0] * 300), spec)
    early = [t for t in before["trades"] if t["exit_date"] < _bars(closes)[598].date]
    assert early and early == [t for t in after["trades"] if t["exit_date"] < _bars(closes)[598].date]


def test_signals_fill_at_the_next_open_with_costs():
    spec = rules.RuleSpec(name="x", entry={"conditions": [{"left": {"name": "close"}, "op": ">",
                                                           "right": {"kind": "value", "value": 100}}]})
    closes = [99.0] * 30 + [101.0] + [102.0] * 30
    opens = [99.0] * 31 + [105.0] + [102.0] * 29
    r = bar_backtest.run(_bars(closes, opens=opens), spec)
    first = r["trades"][0]
    assert first["entry_date"] == _bars(closes)[31].date and first["entry"] == pytest.approx(105 * 1.001)


# ---------- stops ----------

def test_a_gap_through_the_stop_fills_at_the_open():
    spec = rules.RuleSpec(name="x", entry={"conditions": [{"left": {"name": "close"}, "op": ">",
                                                           "right": {"kind": "value", "value": 100}}]},
                          stop_pct=5)
    closes = [99.0] * 30 + [101.0, 101.0, 80.0] + [80.0] * 30
    opens = [99.0] * 32 + [80.0] + [80.0] * 30
    t = bar_backtest.run(_bars(closes, opens=opens), spec)["trades"][0]
    assert t["reason"] == "stop" and t["exit"] == pytest.approx(80 * 0.999)


def test_an_intrabar_stop_fills_at_the_level_and_a_trail_says_so():
    spec = rules.RuleSpec(name="x", entry={"conditions": [{"left": {"name": "close"}, "op": ">",
                                                           "right": {"kind": "value", "value": 100}}]},
                          trail_pct=10)
    closes = [99.0] * 30 + [101.0, 101.0, 120.0, 118.0] + [118.0] * 20
    lows = [c for c in closes]
    lows[33] = 100.0                       # trail = 120 × 0.9 = 108, touched intrabar
    t = bar_backtest.run(_bars(closes, lows=lows), spec)["trades"][0]
    assert t["reason"] == "trailing stop" and t["exit"] == pytest.approx(108 * 0.999)


# ---------- overfitting guards ----------

def test_no_metrics_below_the_minimum_trade_count():
    spec = rules.RuleSpec(name="x", entry=CROSS, exit=UNCROSS)
    r = bar_backtest.run(_bars(_wave(200)), spec)
    assert r["metrics"]["insufficient"] and r["metrics"]["total_return_pct"] is None


def test_in_and_out_of_sample_are_reported_separately():
    spec = rules.RuleSpec(name="x", entry=CROSS, exit=UNCROSS)
    r = bar_backtest.run(_bars(_wave(3000)), spec)
    assert r["metrics"]["trades"] >= bar_backtest.MIN_TRADES and r["metrics"]["total_return_pct"] is not None
    assert r["split_date"] and r["in_sample"]["trades"] + r["out_of_sample"]["trades"] == r["metrics"]["trades"]


def test_each_distinct_variant_is_counted_once(conn):
    u = db.create_user(conn, "v@x.co", "h", "t")
    assert db.note_variant(conn, u.id, "x", "h1", "t") == 1
    assert db.note_variant(conn, u.id, "x", "h1", "t") == 1
    assert db.note_variant(conn, u.id, "x", "h2", "t") == 2


# ---------- live signals & the rule bot ----------

def _save(conn, user_id, spec):
    s = rules.RuleSpec.model_validate(spec)
    return db.save_rule(conn, SavedRule(user_id=user_id, name=s.name, spec=s.model_dump_json(),
                                        model_version=rules.version(s), created_at="t", updated_at="t"))


def _store_daily(conn, ticker, bars):
    db.upsert_ohlc(conn, [OHLCSeries(ticker=ticker, interval="daily", fetched_at="t",
                                     bars_json=json.dumps([b.model_dump() for b in bars]))])


def test_a_saved_rule_fires_on_completed_bars_alerts_once_and_is_tracked_under_its_version(conn):
    u = db.create_user(conn, "r@x.co", "h", "t")
    db.add_watch(conn, u.id, WatchItem(ticker="AAPL", note="", added_at="t"))
    rule = _save(conn, u.id, {"name": "Above 100", "entry": {"conditions": [
        {"left": {"name": "close"}, "op": ">", "right": {"kind": "value", "value": 100}}]}})
    now = datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc)
    _store_daily(conn, "AAPL", _bars([99.0, 101.0], start=date(2026, 10, 26)) +
                 _bars([50.0], start=date(2026, 10, 28)))        # today's forming bar is ignored
    signals = rule_source.fetch(conn, now)
    assert [(s.ticker, s.day, s.kind) for s in signals] == [("AAPL", "2026-10-27", "entry")]
    rule_source.store(conn, signals)
    rule_source.store(conn, signals)
    assert [a.type for a in db.get_alerts(conn, u.id)] == ["rule"]
    digest = {"for_date": "2026-10-28", "holdings_alerts": [], "opportunities": [],
              "rule_signals": rule_source.latest_for_user(conn, u.id, "2026-10-20")}
    [row] = suggestion_history.collect(conn, digest, u.id)
    assert row.kind == "rule" and row.model_version == rule.model_version


def test_a_rule_bot_trades_its_saved_rule(conn):
    from tests.test_bots import LIVE, _quotes
    u = db.create_user(conn, "rb@x.co", "h", "t")
    ledger.record(conn, u.id, ticker="USD", side="DEPOSIT", qty=10_000, currency_code="USD")
    rule = _save(conn, u.id, {"name": "Above 100", "stop_pct": 5, "entry": {"conditions": [
        {"left": {"name": "close"}, "op": ">", "right": {"kind": "value", "value": 100}}]}})
    _store_daily(conn, "AAPL", _bars([99.0] * 20 + [101.0], start=date(2026, 10, 6)))
    bot, errors = bots.validate_new(conn, u.id, {"strategy": "rule", "tickers": ["AAPL"], "amount": 5_000,
                                                 "currency": "USD", "params": {"rule_id": rule.id}})
    assert errors == {}
    bot = db.create_bot(conn, bot)
    bots._LAST_SKIP.clear()
    assert bots.run(conn, LIVE, _quotes({"AAPL": 102.0})) == 1
    ev = db.get_bot_events(conn, bot.id)[0]
    assert 'Rule "Above 100" entry' in ev.reason and "Stop 96.90" in ev.reason
    _, errors = bots.validate_new(conn, u.id, {"strategy": "rule", "tickers": ["AAPL"], "amount": 10,
                                               "currency": "USD", "params": {"rule_id": 999}})
    assert "params.rule_id" in errors


# ---------- API ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "rules.db"))
    import importlib

    from app import config, main as main_module
    from app.sources import ohlc
    importlib.reload(config)
    importlib.reload(main_module)
    monkeypatch.setattr(ohlc, "fetch", lambda tickers, series=None: [])
    with TestClient(main_module.app) as c:
        authenticate(c)
        yield c, main_module


def test_api_save_and_backtest(client):
    c, m = client
    assert {i["name"] for i in c.get("/api/rules/schema").json()["indicators"]} >= {"sma", "rsi", "high_n"}
    bad = c.post("/api/rules", json={"spec": {"name": "x", "entry": {"conditions": [
        {"left": {"name": "boom_score"}, "op": ">", "right": {"kind": "value", "value": 50}}]}}})
    assert bad.status_code == 422 and "point-in-time" in json.dumps(bad.json()["detail"]["errors"])
    spec = {"name": "MA cross", "entry": CROSS, "exit": UNCROSS}
    saved = c.post("/api/rules", json={"spec": spec}).json()
    assert saved["model_version"].startswith("rule:")
    _store_daily(m.conn, "AAPL", _bars(_wave(600)))
    r = c.post("/api/rules/backtest", json={"spec": spec, "ticker": "AAPL"}).json()
    assert r["data"].startswith("the stored 2 years") and r["variants_tested"] == 1 and r["trades"]
    assert c.post("/api/rules/backtest", json={"spec": spec, "ticker": "NOPE"}).status_code == 422
    assert c.delete(f"/api/rules/{saved['id']}").status_code == 200


def test_api_variants_tested_before_saving_count_and_names_are_unique(client):
    c, m = client
    _store_daily(m.conn, "AAPL", _bars(_wave(600)))
    spec = {"name": "Tuned", "entry": CROSS}
    c.post("/api/rules/backtest", json={"spec": spec, "ticker": "AAPL"})
    c.post("/api/rules/backtest", json={"spec": {**spec, "stop_pct": 5}, "ticker": "AAPL"})
    saved = c.post("/api/rules", json={"spec": spec}).json()
    assert saved["variants_tested"] == 2
    assert c.get("/api/rules").json()["rules"][0]["variants_tested"] == 2
    dup = c.post("/api/rules", json={"spec": spec})
    assert dup.status_code == 422 and "name" in dup.json()["detail"]["errors"]
    other = c.post("/api/rules", json={"spec": {**spec, "name": "Other"}}).json()
    assert c.put(f"/api/rules/{other['id']}", json={"spec": spec}).status_code == 422
