"""Order guardrails, the global kill switch with TOTP release, the audit
chain and log redaction (STC-50..54)."""
import json
import logging
from datetime import datetime, timezone

import pyotp
import pytest
from fastapi.testclient import TestClient

from app import audit, auth, bots, db, execution, risk
from app.logging_config import RedactSecrets
from app.models import Bot, LiveQuote, Order
from tests.conftest import authenticate

LIVE = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)
CLOSED = datetime(2026, 10, 27, 3, 0, tzinfo=timezone.utc)


def _q(price):
    return lambda ts: [LiveQuote(ticker=t, price=price, regular_price=price, change_pct=0.0,
                                 previous_close=price, market_state="LIVE",
                                 fetched_at=LIVE.isoformat(timespec="seconds"), currency="USD") for t in ts]


@pytest.fixture
def user(conn):
    u = db.create_user(conn, "guard@x.co", "h", "t")
    execution.reset_paper(conn, u.id, 100_000, "USD", CLOSED)
    return u


def _order(user_id, **kw):
    return Order(**{**dict(user_id=user_id, account="paper", ticker="AAPL", side="BUY", qty=10,
                           currency="USD", created_at="", reference_price=100.0), **kw})


def _set(conn, user_id, **kw):
    db.upsert_risk_settings(conn, db.get_risk_settings(conn, user_id).model_copy(update=kw))


def _refused(conn, user_id):
    return [json.loads(e.detail) for e in db.get_audit(conn, user_id) if e.kind == "order_refused"]


# ---------- guardrails ----------

@pytest.mark.parametrize("settings,order,now,expected", [
    ({}, {"qty": 300}, LIVE, "limit per order is 25,000"),
    ({"max_order_notional": 1e9}, {"qty": 600}, LIVE, "limit per position is 50%"),
    ({}, {}, CLOSED, "market isn't open"),
    ({"trading_halted_at": "2026-10-27T14:00:00+00:00"}, {}, LIVE, "halted by your kill switch"),
])
def test_each_guardrail_refuses_with_its_reason_and_is_audited(conn, user, settings, order, now, expected):
    _set(conn, user.id, **settings)
    with pytest.raises(execution.OrderRejected, match=expected):
        execution.submit(conn, _order(user.id, **order), now, _q(100.0))
    assert expected.split()[-1] in _refused(conn, user.id)[0]["reason"]


def test_daily_count_and_notional_limits(conn, user):
    _set(conn, user.id, max_orders_per_day=2)
    execution.submit(conn, _order(user.id), LIVE, _q(100.0))
    execution.submit(conn, _order(user.id), LIVE, _q(100.0))
    with pytest.raises(execution.OrderRejected, match="2 orders today"):
        execution.submit(conn, _order(user.id), LIVE, _q(100.0))
    _set(conn, user.id, max_orders_per_day=50, max_notional_per_day=2500)
    with pytest.raises(execution.OrderRejected, match="would total 3,00"):
        execution.submit(conn, _order(user.id), LIVE, _q(100.0))


def test_bots_cannot_bypass_the_guardrails(conn, user):
    from tests.test_bots import UP, _bars, _seed_bars
    db.upsert_risk_settings(conn, db.get_risk_settings(conn, user.id).model_copy(update={"max_position_pct": 10}))
    from app import ledger
    ledger.record(conn, user.id, ticker="USD", side="DEPOSIT", qty=10_000, currency_code="USD")
    _seed_bars(conn, "AAPL", _bars(UP))
    bot, _ = bots.validate_new(conn, user.id, {"strategy": "trend", "tickers": ["AAPL"], "amount": 5_000,
                                               "currency": "USD", "params": {"size_pct": 50}})
    bot = db.create_bot(conn, bot)
    bots._LAST_SKIP.clear()
    assert bots.run(conn, LIVE, _q(100.0)) == 0
    assert "limit per position is 10%" in db.get_bot_events(conn, bot.id)[0].reason


def test_other_accounts_are_refused_at_the_choke_point(conn, user):
    for account, msg in (("live", "no live broker"), ("manual", "paper, alpaca or a bot account")):
        with pytest.raises(execution.OrderRejected) as e:
            execution.submit(conn, _order(user.id, account=account), LIVE, _q(100.0))
        assert msg in (e.value.reason + json.dumps(e.value.fields))


