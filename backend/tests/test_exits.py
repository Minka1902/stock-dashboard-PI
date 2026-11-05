"""Exit rules on holdings: storage, the evaluator's edge cases, per-user
alerts and the rules API."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import db, exits, notify
from app.models import Alert, LiveQuote, NotifyProfile, PositionRule, StockAnalysis
from app.validation import validate_exit_rule
from tests.conftest import authenticate, buy

LIVE = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)    # Tue: NYSE 11:00, TASE 17:00
CLOSED = datetime(2026, 10, 27, 3, 0, tzinfo=timezone.utc)


def _quote(ticker, price, fetched_at=LIVE, market_state="LIVE"):
    return LiveQuote(ticker=ticker, price=price, regular_price=price, change_pct=0.0,
                     previous_close=price, market_state=market_state,
                     fetched_at=fetched_at.isoformat(timespec="seconds"),
                     currency="ILS" if ticker.endswith(".TA") else "USD")


def _quotes(prices):
    return lambda tickers: [_quote(t, prices[t]) for t in tickers if prices.get(t)]


@pytest.fixture
def user(conn):
    return db.create_user(conn, "exit@x.co", "h", "t")


def _rule(conn, user_id, ticker="AAPL", account="manual", at="2026-10-27T10:00:00+00:00", **levels):
    return db.upsert_position_rule(conn, PositionRule(
        user_id=user_id, account=account, ticker=ticker, created_at=at, updated_at=at, **levels))


def _exit_alerts(conn, user_id):
    return [a for a in db.get_alerts(conn, user_id) if a.type == "exit"]


# ---------- storage ----------

def test_stop_price_and_stop_pct_are_mutually_exclusive(conn, user):
    with pytest.raises(sqlite3.IntegrityError):
        _rule(conn, user.id, stop_price=90.0, stop_pct=5.0)


def test_the_trailing_high_only_ratchets_up(conn, user):
    rule = _rule(conn, user.id, trailing_pct=10.0, trail_high=100.0)
    assert db.ratchet_trail_high(conn, rule, 95.0) == 100.0
    assert db.ratchet_trail_high(conn, rule, 120.0) == 120.0
    assert db.ratchet_trail_high(conn, rule, 110.0) == 120.0


def test_an_edit_wins_over_a_ratchet_from_the_old_version(conn, user):
    old = _rule(conn, user.id, trailing_pct=10.0, trail_high=100.0)
    _rule(conn, user.id, at="2026-10-27T11:00:00+00:00", trailing_pct=5.0, trail_high=100.0)
    assert db.ratchet_trail_high(conn, old, 150.0) is None
    assert db.get_position_rule(conn, user.id, "manual", "AAPL").trail_high == 100.0


# ---------- pure levels ----------

def test_levels_and_crossings():
    r = PositionRule(user_id=1, ticker="X", stop_pct=10.0, take_profit=150.0,
                     trailing_pct=20.0, trail_high=140.0, created_at="t", updated_at="t")
    assert exits.stop_level(r, 100.0) == 90.0 and exits.trail_level(r) == 112.0
    assert exits.crossed(r, 100.0, 120.0) == []
    assert exits.crossed(r, 100.0, 111.0) == [("trailing", 112.0)]
    assert exits.crossed(r, 100.0, 89.0) == [("stop", 90.0), ("trailing", 112.0)]
    assert exits.crossed(r, 100.0, 150.0) == [("take_profit", 150.0)]


# ---------- evaluator ----------

def test_nothing_is_checked_while_the_market_is_closed(conn, user):
    buy(conn, user.id, "AAPL", 10, 100.0)
    _rule(conn, user.id, stop_price=95.0)
    assert exits.run(conn, CLOSED, _quotes({"AAPL": 80.0})) == 0
    assert _exit_alerts(conn, user.id) == []


def test_a_stop_fires_exactly_once_and_an_edit_re_arms_it(conn, user, monkeypatch):
    pushed = []
    monkeypatch.setattr(notify, "push_alert", lambda c, a: pushed.append(a.dedup_key))
    buy(conn, user.id, "AAPL", 10, 100.0)
    _rule(conn, user.id, stop_price=95.0)
    assert exits.run(conn, LIVE, _quotes({"AAPL": 95.0})) == 1
    assert exits.run(conn, LIVE, _quotes({"AAPL": 94.0})) == 0
    [alert] = _exit_alerts(conn, user.id)
    assert alert.title == "Stop hit" and alert.severity == "high" and alert.user_id == user.id
    assert alert.dedup_key.startswith("exit|AAPL|") and len(pushed) == 1
    _rule(conn, user.id, at="2026-10-27T12:00:00+00:00", stop_price=95.0)
    assert exits.run(conn, LIVE, _quotes({"AAPL": 94.0})) == 1


def test_a_gap_through_the_stop_reports_the_actual_price(conn, user):
    buy(conn, user.id, "AAPL", 10, 100.0)
    _rule(conn, user.id, stop_price=95.0)
    exits.run(conn, LIVE, _quotes({"AAPL": 88.0}))
    [alert] = _exit_alerts(conn, user.id)
    assert "AAPL at 88.00 USD, at or below your stop of 95.00" in alert.message
    assert "gapped 7.4% through" in alert.message and "-12.0%" in alert.message


def test_the_trail_follows_the_high_and_fires_below_it(conn, user):
    buy(conn, user.id, "AAPL", 10, 100.0)
    _rule(conn, user.id, trailing_pct=10.0, trail_high=100.0)
    exits.run(conn, LIVE, _quotes({"AAPL": 130.0}))
    assert db.get_position_rule(conn, user.id, "manual", "AAPL").trail_high == 130.0
    assert exits.run(conn, LIVE, _quotes({"AAPL": 118.0})) == 0   # trail at 130 × 0.9 = 117
    assert exits.run(conn, LIVE, _quotes({"AAPL": 116.0})) == 1
    [alert] = _exit_alerts(conn, user.id)
    assert alert.title == "Trailing stop hit"


def test_take_profit_and_stop_pct_from_cost(conn, user):
    buy(conn, user.id, "MSFT", 4, 200.0)
    _rule(conn, user.id, ticker="MSFT", stop_pct=5.0, take_profit=230.0)
    assert exits.run(conn, LIVE, _quotes({"MSFT": 191.0})) == 0
    assert exits.run(conn, LIVE, _quotes({"MSFT": 189.0})) == 1   # 200 × 0.95 = 190
    assert exits.run(conn, LIVE, _quotes({"MSFT": 231.0})) == 1
    assert {a.title for a in _exit_alerts(conn, user.id)} == {"Stop hit", "Take-profit reached"}


def test_a_tase_holding_is_compared_in_shekels(conn, user):
    buy(conn, user.id, "TEVA.TA", 100, 50.0, currency="ILS")
    _rule(conn, user.id, ticker="TEVA.TA", stop_price=48.0)
    assert exits.run(conn, LIVE, _quotes({"TEVA.TA": 48.5})) == 0
    assert exits.run(conn, LIVE, _quotes({"TEVA.TA": 47.9})) == 1
    [alert] = _exit_alerts(conn, user.id)
    assert "TEVA.TA at 47.90 ILS" in alert.message


def test_a_stale_or_post_market_quote_never_fires(conn, user):
    buy(conn, user.id, "AAPL", 10, 100.0)
    _rule(conn, user.id, stop_price=95.0)
    old = LIVE - timedelta(seconds=exits.quotes.config.ENGINE_QUOTE_MAX_AGE_SECONDS + 1)
    assert exits.run(conn, LIVE, lambda ts: [_quote("AAPL", 50.0, fetched_at=old)]) == 0
    assert exits.run(conn, LIVE, lambda ts: [_quote("AAPL", 50.0, market_state="POST")]) == 0


def test_rules_on_tickers_no_longer_held_are_ignored(conn, user):
    _rule(conn, user.id, stop_price=95.0)
    calls = []
    assert exits.run(conn, LIVE, lambda ts: calls.append(ts) or []) == 0
    assert calls == []   # no quote call at all


def test_an_inactive_rule_never_fires(conn, user):
    buy(conn, user.id, "AAPL", 10, 100.0)
    _rule(conn, user.id, stop_price=95.0, active=False)
    assert exits.run(conn, LIVE, _quotes({"AAPL": 80.0})) == 0


def test_a_rule_deleted_mid_trigger_does_not_fire(conn, user):
    buy(conn, user.id, "AAPL", 10, 100.0)
    rule = _rule(conn, user.id, stop_price=95.0)
    db.delete_position_rule(conn, user.id, "manual", "AAPL")
    alert = Alert(dedup_key="exit|AAPL|x", created_at="t", ticker="AAPL", type="exit",
                  severity="high", title="Stop hit", message="m", user_id=user.id)
    assert db.fire_rule(conn, rule, "stop", alert) is False
    assert _exit_alerts(conn, user.id) == []


# ---------- per-user alerts ----------

def test_a_users_own_alert_is_theirs_alone(conn, user):
    other = db.create_user(conn, "other@x.co", "h", "t")
    db.upsert_alerts(conn, [
        Alert(dedup_key="mine", created_at="1", ticker="AAPL", type="exit", severity="high",
              title="t", message="m", user_id=user.id),
        Alert(dedup_key="market", created_at="2", ticker="AAPL", type="golden_cross",
              severity="medium", title="t", message="m")])
    assert {a.dedup_key for a in db.get_alerts(conn, user.id)} == {"mine", "market"}
    assert {a.dedup_key for a in db.get_alerts(conn, other.id)} == {"market"}
    assert [a.dedup_key for a in db.get_alerts_for(conn, other.id, "AAPL")] == ["market"]
    assert db.count_unread_alerts(conn, other.id) == 1
    db.mark_alerts_read(conn, other.id)
    assert db.count_unread_alerts(conn, user.id) == 2
    db.delete_user(conn, user.id)
    assert not db.alert_exists(conn, "mine") and db.alert_exists(conn, "market")


def test_a_users_own_alert_is_pushed_to_them_only(conn, user, monkeypatch):
    other = db.create_user(conn, "other@x.co", "h", "t")
    for u in (user, other):
        db.upsert_notify_profile(conn, u.id, NotifyProfile(email=u.email, email_enabled=True))
    sent = []
    monkeypatch.setattr(notify, "send_email", lambda p, *a: sent.append(p.email) or "sent")
    monkeypatch.setattr(notify, "send_sms", lambda p, *a: "skipped")
    notify.push_alert(conn, Alert(dedup_key="k", created_at="t", ticker="AAPL", type="exit",
                                  severity="high", title="t", message="m", user_id=user.id))
    assert sent == ["exit@x.co"]


# ---------- suggested stop & validation ----------

def test_the_suggested_stop_explains_itself():
    a = StockAnalysis(ticker="AAPL", computed_at="c", price=100.0, entry=100.0, atr14=3.0,
                      stop=94.0, stop_basis="atr")
    s = exits.suggested_stop(a, 100.0)
    assert s["stop_price"] == 94.0 and s["reason"] == "2.0×ATR below the last close — 6.0% below 100.00"
    assert exits.suggested_stop(a, 93.0) is None and exits.suggested_stop(None, 100.0) is None


def test_validation_names_each_bad_field():
    _, e = validate_exit_rule({"stop_price": 101, "take_profit": 99, "trailing_pct": 60}, 100.0, 90.0)
    assert set(e) == {"stop_price", "take_profit", "trailing_pct"}
    _, e = validate_exit_rule({"stop_price": 90, "stop_pct": 5}, 100.0, 90.0)
    assert "not both" in e["stop_pct"]
    _, e = validate_exit_rule({"stop_pct": 5}, 100.0, 110.0)   # 104.5 is above the price
    assert "must be below" in e["stop_pct"]
    _, e = validate_exit_rule({}, 100.0, 90.0)
    assert "Set a stop" in e["stop_price"]
    clean, e = validate_exit_rule({"stop_price": "95", "trailing_pct": 8}, 100.0, 90.0)
    assert e == {} and clean["stop_price"] == 95.0 and clean["active"] is True


# ---------- API ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "exits.db"))
    import importlib

    from app import config, main as main_module, quotes
    importlib.reload(config)
    importlib.reload(main_module)
    monkeypatch.setattr(quotes, "get_quotes", lambda tickers, ttl_seconds=None: [
        _quote(t, 100.0) for t in tickers])
    with TestClient(main_module.app) as c:
        authenticate(c)
        c.post("/api/portfolio", json={"ticker": "AAPL", "shares": 10, "avg_cost": 90})
        yield c


def test_api_rule_lifecycle(client):
    r = client.get("/api/portfolio/AAPL/rules")
    assert r.status_code == 200 and r.json()["held"] and r.json()["rule"] is None
    r = client.put("/api/portfolio/AAPL/rules", json={"stop_price": 95, "trailing_pct": 10})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rule"]["trail_high"] == 100.0 and body["levels"]["trailing"] == 90.0
    assert body["levels"]["stop"] == 95.0 and body["suggested"] is None
    items = client.get("/api/portfolio/rules").json()["items"]
    assert [(i["ticker"], i["rule"]["stop_price"]) for i in items] == [("AAPL", 95.0)]
    assert client.delete("/api/portfolio/AAPL/rules").status_code == 200
    assert client.delete("/api/portfolio/AAPL/rules").status_code == 404


def test_api_refuses_bad_levels_and_unheld_tickers(client):
    r = client.put("/api/portfolio/AAPL/rules", json={"stop_price": 120})
    assert r.status_code == 422 and "stop_price" in r.json()["detail"]["errors"]
    assert client.put("/api/portfolio/MSFT/rules", json={"stop_price": 90}).status_code == 409
    assert client.get("/api/portfolio/AAPL/rules?account=live").status_code == 409


def test_api_rules_are_private(client):
    client.put("/api/portfolio/AAPL/rules", json={"stop_price": 95})
    client.post("/api/auth/logout")
    authenticate(client, email="second@example.com")
    assert client.get("/api/portfolio/rules").json()["items"] == []
    assert client.get("/api/portfolio/AAPL/rules").json()["rule"] is None
