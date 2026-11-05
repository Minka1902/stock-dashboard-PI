"""The Alpaca broker (paper): the order payload, who owns the account, placing
and refusals, fills synced into the ledger (partial, full, bracket legs),
reconciling, cancels, the kill switch, cash-only, secrets, and the API."""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app import alpaca, config, db, execution, risk
from app.logging_config import RedactSecrets
from app.models import Order
from tests.conftest import authenticate

LIVE = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)      # Tue 11:00 New York
SECRET = "sk-test-SECRET-value-1234"


class FakeAlpaca:
    """Just enough of Alpaca's Trading API, with the requests it saw."""

    def __init__(self, cash="10000", positions=None, refuse=None):
        self.cash, self.positions, self.refuse = cash, positions or [], refuse
        self.orders, self.seen, self.n = {}, [], 0

    def __call__(self, req: httpx.Request):
        self.seen.append((req.method, req.url.path, req.headers.get("APCA-API-SECRET-KEY")))
        path, m = req.url.path, req.method
        if path == "/v2/account":
            return httpx.Response(200, json={"status": "ACTIVE", "cash": self.cash, "buying_power": "20000",
                                             "equity": self.cash, "trading_blocked": False, "account_blocked": False})
        if path == "/v2/account/configurations":
            return httpx.Response(200, json={"max_margin_multiplier": "2", "no_shorting": False})
        if path == "/v2/positions":
            return httpx.Response(200, json=self.positions)
        if path == "/v2/orders" and m == "POST":
            if self.refuse:
                return httpx.Response(403, json={"message": self.refuse})
            self.n += 1
            body = json.loads(req.content)
            o = {"id": f"a{self.n}", "client_order_id": body["client_order_id"], "status": "accepted",
                 "filled_qty": "0", "filled_avg_price": None, "symbol": body["symbol"], "side": body["side"],
                 "qty": body["qty"], "legs": None, "_body": body}
            self.orders[o["id"]] = o
            return httpx.Response(200, json=o)
        if path == "/v2/orders":
            return httpx.Response(200, json=list(self.orders.values()))
        oid = path.rsplit("/", 1)[1]
        if m == "DELETE":
            self.orders[oid]["status"] = "canceled"
            return httpx.Response(204)
        return httpx.Response(200, json=self.orders[oid])

    def fill(self, oid, qty, avg, status="partially_filled"):
        self.orders[oid].update(filled_qty=str(qty), filled_avg_price=str(avg), status=status,
                                filled_at="2026-10-27T15:01:00Z")


@pytest.fixture
def fake(monkeypatch):
    f = FakeAlpaca()
    monkeypatch.setattr(config, "ALPACA_KEY_ID", "PKTESTKEY")
    monkeypatch.setattr(config, "ALPACA_SECRET_KEY", SECRET)
    monkeypatch.setattr(config, "ALPACA_PAPER", True)
    monkeypatch.setattr(config, "ALPACA_OWNER", "")
    real = alpaca.client
    monkeypatch.setattr(alpaca, "client", lambda: real().__class__(
        base_url=alpaca.BASE["paper"], transport=httpx.MockTransport(f),
        headers={"APCA-API-KEY-ID": config.ALPACA_KEY_ID, "APCA-API-SECRET-KEY": config.ALPACA_SECRET_KEY}))
    execution._last_reconcile.clear()
    return f


@pytest.fixture
def owner(conn, fake):
    u = db.create_user(conn, "owner@x.co", "h", "t", is_admin=True)
    s = db.get_risk_settings(conn, u.id)
    db.upsert_risk_settings(conn, s.model_copy(update={"max_position_pct": 100}))
    execution.sync_broker(conn, LIVE)            # first sync imports Alpaca's cash
    return u


def _order(uid, **kw):
    return Order(**{**dict(user_id=uid, account="alpaca", ticker="AAPL", side="BUY", qty=10, currency="USD",
                           created_at="", reference_price=100.0), **kw})


# ---------- the payload and the owner ----------

def test_a_buy_with_a_plan_goes_as_a_bracket_in_alpacas_spelling():
    o = _order(1, id=7, ticker="BRK-B", type="limit", limit_price=400.0, plan_stop=380.0, plan_target=440.0)
    assert alpaca.payload(o) == {
        "symbol": "BRK.B", "qty": "10", "side": "buy", "type": "limit", "time_in_force": "day",
        "client_order_id": "sd-7", "limit_price": "400", "order_class": "bracket",
        "take_profit": {"limit_price": "440"}, "stop_loss": {"stop_price": "380"}}
    assert "order_class" not in alpaca.payload(_order(1, id=8, plan_stop=90.0))