# ---------- per-trade limits (STC-82) and exits ----------

def _buy(conn, user_id, qty=10, **kw):
    return execution.submit(conn, _order(user_id, qty=qty, **kw), LIVE, _q(100.0))


def test_an_exit_passes_every_limit_even_the_kill_switch_but_a_bigger_sell_does_not(conn, user):
    _buy(conn, user.id, 10)
    _set(conn, user.id, trading_halted_at="2026-10-27T14:00:00+00:00", max_order_notional=100,
         max_orders_per_day=1, no_entry_after_open_min=300, require_stop=True)
    out = execution.submit(conn, _order(user.id, side="SELL", qty=10), CLOSED, _q(100.0))
    assert out.status == "submitted"            # rests until the open; nothing refused it
    with pytest.raises(execution.OrderRejected, match="kill switch"):
        execution.submit(conn, _order(user.id, side="SELL", qty=11), LIVE, _q(100.0))


def test_max_loss_per_trade_is_measured_at_the_stop_and_says_what_fits(conn, user):
    _set(conn, user.id, max_loss_per_trade=100)
    with pytest.raises(execution.OrderRejected, match="lose 150.00 at the stop.*20 shares would fit"):
        _buy(conn, user.id, 30, plan_stop=95.0)
    with pytest.raises(execution.OrderRejected, match="with no stop"):
        _buy(conn, user.id, 10)
    assert _buy(conn, user.id, 20, plan_stop=95.0).status == "filled"
    assert _refused(conn, user.id)[0]["guardrail"] == "loss_per_trade"


def test_a_market_entry_measures_risk_from_the_higher_of_decision_and_fill(conn, user):
    from app import guardrails
    o = _order(user.id, reference_price=100.0, fill_price=104.0, plan_stop=95.0)
    assert guardrails.entry_price(o) == 104.0 and guardrails.loss_at_stop(o) == 90.0
    assert guardrails.entry_price(_order(user.id, type="limit", limit_price=99.0)) == 99.0


def test_stop_and_target_must_bracket_the_entry_and_can_be_required(conn, user):
    with pytest.raises(execution.OrderRejected, match="stop 101 must be below the entry 100"):
        _buy(conn, user.id, plan_stop=101.0)
    with pytest.raises(execution.OrderRejected, match="target 99 must be above"):
        _buy(conn, user.id, plan_stop=95.0, plan_target=99.0)
    _set(conn, user.id, require_stop=True)
    with pytest.raises(execution.OrderRejected, match="require a stop"):
        _buy(conn, user.id)


def test_minimum_reward_risk(conn, user):
    _set(conn, user.id, min_reward_risk=1.5)
    with pytest.raises(execution.OrderRejected, match="reward:risk is 0.80R; your minimum is 1.5R"):
        _buy(conn, user.id, plan_stop=95.0, plan_target=104.0)
    with pytest.raises(execution.OrderRejected, match="set a stop and a target"):
        _buy(conn, user.id, plan_stop=95.0)
    assert _buy(conn, user.id, plan_stop=95.0, plan_target=110.0).status == "filled"


def test_the_daily_loss_limit_counts_todays_entries_at_their_stops(conn, user):
    _set(conn, user.id, max_daily_loss=120)
    _buy(conn, user.id, plan_stop=95.0)
    resting = _buy(conn, user.id, type="limit", limit_price=90.0, tif="gtc", plan_stop=89.0)
    execution.cancel(conn, user.id, resting.id)          # cancelled: no longer at risk
    _buy(conn, user.id, plan_stop=95.0)
    with pytest.raises(execution.OrderRejected, match="could lose 152.00 at their stops"):   # 51 + 51 (filled at 100.10) + 50
        _buy(conn, user.id, plan_stop=95.0)


