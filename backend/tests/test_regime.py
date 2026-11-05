"""Regime gate and signal auto-mute: missing inputs → unknown, hysteresis,
the MIN_N gate, sells never suppressed, override precedence."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import automute, bots, config, db, ledger, regime, risk, suggestions
from app.models import (
    BoomScore, FearGreedSnapshot, LiveQuote, OHLCSeries, PutCallPoint, RegimeSnapshot,
    SignalMute, SignalPerformance, StockAnalysis, VixPoint, WatchItem,
)
from app.sources import boom_score
from tests.conftest import authenticate, buy

TODAY = date.today()
LIVE = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)


def _iso(days_ago):
    return (TODAY - timedelta(days=days_ago)).isoformat()


def _vote(key, vote):
    return {"key": key, "label": key, "value": 1, "vote": vote, "detail": "", "as_of": None}


@pytest.fixture
def user(conn):
    return db.create_user(conn, "regime@x.co", "h", "t")


def _store_regime(conn, label, score=-3):
    db.upsert_regime(conn, [RegimeSnapshot(date=TODAY.isoformat(), label=label, score=score,
                                           inputs="[]", computed_at="t")])


# ---------- classifier ----------

def test_fewer_than_three_inputs_is_unknown_never_a_guess():
    assert regime.classify([_vote("a", -1), _vote("b", -1), _vote("c", None)])[0] == "unknown"
    assert regime.classify([_vote("a", -1), _vote("b", -1), _vote("c", 0)])[0] == "risk_off"
    assert regime.classify([_vote("a", 1), _vote("b", 1), _vote("c", 0)])[0] == "risk_on"
    assert regime.classify([_vote("a", 1), _vote("b", -1), _vote("c", 1)])[0] == "neutral"


def test_with_no_data_every_input_says_why_and_the_regime_is_unknown(conn):
    [snap] = regime.compute(conn)
    inputs = json.loads(snap.inputs)
    assert snap.label == "unknown" and all(i["vote"] is None for i in inputs)
    assert {i["key"] for i in inputs} == {"vix", "vix_trend", "fear_greed", "put_call", "yield_curve", "spy_trend"}


def test_fearful_inputs_read_risk_off_and_stale_ones_are_ignored(conn):
    db.upsert_vix(conn, [VixPoint(date=_iso(i), close=20 + (6 - i) * 2) for i in range(6, -1, -1)])
    db.upsert_fear_greed(conn, [FearGreedSnapshot(
        captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), score=18, rating="Extreme Fear")])
    db.upsert_put_call(conn, [PutCallPoint(date=_iso(30), ratio=1.3)])   # stale
    [snap] = regime.compute(conn)
    by = {i["key"]: i for i in json.loads(snap.inputs)}
    assert by["vix"]["vote"] == -1 and by["vix_trend"]["vote"] == -1 and by["fear_greed"]["vote"] == -1
    assert by["put_call"]["vote"] is None
    assert snap.label == "risk_off" and snap.score == -3


def test_latest_is_unknown_until_computed(conn):
    assert regime.latest(conn)["label"] == "unknown"
    _store_regime(conn, "risk_off")
    assert regime.latest(conn)["label"] == "risk_off"


# ---------- the gate ----------

def _buy_idea(conn, user_id, ticker="AAPL"):
    db.add_watch(conn, user_id, WatchItem(ticker=ticker, note="", added_at="t"))
    db.upsert_analyses(conn, [StockAnalysis(
        ticker=ticker, computed_at="t", price=100.0, recommendation="buy", rr_pass=True, rr=3.5,
        conviction=60, entry=100.0, stop=90.0, target=135.0)])


def test_risk_off_downgrades_new_buys_to_watch_with_the_reason(conn, user):
    _buy_idea(conn, user.id)
    buy(conn, user.id, "MSFT", 5, 300.0)
    _store_regime(conn, "risk_off")
    d = suggestions.build_digest(conn, "2026-10-28", user.id)
    [o] = d["opportunities"]
    assert o["recommendation"] == "watch" and o["regime_note"].startswith("Risk-off market regime")
    assert [a["ticker"] for a in d["holdings_alerts"]] == ["MSFT"]     # sells/holdings never suppressed
    assert d["downgraded"] and not d["paused"]


def test_pause_suppresses_and_off_ignores(conn, user):
    _buy_idea(conn, user.id)
    _store_regime(conn, "risk_off")
    risk.set_regime_gate(conn, user.id, "pause")
    d = suggestions.build_digest(conn, "2026-10-28", user.id)
    assert d["opportunities"] == [] and d["paused"][0].startswith("Risk-off")
    risk.set_regime_gate(conn, user.id, "off")
    d = suggestions.build_digest(conn, "2026-10-28", user.id)
    assert d["opportunities"][0]["recommendation"] == "buy" and not d["paused"]


def test_an_unknown_or_risk_on_regime_gates_nothing(conn, user):
    _buy_idea(conn, user.id)
    for label in ("unknown", "risk_on", "neutral"):
        _store_regime(conn, label, 0)
        assert suggestions.build_digest(conn, "2026-10-28", user.id)["opportunities"][0]["recommendation"] == "buy"


def test_a_downgrade_holds_bot_entries_with_the_reason(conn, user):
    ledger.record(conn, user.id, ticker="USD", side="DEPOSIT", qty=10_000, currency_code="USD")
    bars = [{"date": f"2026-0{8 + i // 31}-{i % 31 + 1:02d}", "open": 100 + i, "high": 100 + i,
             "low": 100 + i, "close": 100 + i, "volume": 1e6} for i in range(60)]
    db.upsert_ohlc(conn, [OHLCSeries(ticker="AAPL", interval="daily", bars_json=json.dumps(bars), fetched_at="t")])
    bot, _ = bots.validate_new(conn, user.id, {"strategy": "trend", "tickers": ["AAPL"],
                                               "amount": 5_000, "currency": "USD"})
    bot = db.create_bot(conn, bot)
    _store_regime(conn, "risk_off")
    bots._LAST_SKIP.clear()
    quote = LiveQuote(ticker="AAPL", price=100.0, regular_price=100.0, change_pct=0.0, previous_close=100.0,
                      market_state="LIVE", fetched_at=LIVE.isoformat(timespec="seconds"), currency="USD")
    assert bots.run(conn, LIVE, lambda ts: [quote]) == 0
    assert db.get_bot_events(conn, bot.id)[0].reason.startswith("New entries paused — Risk-off")


# ---------- auto-mute ----------

def _perf(comp, hit_rate, n):
    return SignalPerformance(component=comp, horizon_days=7, window_days=30, n=n, hits=0,
                             hit_rate=hit_rate, mean_net=0.0, computed_at="t")


def _state(comp, muted):
    return SignalMute(component=comp, muted=muted, since="t0" if muted else None, hit_rate=None,
                      n=0, reason="", updated_at="t0")


def test_mute_below_the_line_with_enough_outcomes_only():
    [m] = automute.next_mutes([_perf("golden_cross", 31.0, 16)], {}, "t1")
    assert m.muted and m.since == "t1" and "31% hit rate over 30d, n=16" in m.reason
    [m] = automute.next_mutes([_perf("golden_cross", 10.0, config.SIGNAL_MUTE_MIN_N - 1)], {}, "t1")
    assert not m.muted and "needs n≥" in m.reason


def test_hysteresis_keeps_a_muted_signal_muted_until_clearly_better():
    current = {"golden_cross": _state("golden_cross", True)}
    between = config.SIGNAL_MUTE_BELOW + config.SIGNAL_UNMUTE_MARGIN - 1
    [m] = automute.next_mutes([_perf("golden_cross", between, 20)], current, "t1")
    assert m.muted and m.since == "t0"
    [m] = automute.next_mutes([_perf("golden_cross", between + 1, 20)], current, "t1")
    assert not m.muted and m.since is None
    [m] = automute.next_mutes([_perf("golden_cross", 90.0, 3)], current, "t1")   # thin data: no change
    assert m.muted


def test_performance_scores_each_component_in_its_own_direction(conn):
    day = _iso(20)
    bars = [{"date": _iso(i), "open": 100.0 - (30 - i), "high": 1, "low": 1, "close": 100.0 - (30 - i),
             "volume": 1} for i in range(30, -1, -1)]   # steadily falling
    db.upsert_ohlc(conn, [OHLCSeries(ticker="X", interval="daily", bars_json=json.dumps(bars), fetched_at="t")])
    conn.executemany("INSERT INTO boom_component_history VALUES (?, ?, ?, ?, ?)", [
        ("X", day, "golden_cross", 20, day), ("X", day, "death_cross", -20, day)])
    perf = {(p.component, p.horizon_days): p for p in automute.performance(conn, TODAY)}
    assert perf[("death_cross", 7)].hit_rate == 100.0     # bearish + falling = a hit
    assert perf[("golden_cross", 7)].hit_rate == 0.0
    assert ("golden_cross", 30) not in perf                # 30-day window hasn't elapsed


def _score(**components):
    flags = {k: k in components for k in (
        "golden_cross", "rsi_recovery", "insider_cluster_buy", "congress_buy", "short_squeeze",
        "wsb_rising", "analyst_upgrade", "death_cross", "insider_cluster_sell", "overbought_rsi",
        "congress_sale", "analyst_downgrade_cluster", "extreme_greed")}
    return BoomScore(ticker="AAPL", computed_at="t", score=sum(components.values()),
                     components=json.dumps(components), **flags)


def test_overrides_beat_the_shared_mute_set(conn, user):
    db.upsert_signal_mutes(conn, [_state("golden_cross", True)])
    assert automute.muted_for(conn, user.id) == {"golden_cross"}
    db.set_mute_override(conn, user.id, "golden_cross", "on")
    db.set_mute_override(conn, user.id, "congress_buy", "off")
    assert automute.muted_for(conn, user.id) == {"congress_buy"}
    db.set_mute_override(conn, user.id, "golden_cross", None)
    assert automute.muted_for(conn, user.id) == {"golden_cross", "congress_buy"}


def test_applying_mutes_recomputes_the_score_and_the_flags():
    b = _score(golden_cross=20, congress_buy=15)
    muted = automute.apply_mutes(b, frozenset({"golden_cross"}))
    assert muted.score == 15 and muted.golden_cross is False and json.loads(muted.muted) == ["golden_cross"]
    back = automute.apply_mutes(muted, frozenset())
    assert back.score == 35 and back.golden_cross is True


def test_scoring_leaves_a_muted_component_out_but_keeps_its_points(conn):
    b = boom_score._score_ticker("AAPL", conn, "t", 10.0, False, None, None, None, None,
                                 frozenset({"fear_greed_contrarian"}))
    assert json.loads(b.components).get("fear_greed_contrarian") == 10
    assert b.score == 0 and b.fear_greed_contrarian is False and "Muted" in b.score_note


# ---------- API ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "regime.db"))
    import importlib

    from app import config as cfg, main as main_module, quotes
    importlib.reload(cfg)
    importlib.reload(main_module)
    monkeypatch.setattr(quotes, "get_quotes", lambda tickers, ttl_seconds=None: [])
    with TestClient(main_module.app) as c:
        authenticate(c)
        yield c


def test_api_regime_gate_and_overrides(client):
    status = client.get("/api/risk/status").json()
    assert status["regime"]["label"] == "unknown" and status["regime_gate"] == "downgrade"
    assert client.put("/api/risk/settings", json={"regime_gate": "maybe"}).status_code == 422
    status = client.put("/api/risk/settings", json={"regime_gate": "pause"}).json()
    assert status["regime_gate"] == "pause" and status["drawdown"]["max_pct"] is None
    items = {i["component"]: i for i in client.get("/api/automute").json()["items"]}
    assert items["golden_cross"]["counts"] and items["golden_cross"]["override"] == "auto"
    items = {i["component"]: i for i in
             client.put("/api/automute/golden_cross", json={"mode": "off"}).json()["items"]}
    assert items["golden_cross"]["override"] == "off" and not items["golden_cross"]["counts"]
    assert client.put("/api/automute/nope", json={"mode": "off"}).status_code == 404