def test_the_account_belongs_to_one_user(conn, fake, monkeypatch):
    a = db.create_user(conn, "a@x.co", "h", "t", is_admin=True)
    b = db.create_user(conn, "b@x.co", "h", "t")
    assert alpaca.owner(conn) == a.id
    monkeypatch.setattr(config, "ALPACA_OWNER", "B@x.co")
    assert alpaca.owner(conn) == b.id
    with pytest.raises(execution.OrderRejected, match="no broker is connected"):
        execution.submit(conn, _order(a.id), LIVE)
    monkeypatch.setattr(config, "ALPACA_KEY_ID", "")
    assert alpaca.owner(conn) is None


# ---------- placing ----------

def test_an_order_goes_to_alpaca_and_its_refusal_comes_back(conn, owner, fake):
    placed = execution.submit(conn, _order(owner.id, qty=5), LIVE)
    assert (placed.status, placed.broker_order_id) == ("submitted", "a1")
    assert fake.orders["a1"]["_body"]["client_order_id"] == f"sd-{placed.id}"
    fake.refuse = "insufficient buying power"
    refused = execution.submit(conn, _order(owner.id, qty=5), LIVE)
    assert refused.status == "rejected" and refused.reason == "Alpaca refused (403): insufficient buying power"


def test_live_mode_tase_and_margin_are_refused_before_alpaca(conn, owner, fake, monkeypatch):
    with pytest.raises(execution.OrderRejected, match="US listings only"):
        execution.submit(conn, _order(owner.id, ticker="TEVA.TA", currency="ILS"), LIVE)
    with pytest.raises(execution.OrderRejected, match="no margin here"):
        execution.submit(conn, _order(owner.id, qty=200), LIVE)
    monkeypatch.setattr(config, "ALPACA_PAPER", False)
    with pytest.raises(execution.OrderRejected, match="authenticator step"):
        execution.submit(conn, _order(owner.id), LIVE)
    assert not any(m == "POST" for m, *_ in fake.seen)


def test_the_first_order_reconciles_first_and_an_unreachable_alpaca_says_so(conn, fake, monkeypatch):
    u = db.create_user(conn, "first@x.co", "h", "t", is_admin=True)
    assert execution.submit(conn, _order(u.id, qty=1), LIVE).status == "submitted"     # cash imported first
    execution._last_reconcile.clear()

    def down(req):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(alpaca, "client", lambda: httpx.Client(base_url=alpaca.BASE["paper"],
                                                               transport=httpx.MockTransport(down)))
    with pytest.raises(execution.OrderRejected, match="can't reach Alpaca to check the account first"):
        execution.submit(conn, _order(u.id, qty=1), LIVE)


# ---------- fills, legs, reconcile ----------

def test_partial_then_full_fills_land_in_the_ledger_once_each(conn, owner, fake):
    o = execution.submit(conn, _order(owner.id), LIVE)
    fake.fill("a1", 4, 100.0)
    execution.sync_broker(conn, LIVE)
    fake.fill("a1", 10, 101.0, "filled")
    execution.sync_broker(conn, LIVE)
    execution.sync_broker(conn, LIVE)                     # nothing new: nothing written
    fills = [(t.qty, round(t.price, 4)) for t in db.get_transactions(conn, owner.id, account="alpaca")
             if t.side == "BUY"]
    assert sorted(fills) == [(4, 100.0), (6, 101.6667)]
    done = db.get_order(conn, o.id)
    assert (done.status, done.filled_qty, done.fill_price) == ("filled", 10, 101.0)
    assert db.get_portfolio(conn, owner.id, "alpaca")[0].shares == 10