@pytest.mark.parametrize("now,ticker,ok", [
    (datetime(2026, 10, 27, 13, 40, tzinfo=timezone.utc), "AAPL", False),   # 09:40 New York
    (datetime(2026, 10, 27, 13, 46, tzinfo=timezone.utc), "AAPL", True),    # 09:46
    (datetime(2026, 10, 27, 19, 31, tzinfo=timezone.utc), "AAPL", False),   # 15:31
    (datetime(2026, 11, 27, 17, 31, tzinfo=timezone.utc), "AAPL", False),   # 12:31 on a 13:00 half day
    (datetime(2026, 11, 27, 17, 29, tzinfo=timezone.utc), "AAPL", True),
])
def test_the_entry_window_follows_each_days_real_session(conn, user, now, ticker, ok):
    _set(conn, user.id, no_entry_after_open_min=15, no_entry_before_close_min=30)
    order = _order(user.id, ticker=ticker)
    if ok:
        execution.submit(conn, order, now, _q(100.0))
    else:
        with pytest.raises(execution.OrderRejected, match="entry window"):
            execution.submit(conn, order, now, _q(100.0))


def test_a_bot_entry_carries_its_stop_and_target_to_the_checks(conn, user):
    from tests.test_bots import UP, _bars, _seed_bars
    from app import ledger
    ledger.record(conn, user.id, ticker="USD", side="DEPOSIT", qty=10_000, currency_code="USD")
    _seed_bars(conn, "AAPL", _bars(UP))
    bot, _ = bots.validate_new(conn, user.id, {"strategy": "trend", "tickers": ["AAPL"], "amount": 5_000,
                                               "currency": "USD", "params": {"size_pct": 10}})
    bot = db.create_bot(conn, bot)
    bots._LAST_SKIP.clear()
    _set(conn, user.id, max_loss_per_trade=1)
    assert bots.run(conn, LIVE, _q(100.0)) == 0
    assert "max loss per trade is 1.00" in db.get_bot_events(conn, bot.id)[0].reason
    _set(conn, user.id, max_loss_per_trade=None)
    assert bots.run(conn, LIVE, _q(100.0)) == 1
    placed = db.get_orders(conn, user.id, bot.account)[0]
    assert placed.plan_stop == db.get_bot(conn, bot.id).state["pos"]["AAPL"]["stop"]


# ---------- kill switch ----------

def test_the_kill_switch_cancels_pauses_refuses_and_holds(conn, user):
    _set(conn, user.id, allow_outside_hours=True)
    resting = execution.submit(conn, _order(user.id, type="limit", limit_price=50.0, tif="gtc"), LIVE, _q(100.0))
    bot = db.create_bot(conn, Bot(user_id=user.id, name="b", strategy="trend", currency="USD", allocated=0,
                                  created_at="t"))
    out = risk.halt_trading(conn, user.id, "testing")
    assert out == {"cancelled_orders": [resting.id], "paused_bots": [bot.id]}
    assert db.get_bot(conn, bot.id).status == "paused"
    assert risk.halt_reasons(conn, user.id)[0].startswith("Kill switch")
    with pytest.raises(execution.OrderRejected, match="kill switch"):
        execution.submit(conn, _order(user.id), LIVE, _q(100.0))
    assert risk.release_trading(conn, user.id) and db.get_bot(conn, bot.id).status == "paused"


def test_a_step_up_needs_a_fresh_unused_code(conn):
    u = db.create_user(conn, "step@x.co", "h", "t")
    secret = pyotp.random_base32()
    db.set_totp_secret(conn, u.id, secret)
    u = db.get_user(conn, u.id)
    assert not auth.verify_step_up(u, "000000")
    code = pyotp.TOTP(secret).now()
    assert auth.verify_step_up(u, code)
    assert not auth.verify_step_up(u, code)          # no replay


# ---------- audit chain ----------

def test_the_chain_verifies_and_catches_tampering(conn, user):
    for i in range(3):
        audit.record(conn, user.id, "note", i=i)
    assert audit.verify(conn, user.id)["ok"]
    rows = db.get_audit(conn, user.id, newest_first=False)
    conn.execute("UPDATE audit_log SET detail = ? WHERE id = ?", ('{"i": 99}', rows[-2].id))
    conn.commit()
    v = audit.verify(conn, user.id)
    assert v == {"ok": False, "n": len(rows), "broken_at": rows[-2].id, "reason": "its content was changed"}


