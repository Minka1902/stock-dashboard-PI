"""The drawdown kill switch: time-weighted drawdown, trip, acknowledge, and
what it pauses (new buys and bot entries; never exits)."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import analytics, bots, db, ledger, risk, suggestions
from app.models import EquitySnapshot, LiveQuote, OHLCBar, OHLCSeries
from tests.conftest import authenticate

LIVE = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)


def _snap(user_id, date, value, flow=0.0, base="USD", account="manual"):
    return EquitySnapshot(user_id=user_id, account=account, date=date, base_currency=base,
                          value_base=value, cash_base=value, invested_base=0.0, flow_base=flow,
                          computed_at=f"{date}T21:00:00+00:00")


@pytest.fixture
def user(conn):
    return db.create_user(conn, "risk@x.co", "h", "t")


def _series(conn, user_id, values, flows=None):
    flows = flows or [0.0] * len(values)
    db.upsert_equity_snapshots(conn, [
        _snap(user_id, f"2026-10-{i + 1:02d}", v, f) for i, (v, f) in enumerate(zip(values, flows))])


# ---------- maths ----------

def test_flows_never_look_like_returns():
    snaps = [_snap(1, "d1", 1000), _snap(1, "d2", 2000, flow=1000), _snap(1, "d3", 1800)]
    idx = analytics.twr_index(snaps)
    assert [round(v, 4) for _, v in idx] == [1.0, 1.0, 0.9]
    assert analytics.drawdown_from_peak(idx) == {"pct": 10.0, "peak_date": "d1", "as_of": "d3"}


def test_a_base_currency_switch_starts_the_series_afresh():
    snaps = [_snap(1, "d1", 1000), _snap(1, "d2", 3600, base="ILS"), _snap(1, "d3", 3240, base="ILS")]
    run = analytics.current_base_run(snaps)
    assert [s.date for s in run] == ["d2", "d3"]
    assert analytics.drawdown_from_peak(analytics.twr_index(run))["pct"] == 10.0


def test_a_single_snapshot_has_no_drawdown():
    assert analytics.drawdown_from_peak(analytics.twr_index([_snap(1, "d1", 1000)])) is None


# ---------- trip & acknowledge ----------

def test_the_switch_trips_at_the_limit_once_and_holds_until_acknowledged(conn, user):
    risk.set_max_drawdown(conn, user.id, 10.0)
    _series(conn, user.id, [1000, 950])
    assert risk.check_user(conn, user.id) is None
    _series(conn, user.id, [1000, 950, 890])
    alert = risk.check_user(conn, user.id, now="2026-10-03T21:00:00+00:00")
    assert alert is not None and alert.type == "risk" and alert.user_id == user.id
    assert "11.0% below its high of 2026-10-01" in alert.message
    assert risk.check_user(conn, user.id) is None          # already tripped
    assert risk.halt_reasons(conn, user.id) == [
        "Drawdown kill switch: 11.0% below the high (limit 10%)"]
    assert risk.acknowledge(conn, user.id, now="2026-10-03T22:00:00+00:00")
    assert risk.halt_reasons(conn, user.id) == []
    assert risk.acknowledge(conn, user.id) is False


def test_acknowledging_resets_the_high_so_it_does_not_trip_again_next_day(conn, user):
    risk.set_max_drawdown(conn, user.id, 10.0)
    _series(conn, user.id, [1000, 880])
    risk.check_user(conn, user.id, now="2026-10-02T21:00:00+00:00")
    risk.acknowledge(conn, user.id, now="2026-10-02T22:00:00+00:00")
    _series(conn, user.id, [1000, 880, 860])
    assert risk.check_user(conn, user.id) is None           # 2.3% below the new high
    _series(conn, user.id, [1000, 880, 860, 780])
    assert risk.check_user(conn, user.id) is not None       # 11.4% below 880


def test_funding_a_bot_is_a_flow_not_a_drawdown(conn, user):
    risk.set_max_drawdown(conn, user.id, 10.0)
    _series(conn, user.id, [10_000, 5_000], flows=[0.0, -5_000])
    assert risk.current_drawdown(conn, user.id)["pct"] == 0.0
    assert risk.check_user(conn, user.id) is None


def test_no_limit_means_no_trip(conn, user):
    _series(conn, user.id, [1000, 100])
    assert risk.check_user(conn, user.id) is None and risk.check_drawdowns(conn) == []


# ---------- what a trip pauses ----------

def _trip(conn, user_id):
    risk.set_max_drawdown(conn, user_id, 5.0)
    _series(conn, user_id, [1000, 900])
    assert risk.check_user(conn, user_id) is not None


def test_a_tripped_switch_pauses_new_buy_suggestions(conn, user):
    _trip(conn, user.id)
    digest = suggestions.build_digest(conn, "2026-10-28", user.id)
    assert digest["paused"] and digest["opportunities"] == [] and digest["new_ideas"] == []
    _, text, _ = suggestions.render_email(digest)
    assert "NEW BUYS PAUSED" in text


def _bars(n=60):
    return [OHLCBar(date=f"2026-08-{i + 1:02d}" if i < 31 else f"2026-09-{i - 30:02d}",
                    open=100 + i, high=100 + i, low=100 + i, close=100 + i, volume=1e6)
            for i in range(n)]


def _quote(t, price):
    return LiveQuote(ticker=t, price=price, regular_price=price, change_pct=0.0,
                     previous_close=price, market_state="LIVE",
                     fetched_at=LIVE.isoformat(timespec="seconds"), currency="USD")


def test_a_tripped_switch_pauses_bot_entries_but_not_exits(conn, user):
    import json
    ledger.record(conn, user.id, ticker="USD", side="DEPOSIT", qty=10_000, currency_code="USD")
    db.upsert_ohlc(conn, [OHLCSeries(ticker="AAPL", interval="daily", fetched_at="t",
                                     bars_json=json.dumps([b.model_dump() for b in _bars()]))])
    bot, _ = bots.validate_new(conn, user.id, {"strategy": "trend", "tickers": ["AAPL"],
                                               "amount": 5_000, "currency": "USD"})
    bot = db.create_bot(conn, bot)
    bots.run(conn, LIVE, lambda ts: [_quote(t, 100.0) for t in ts])
    assert len(db.get_portfolio(conn, user.id, bot.account)) == 1
    _trip(conn, user.id)
    bots._LAST_SKIP.clear()
    assert bots.run(conn, LIVE, lambda ts: [_quote(t, 50.0) for t in ts]) == 1   # the stop still sells
    assert db.get_portfolio(conn, user.id, bot.account) == []
    db.set_bot_state(conn, bot.id, {"pos": {}, "cooldown": {}})
    assert bots.run(conn, LIVE, lambda ts: [_quote(t, 100.0) for t in ts]) == 0
    assert db.get_bot_events(conn, bot.id)[0].reason.startswith("New entries paused — Drawdown")


# ---------- API ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "risk.db"))
    import importlib

    from app import config, main as main_module, quotes
    importlib.reload(config)
    importlib.reload(main_module)
    monkeypatch.setattr(quotes, "get_quotes", lambda tickers, ttl_seconds=None: [])
    with TestClient(main_module.app) as c:
        authenticate(c)
        yield c, main_module


def test_api_settings_trip_and_acknowledge(client):
    c, main_module = client
    status = c.get("/api/risk/status").json()
    assert status["halted"] is False and status["drawdown"]["max_pct"] is None
    r = c.put("/api/risk/settings", json={"max_drawdown_pct": 95})
    assert r.status_code == 422 and "max_drawdown_pct" in r.json()["detail"]["errors"]
    assert c.post("/api/risk/drawdown/acknowledge").status_code == 409
    uid = db.get_users(main_module.conn)[0].id
    _series(main_module.conn, uid, [1000, 850])
    status = c.put("/api/risk/settings", json={"max_drawdown_pct": 10}).json()
    assert status["halted"] is True and status["drawdown"]["tripped_pct"] == 15.0
    assert any(a["type"] == "risk" for a in c.get("/api/alerts").json()["alerts"])
    status = c.post("/api/risk/drawdown/acknowledge").json()
    assert status["halted"] is False