def test_a_bracket_exit_becomes_an_order_and_a_sale(conn, owner, fake):
    execution.submit(conn, _order(owner.id, plan_stop=95.0, plan_target=110.0), LIVE)
    fake.fill("a1", 10, 100.0, "filled")
    fake.orders["a1"]["legs"] = [
        {"id": "leg-stop", "side": "sell", "qty": "10", "type": "stop", "stop_price": "95", "status": "filled",
         "time_in_force": "gtc", "filled_qty": "10", "filled_avg_price": "94.5", "filled_at": "2026-10-28T15:00:00Z"},
        {"id": "leg-tp", "side": "sell", "qty": "10", "type": "limit", "limit_price": "110", "status": "canceled",
         "time_in_force": "gtc", "filled_qty": "0"}]
    execution.sync_broker(conn, LIVE)
    exit_ = db.get_order_by_broker_id(conn, "leg-stop")
    assert (exit_.source, exit_.side, exit_.status) == ("bracket", "SELL", "filled")
    assert db.get_order_by_broker_id(conn, "leg-tp") is None
    sale = next(t for t in db.get_transactions(conn, owner.id, account="alpaca") if t.side == "SELL")
    assert sale.realized_pnl == pytest.approx(-55.0) and db.get_portfolio(conn, owner.id, "alpaca") == []


def test_reconcile_records_what_alpaca_holds_that_the_ledger_doesnt(conn, owner, fake):
    fake.positions = [{"symbol": "BRK.B", "qty": "3", "avg_entry_price": "410"}]
    fake.cash = "9000"
    out = execution.sync_broker(conn, LIVE + timedelta(hours=1))
    assert {d["ticker"] for d in out["reconcile"]["diffs"]} == {"BRK-B", "USD cash"}
    assert [(h.ticker, h.shares) for h in db.get_portfolio(conn, owner.id, "alpaca")] == [("BRK-B", 3)]
    assert db.get_cash(conn, owner.id, "alpaca")[0].balance == 9000
    assert any(e.kind == "broker_reconciled" for e in db.get_audit(conn, owner.id))


# ---------- cancels and the kill switch ----------

def test_cancel_and_the_kill_switch_leave_protective_legs_alone(conn, owner, fake):
    o = execution.submit(conn, _order(owner.id, qty=2), LIVE)
    assert execution.cancel(conn, owner.id, o.id).status == "cancelled"
    resting = execution.submit(conn, _order(owner.id, qty=2), LIVE)
    db.insert_order(conn, _order(owner.id, side="SELL", status="submitted", broker_order_id="leg-x",
                                 source="bracket", created_at="t"))
    fake.orders["leg-x"] = {"id": "leg-x", "status": "accepted"}
    out = risk.halt_trading(conn, owner.id, "test")
    assert resting.id in out["cancelled_orders"] and fake.orders["leg-x"]["status"] == "accepted"


# ---------- secrets ----------

def test_the_secret_rides_in_a_header_and_never_in_an_error(conn, owner, fake):
    assert all(s == SECRET for *_, s in fake.seen)
    fake.refuse = "forbidden"
    refused = execution.submit(conn, _order(owner.id, qty=1), LIVE)
    assert SECRET not in refused.reason
    assert SECRET not in RedactSecrets().scrub(f"boom {SECRET}")


# ---------- API ----------

@pytest.fixture
def client(tmp_path, monkeypatch, fake):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "alpaca.db"))
    import importlib

    from app import main as main_module, quotes
    importlib.reload(config)
    importlib.reload(main_module)
    for k, v in (("ALPACA_KEY_ID", "PKTESTKEY"), ("ALPACA_SECRET_KEY", SECRET), ("ALPACA_PAPER", True),
                 ("ALPACA_OWNER", "")):
        monkeypatch.setattr(config, k, v)
    monkeypatch.setattr(quotes, "get_quotes", lambda tickers, ttl_seconds=None: [])
    with TestClient(main_module.app) as c:
        authenticate(c)
        yield c, main_module


def test_api_status_order_and_sync(client, fake):
    c, m = client
    c.put("/api/risk/settings", json={"allow_outside_hours": True, "max_position_pct": 100})
    assert c.post("/api/broker/sync").json()["reconcile"]["diffs"][0]["alpaca"] == 10000.0
    s = c.get("/api/broker").json()
    assert s["yours"] and s["mode"] == "paper" and s["summary"]["cash"] == 10000.0
    assert {ch["text"]: ch["ok"] for ch in s["checks"]}["paper account"] is True
    assert any(not ch["ok"] and "margin" in ch["text"] for ch in s["checks"])
    placed = c.post("/api/broker/orders", json={"ticker": "aapl", "side": "BUY", "qty": 1, "type": "limit",
                                                "limit_price": 100}).json()
    assert placed["broker_order_id"] == "a1" and placed["account"] == "alpaca"
    assert c.post(f"/api/broker/orders/{placed['id']}/cancel").json()["status"] == "cancelled"