def test_deleting_a_middle_row_breaks_the_chain(conn, user):
    for i in range(3):
        audit.record(conn, user.id, "note", i=i)
    rows = db.get_audit(conn, user.id, newest_first=False)
    conn.execute("DELETE FROM audit_log WHERE id = ?", (rows[-2].id,))
    conn.commit()
    assert audit.verify(conn, user.id)["broken_at"] == rows[-1].id


def test_deleting_a_user_drops_their_chain_and_leaves_others_intact(conn, user):
    other = db.create_user(conn, "o@x.co", "h", "t")
    audit.record(conn, other.id, "note")
    audit.record(conn, user.id, "note")
    db.delete_user(conn, user.id)
    assert db.get_audit(conn, user.id) == [] and audit.verify(conn, other.id)["ok"]


# ---------- redaction ----------

def test_secrets_never_reach_a_log_line(caplog):
    logger = logging.getLogger("redaction-test")
    handler = logging.StreamHandler()
    f = RedactSecrets(("hunter2-twilio",))
    logger.addFilter(f)
    with caplog.at_level(logging.INFO, logger="redaction-test"):
        logger.info("sms via %s failed: token=abc&x=1", "hunter2-twilio")
        try:
            raise RuntimeError("password=letmein")
        except RuntimeError:
            logger.exception("boom")
    logger.removeFilter(f)
    text = "\n".join(r.getMessage() + (r.exc_text or "") for r in caplog.records)
    assert "hunter2-twilio" not in text and "abc" not in text and "letmein" not in text
    assert "[redacted]" in text
    assert handler  # the filter works on records, before any handler formats them


# ---------- API ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "guard.db"))
    import importlib

    from app import config, main as main_module, quotes
    importlib.reload(config)
    importlib.reload(main_module)
    monkeypatch.setattr(quotes, "get_quotes", lambda tickers, ttl_seconds=None: [])
    with TestClient(main_module.app) as c:
        secret = authenticate(c)
        yield c, secret


def test_api_kill_switch_release_needs_totp(client):
    c, secret = client
    status = c.post("/api/risk/kill-switch", json={"reason": "stop everything"}).json()
    assert status["halted"] and status["kill_switch"]["reason"] == "stop everything"
    assert c.post("/api/risk/kill-switch", json={}).status_code == 409
    bad = c.post("/api/risk/kill-switch/release", json={"code": "123456"})
    assert bad.status_code == 422 and "code" in bad.json()["detail"]["errors"]
    ok = c.post("/api/risk/kill-switch/release", json={"code": pyotp.TOTP(secret).now()})
    assert ok.status_code == 200 and not ok.json()["halted"]
    kinds = [e["kind"] for e in c.get("/api/audit").json()["entries"]]
    assert kinds[:3] == ["kill_switch_off", "step_up_failed", "kill_switch_on"]
    assert c.get("/api/audit").json()["verify"]["ok"]
    assert c.get("/api/audit.csv").text.startswith("id,at,kind,detail,prev_hash,hash")


def test_api_guardrail_settings_validate(client):
    c, _ = client
    r = c.put("/api/risk/settings", json={"max_position_pct": 0, "max_orders_per_day": 5})
    assert r.status_code == 422 and set(r.json()["detail"]["errors"]) == {"max_position_pct"}
    g = c.put("/api/risk/settings", json={"max_orders_per_day": 5, "allow_outside_hours": True}).json()["guardrails"]
    assert g["max_orders_per_day"] == 5 and g["allow_outside_hours"] is True and g["max_position_pct"] == 50.0
    assert c.get("/api/server/audit/verify").status_code == 200   # first account is admin
    bad = c.put("/api/risk/settings", json={"min_reward_risk": 50, "max_loss_per_trade": 0})
    assert set(bad.json()["detail"]["errors"]) == {"min_reward_risk", "max_loss_per_trade"}
    g = c.put("/api/risk/settings", json={"max_loss_per_trade": 250, "require_stop": True,
                                          "no_entry_before_close_min": 30}).json()["guardrails"]
    assert g["max_loss_per_trade"] == 250 and g["require_stop"] is True and g["no_entry_before_close_min"] == 30
    assert c.put("/api/risk/settings", json={"max_loss_per_trade": None}).json()["guardrails"]["max_loss_per_trade"] is None
