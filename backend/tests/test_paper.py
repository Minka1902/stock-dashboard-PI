"""Paper trading: execution.submit, the paper broker's fills, the ledger they
write, cancel races, and the paper API."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import config, db, execution, ledger
from app.models import LiveQuote, Order
from tests.conftest import authenticate

LIVE = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)      # Tue 11:00 New York
CLOSED = datetime(2026, 10, 27, 3, 0, tzinfo=timezone.utc)
NEXT_DAY = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)


def _q(price, at=LIVE, ticker="AAPL"):
    return lambda ts: [LiveQuote(ticker=t, price=price, regular_price=price, change_pct=0.0,
                                 previous_close=price, market_state="LIVE",
                                 fetched_at=at.isoformat(timespec="seconds"), currency="USD") for t in ts]


@pytest.fixture
def user(conn):
    u = db.create_user(conn, "paper@x.co", "h", "t")
    execution.reset_paper(conn, u.id, 10_000, "USD", CLOSED)
    return u


def _order(user_id, **kw):
    base = dict(user_id=user_id, account="paper", ticker="AAPL", side="BUY", qty=10, currency="USD",
                created_at="", reference_price=100.0)
    return Order(**{**base, **kw})


def _loosen(conn, user_id, **limits):
    s = db.get_risk_settings(conn, user_id)
    db.upsert_risk_settings(conn, s.model_copy(update={
        "allow_outside_hours": True, "max_order_notional": 1e9, "max_notional_per_day": 1e9,
        "max_position_pct": 1e6, **limits}))


def _cash(conn, user_id):
    return {c.currency: c.balance for c in db.get_cash(conn, user_id, "paper")}


# ---------- fills ----------

def test_a_market_order_fills_at_the_quote_plus_costs(conn, user):
    o = execution.submit(conn, _order(user.id), LIVE, _q(100.0))
    assert o.status == "filled" and o.fill_price == 100.1 and o.slippage_bps == 10.0
    assert [(h.ticker, h.shares, h.avg_cost) for h in db.get_portfolio(conn, user.id, "paper")] == [("AAPL", 10, 100.1)]
    assert _cash(conn, user.id) == {"USD": 10_000 - 1001.0}
    tx = db.get_transactions(conn, user.id, account="paper")[0]
    assert tx.order_id == o.id and tx.source == "paper_fill"


def test_a_market_order_placed_while_closed_waits_for_the_next_live_quote(conn, user):
    _loosen(conn, user.id)
    o = execution.submit(conn, _order(user.id), CLOSED, _q(100.0))
    assert o.status == "submitted"
    assert execution.poll(conn, CLOSED, _q(100.0)) == 0
    assert execution.poll(conn, LIVE, _q(101.0)) == 1
    assert db.get_order(conn, o.id).fill_price == pytest.approx(101.101)


def test_a_limit_buy_never_fills_above_its_limit(conn, user):
    o = execution.submit(conn, _order(user.id, type="limit", limit_price=99.0, tif="gtc"), LIVE, _q(100.0))
    assert o.status == "submitted"
    assert execution.poll(conn, LIVE, _q(98.95)) == 1
    assert db.get_order(conn, o.id).fill_price == 99.0          # 98.95 + 10 bps = 99.049 → capped


def test_a_stop_sell_triggers_and_a_gap_fills_at_the_gapped_price(conn, user):
    execution.submit(conn, _order(user.id), LIVE, _q(100.0))
    o = execution.submit(conn, _order(user.id, side="SELL", type="stop", stop_price=95.0, tif="gtc"),
                         LIVE, _q(97.0))
    assert o.status == "submitted"
    assert execution.poll(conn, LIVE, _q(90.0)) == 1
    filled = db.get_order(conn, o.id)
    assert filled.fill_price == pytest.approx(89.91) and db.get_portfolio(conn, user.id, "paper") == []


def test_a_day_order_expires_after_its_session(conn, user):
    o = execution.submit(conn, _order(user.id, type="limit", limit_price=50.0), LIVE, _q(100.0))
    execution.poll(conn, NEXT_DAY, _q(40.0, NEXT_DAY))
    gone = db.get_order(conn, o.id)
    assert gone.status == "cancelled" and gone.reason.startswith("expired")


def test_a_day_order_placed_after_the_close_is_for_the_next_session(conn, user):
    monday_night = datetime(2026, 10, 26, 23, 0, tzinfo=timezone.utc)   # 19:00 New York
    assert str(execution.session_for("AAPL", monday_night)) == "2026-10-27"
    assert str(execution.session_for("AAPL", LIVE)) == "2026-10-27"


def test_the_ledger_replays_to_the_paper_tables(conn, user):
    execution.submit(conn, _order(user.id), LIVE, _q(100.0))
    execution.submit(conn, _order(user.id, side="SELL", qty=4), LIVE, _q(110.0))
    positions, cash = ledger.replay(db.get_transactions(conn, user.id, account="paper", limit=1000))
    held = db.get_portfolio(conn, user.id, "paper")[0]
    assert positions[("paper", "AAPL")].shares == held.shares == 6
    assert cash[("paper", "USD")] == pytest.approx(_cash(conn, user.id)["USD"])


# ---------- refusals and races ----------

def test_not_enough_paper_cash_is_refused_up_front_and_again_at_the_fill(conn, user):
    _loosen(conn, user.id)
    with pytest.raises(execution.OrderRejected, match="needs about 100,100.00 USD; the account has 10,000.00"):
        execution.submit(conn, _order(user.id, qty=1000), LIVE, _q(100.0))
    resting = execution.submit(conn, _order(user.id, qty=50, type="limit", limit_price=99.0, tif="gtc"), LIVE, _q(100.0))
    ledger.record(conn, user.id, ticker="USD", side="WITHDRAW", qty=9_000, currency_code="USD", account="paper")
    execution.poll(conn, LIVE, _q(98.0))
    late = db.get_order(conn, resting.id)
    assert late.status == "rejected" and "not enough USD cash" in late.reason
    assert db.get_portfolio(conn, user.id, "paper") == []


def test_bad_tickets_name_their_fields(conn, user):
    with pytest.raises(execution.OrderRejected) as e:
        execution.submit(conn, _order(user.id, qty=0, type="limit"), LIVE, _q(100.0))
    assert set(e.value.fields) == {"qty", "limit_price"}
    with pytest.raises(execution.OrderRejected, match="no live broker"):
        execution.submit(conn, _order(user.id, account="live"), LIVE, _q(100.0))


def test_a_filled_order_cannot_be_cancelled(conn, user):
    o = execution.submit(conn, _order(user.id), LIVE, _q(100.0))
    with pytest.raises(execution.OrderRejected, match="filled"):
        execution.cancel(conn, user.id, o.id)


def test_a_cancel_that_lands_first_wins_over_the_fill(conn, user):
    o = execution.submit(conn, _order(user.id, type="limit", limit_price=99.0, tif="gtc"), LIVE, _q(100.0))
    execution.cancel(conn, user.id, o.id)
    assert execution.poll(conn, LIVE, _q(90.0)) == 0
    tx = ledger.build(user.id, ticker="AAPL", side="BUY", qty=10, price=90.0, currency_code="USD",
                      account="paper")
    assert db.fill_order(conn, o, tx, None) is None              # the late fill finds it cancelled
    assert db.get_portfolio(conn, user.id, "paper") == []


def test_someone_elses_order_is_not_yours_to_cancel(conn, user):
    o = execution.submit(conn, _order(user.id, type="limit", limit_price=99.0), LIVE, _q(100.0))
    with pytest.raises(execution.OrderRejected, match="no such order"):
        execution.cancel(conn, user.id + 1, o.id)


# ---------- reset & slippage ----------

def test_reset_cancels_orders_clears_positions_and_redeposits(conn, user):
    execution.submit(conn, _order(user.id), LIVE, _q(100.0))
    resting = execution.submit(conn, _order(user.id, type="limit", limit_price=50.0, tif="gtc"), LIVE, _q(100.0))
    execution.reset_paper(conn, user.id, 5_000, "USD", NEXT_DAY)
    assert db.get_order(conn, resting.id).status == "cancelled"
    assert db.get_portfolio(conn, user.id, "paper") == [] and _cash(conn, user.id) == {"USD": 5_000}


def test_slippage_is_measured_but_paper_never_calibrates_costs(conn, user):
    execution.submit(conn, _order(user.id), LIVE, _q(100.0))
    s = execution.median_slippage(conn, user.id, "paper")
    assert s == {"median_bps": 10.0, "n": 1, "calibrates_costs": False}
    assert execution.median_slippage(conn, user.id, "live")["calibrates_costs"] is True


# ---------- API ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "paper.db"))
    import importlib

    from app import config as cfg, main as main_module, quotes
    importlib.reload(cfg)
    importlib.reload(main_module)
    monkeypatch.setattr(quotes, "get_quotes", lambda tickers, ttl_seconds=None: [])
    with TestClient(main_module.app) as c:
        authenticate(c)
        yield c


def test_api_fund_order_cancel(client):
    s = client.get("/api/paper").json()
    assert s["funded"] is False and s["modes"][1] == {"value": "live", "available": False,
                                                       "reason": "no broker connected"}
    client.put("/api/risk/settings", json={"allow_outside_hours": True})
    s = client.post("/api/paper/reset", json={}).json()
    assert s["cash"][0]["balance"] == config.PAPER_START_CASH
    r = client.post("/api/paper/orders", json={"ticker": "aapl", "side": "buy", "qty": 5, "type": "limit",
                                               "limit_price": 90, "tif": "gtc"})
    assert r.status_code == 200 and r.json()["status"] == "submitted"     # no live quote here: it rests
    oid = r.json()["id"]
    assert client.post(f"/api/paper/orders/{oid}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/paper/orders/{oid}/cancel").status_code == 409
    bad = client.post("/api/paper/orders", json={"ticker": "AAPL", "side": "BUY", "qty": -1})
    assert bad.status_code == 422 and "qty" in bad.json()["detail"]["errors"]
    assert client.post("/api/paper/reset", json={"currency": "EUR"}).status_code == 422


def test_api_ticket_defaults_without_data_say_so(client):
    d = client.get("/api/paper/ticket-defaults?ticker=AAPL").json()
    assert d["price"] is None and d["qty"] is None and d["currency"] == "USD"
