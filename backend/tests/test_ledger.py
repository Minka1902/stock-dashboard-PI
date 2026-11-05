"""Transactions ledger, cash, the pre-ledger migration and equity snapshots."""
import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import db, equity, ingest, ledger
from app.models import CashBalance, Holding, LiveQuote
from tests.conftest import authenticate, buy


def _record(conn, side, ticker, qty, price=1.0, ccy="USD", **kw):
    return ledger.record(conn, 1, ticker=ticker, side=side, qty=qty, price=price,
                         currency_code=ccy, **kw)


def _materialized(conn, user_id=1):
    positions = {
        (r["account"], r["ticker"]): (r["shares"], r["avg_cost"], r["currency"])
        for r in conn.execute("SELECT * FROM portfolio WHERE user_id = ?", (user_id,))
    }
    cash = {
        (r["account"], r["currency"]): r["balance"]
        for r in conn.execute("SELECT * FROM cash_balances WHERE user_id = ?", (user_id,))
        if r["balance"]
    }
    return positions, cash


def _replayed(conn, user_id=1):
    positions, cash = ledger.replay(db.get_transactions(conn, user_id, limit=10_000))
    return (
        {k: (p.shares, p.avg_cost, p.currency) for k, p in positions.items()},
        {k: v for k, v in cash.items() if abs(v) > ledger.EPS},
    )


def _approx(d):
    return {k: tuple(round(x, 6) if isinstance(x, float) else x for x in v)
            if isinstance(v, tuple) else round(v, 6) for k, v in d.items()}


# ---------- pure arithmetic ----------

def test_partial_sell_keeps_cost_and_realizes_net_of_fees(conn):
    buy(conn, 1, "AAPL", 10, 100.0)
    tx = _record(conn, "SELL", "AAPL", 4, 130.0, fees=2.0)
    assert tx.realized_pnl == pytest.approx(4 * 30 - 2)
    aapl = db.get_portfolio(conn, 1)[0]
    assert (aapl.shares, aapl.avg_cost) == (6, 100.0)


def test_selling_everything_closes_the_position(conn):
    buy(conn, 1, "AAPL", 10, 100.0)
    _record(conn, "SELL", "AAPL", 10, 90.0)
    assert db.get_portfolio(conn, 1) == []


def test_oversell_is_refused_and_writes_nothing(conn):
    buy(conn, 1, "AAPL", 10, 100.0)
    before = len(db.get_transactions(conn, 1))
    with pytest.raises(ledger.LedgerError, match="only 10 held"):
        _record(conn, "SELL", "AAPL", 11, 100.0)
    assert len(db.get_transactions(conn, 1)) == before
    assert db.get_portfolio(conn, 1)[0].shares == 10


def test_selling_an_unheld_ticker_is_refused(conn):
    with pytest.raises(ledger.LedgerError, match="not held"):
        _record(conn, "SELL", "MSFT", 1, 10.0)


def test_trade_in_a_currency_other_than_the_positions_is_refused(conn):
    buy(conn, 1, "TEVA.TA", 10, 50.0, currency="ILS")
    with pytest.raises(ledger.LedgerError, match="held in ILS"):
        _record(conn, "BUY", "TEVA.TA", 1, 15.0, ccy="USD")


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf")])
def test_non_positive_or_non_finite_quantities_are_refused(conn, bad):
    with pytest.raises(ledger.LedgerError):
        _record(conn, "BUY", "AAPL", bad, 10.0)


def test_agorot_are_normalized_to_shekels(conn):
    tx = _record(conn, "BUY", "TEVA.TA", 10, 12050, ccy="ILA")
    assert (tx.price, tx.currency) == (120.5, "ILS")
    teva = db.get_portfolio(conn, 1)[0]
    assert (teva.avg_cost, teva.currency) == (120.5, "ILS")


# ---------- cash ----------

def test_deposit_withdraw_and_funded_trades_move_cash(conn):
    _record(conn, "DEPOSIT", "USD", 5000)
    buy_tx = _record(conn, "BUY", "AAPL", 10, 200.0, fees=1.0, funded=True)
    assert buy_tx.cash_delta == -2001.0 and buy_tx.flow == 0
    _record(conn, "SELL", "AAPL", 5, 220.0, fees=1.0, funded=True)
    _record(conn, "WITHDRAW", "USD", 1000)
    assert db.get_cash(conn, 1) == [CashBalance(currency="USD", balance=5000 - 2001 + 1099 - 1000)]


def test_unfunded_buy_is_an_external_flow_and_leaves_cash_alone(conn):
    tx = _record(conn, "BUY", "AAPL", 10, 200.0, fees=1.0)
    assert (tx.cash_delta, tx.flow) == (0, 2001.0)
    assert db.get_cash(conn, 1) == []


def test_cash_can_never_go_negative(conn):
    _record(conn, "DEPOSIT", "USD", 100)
    with pytest.raises(ledger.LedgerError, match="not enough USD cash"):
        _record(conn, "BUY", "AAPL", 1, 200.0, funded=True)
    with pytest.raises(ledger.LedgerError, match="not enough USD cash"):
        _record(conn, "WITHDRAW", "USD", 101)
    assert db.get_cash(conn, 1)[0].balance == 100
    assert db.get_portfolio(conn, 1) == []


def test_cash_in_agorot_is_stored_in_shekels(conn):
    _record(conn, "DEPOSIT", "ILA", 10_000, ccy="ILA")
    assert db.get_cash(conn, 1) == [CashBalance(currency="ILS", balance=100.0)]


# ---------- ledger == materialized tables ----------

def test_replaying_the_ledger_reproduces_portfolio_and_cash(conn):
    _record(conn, "DEPOSIT", "USD", 10_000)
    _record(conn, "DEPOSIT", "ILS", 2_000, ccy="ILS")
    _record(conn, "BUY", "AAPL", 10, 150.0, fees=1.0, funded=True)
    _record(conn, "BUY", "AAPL", 5, 180.0, funded=True)
    _record(conn, "BUY", "MSFT", 3, 400.0)
    _record(conn, "BUY", "TEVA.TA", 20, 5000, ccy="ILA", funded=True)
    _record(conn, "SELL", "AAPL", 7, 170.0, fees=1.5, funded=True)
    _record(conn, "ADJUST", "MSFT", 4, 390.0)
    _record(conn, "SELL", "TEVA.TA", 20, 55.0, ccy="ILS")
    _record(conn, "WITHDRAW", "USD", 500)
    buy(conn, 1, "NVDA", 2, 900.0, account="paper")
    assert _approx(_replayed(conn)[0]) == _approx(_materialized(conn)[0])
    assert _approx(_replayed(conn)[1]) == _approx(_materialized(conn)[1])


# ---------- migration ----------

def test_ledger_seed_runs_once_and_matches_the_positions(conn):
    conn.execute(
        "INSERT INTO portfolio (user_id, account, ticker, shares, avg_cost, added_at, currency) "
        "VALUES (1, 'manual', 'AAPL', 10, 150, '2025-05-01T00:00:00+00:00', 'USD'), "
        "       (1, 'manual', 'TEVA.TA', 20, 50, '2025-06-01T00:00:00+00:00', 'ILS')")
    conn.execute("DELETE FROM data_migrations WHERE name = 'ledger_seed_2026_10'")
    conn.commit()
    db.init_schema(conn)
    db.init_schema(conn)
    rows = db.get_transactions(conn, 1)
    assert sorted((t.ticker, t.side, t.qty, t.price, t.source, t.cash_delta) for t in rows) == [
        ("AAPL", "BUY", 10, 150, "migration", 0), ("TEVA.TA", "BUY", 20, 50, "migration", 0)]
    assert _approx(_replayed(conn)[0]) == _approx(_materialized(conn)[0])


def test_pre_account_portfolio_is_rebuilt_with_the_account_key(tmp_path):
    path = str(tmp_path / "legacy.db")
    raw = sqlite3.connect(path)
    raw.executescript(
        """
        CREATE TABLE portfolio (
            user_id INTEGER NOT NULL DEFAULT 0, ticker TEXT NOT NULL, shares REAL NOT NULL,
            avg_cost REAL NOT NULL, added_at TEXT NOT NULL, category TEXT,
            currency TEXT NOT NULL DEFAULT 'USD', PRIMARY KEY (user_id, ticker));
        INSERT INTO portfolio VALUES (3, 'LMT', 2, 400, '2025-01-01T00:00:00+00:00', 'Defense', 'USD');
        """
    )
    raw.commit()
    raw.close()
    conn = db.connect(path)
    db.init_schema(conn)
    db.init_schema(conn)
    pk = [r["name"] for r in conn.execute("PRAGMA table_info(portfolio)") if r["pk"]]
    assert pk == ["user_id", "account", "ticker"]
    assert db.get_portfolio(conn, 3) == [Holding(
        ticker="LMT", shares=2, avg_cost=400, added_at="2025-01-01T00:00:00+00:00")]
    assert db.get_holding_categories(conn, 3) == {"LMT": "Defense"}
    assert [(t.ticker, t.side, t.source) for t in db.get_transactions(conn, 3)] == [
        ("LMT", "BUY", "migration")]
    conn.close()


def test_accounts_are_separate_positions(conn):
    buy(conn, 1, "AAPL", 1, 100.0)
    buy(conn, 1, "AAPL", 5, 120.0, account="paper")
    assert db.get_portfolio(conn, 1)[0].shares == 1
    assert db.get_portfolio(conn, 1, "paper")[0].shares == 5
    assert db.get_all_portfolio_tickers(conn) == ["AAPL"]


# ---------- equity snapshots ----------

def _quote(ticker, price, ccy="USD"):
    return LiveQuote(ticker=ticker, price=price, regular_price=price, currency=ccy,
                     change_pct=0.0, previous_close=price, market_state="CLOSED",
                     fetched_at="t")


def test_value_account_converts_positions_cash_and_flows_to_base():
    values, missing = equity.value_account(
        [Holding(ticker="TEVA.TA", shares=10, avg_cost=40, added_at="t", currency="ILS")],
        [CashBalance(currency="USD", balance=100)],
        {"ILS": 400.0},
        lambda t: _quote(t, 50.0, "ILS"),
        lambda f, t: {("ILS", "USD"): 0.25, ("USD", "USD"): 1.0}.get((f, t)),
        "USD",
    )
    assert missing == [] and values == (125.0, 100.0, 100.0)


def test_value_account_refuses_a_partial_value():
    values, missing = equity.value_account(
        [Holding(ticker="TEVA.TA", shares=10, avg_cost=40, added_at="t", currency="ILS"),
         Holding(ticker="AAPL", shares=1, avg_cost=100, added_at="t")],
        [], {}, lambda t: _quote(t, 50.0, "ILS") if t == "TEVA.TA" else None,
        lambda f, t: None if f == "ILS" else 1.0, "USD",
    )
    assert values is None
    assert missing == ["TEVA.TA: no ILS->USD rate", "AAPL: no price"]


def test_snapshot_skips_accounts_with_missing_fx_and_flags_the_run(conn):
    for email in ("a@x.co", "b@x.co"):
        db.create_user(conn, email, "h", "t")
    buy(conn, 1, "AAPL", 10, 100.0)
    buy(conn, 2, "TEVA.TA", 10, 40.0, currency="ILS")
    now = datetime(2026, 10, 27, 21, 0, tzinfo=timezone.utc)  # Tue 17:00 ET
    out = equity.snapshot_all(
        conn, now,
        quote_getter=lambda ts: [_quote("AAPL", 110.0), _quote("TEVA.TA", 45.0, "ILS")],
        rate_fn=lambda f, t: 1.0 if f == t else None)
    assert isinstance(out, ingest.FetchResult) and "user 2/manual" in out.warning
    assert [(s.user_id, s.date, s.value_base) for s in out] == [(1, "2026-10-27", 1100.0)]


def test_snapshot_flows_cover_only_the_ledger_since_the_previous_day(conn):
    db.create_user(conn, "a@x.co", "h", "t")
    rate = lambda f, t: 1.0  # noqa: E731
    first = datetime(2026, 10, 26, 21, 0, tzinfo=timezone.utc)
    buy(conn, 1, "AAPL", 10, 100.0, executed_at="2026-10-26T15:00:00+00:00")
    snaps = equity.snapshot_all(conn, first, lambda ts: [_quote("AAPL", 100.0)], rate)
    db.upsert_equity_snapshots(conn, snaps)
    _record(conn, "DEPOSIT", "USD", 500, executed_at="2026-10-27T15:00:00+00:00")
    second = datetime(2026, 10, 27, 21, 0, tzinfo=timezone.utc)
    snaps = equity.snapshot_all(conn, second, lambda ts: [_quote("AAPL", 105.0)], rate)
    db.upsert_equity_snapshots(conn, snaps)
    rerun = equity.snapshot_all(conn, second, lambda ts: [_quote("AAPL", 105.0)], rate)
    assert [(s.date, s.value_base, s.flow_base) for s in db.get_equity_snapshots(conn, 1)] == [
        ("2026-10-26", 1000.0, 1000.0), ("2026-10-27", 1550.0, 500.0)]
    assert rerun[0].flow_base == 500.0  # a same-day rerun sees the same window


def test_weekend_runs_belong_to_fridays_close(conn):
    db.create_user(conn, "a@x.co", "h", "t")
    buy(conn, 1, "AAPL", 1, 100.0)
    saturday = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
    out = equity.snapshot_all(conn, saturday, lambda ts: [_quote("AAPL", 1.0)], lambda f, t: 1.0)
    assert out[0].date == "2026-10-30"


# ---------- API ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "ledger.db"))
    import importlib

    from app import config, main as main_module
    importlib.reload(config)
    importlib.reload(main_module)
    monkeypatch.setattr(main_module, "_ticker_currency", lambda t: None)
    with TestClient(main_module.app) as c:
        authenticate(c)
        yield c


def test_api_buy_from_cash_sell_to_cash_and_history(client):
    assert client.post("/api/portfolio/cash", json={
        "currency": "USD", "amount": 3000, "side": "deposit"}).json() == [
        {"currency": "USD", "balance": 3000.0}]
    r = client.post("/api/portfolio", json={
        "ticker": "aapl", "shares": 10, "avg_cost": 200, "fees": 1, "pay_from_cash": True})
    assert r.status_code == 200 and r.json()[0]["shares"] == 10
    r = client.post("/api/portfolio/AAPL/sell", json={
        "shares": 4, "price": 250, "to_cash": True})
    assert r.status_code == 200
    assert r.json()["realized_pnl"] == pytest.approx(200.0)
    assert client.get("/api/portfolio/cash").json() == [{"currency": "USD", "balance": 1999.0}]
    sides = [t["side"] for t in client.get("/api/portfolio/transactions").json()]
    assert sides == ["SELL", "BUY", "DEPOSIT"]


def test_api_refuses_what_the_ledger_refuses(client):
    r = client.post("/api/portfolio", json={
        "ticker": "AAPL", "shares": 1, "avg_cost": 200, "pay_from_cash": True})
    assert r.status_code == 400 and "not enough USD cash" in r.json()["detail"]
    client.post("/api/portfolio", json={"ticker": "AAPL", "shares": 1, "avg_cost": 200})
    r = client.post("/api/portfolio/AAPL/sell", json={"shares": 2, "price": 1})
    assert r.status_code == 400 and "only 1 held" in r.json()["detail"]
    assert client.post("/api/portfolio/MSFT/sell", json={"shares": 1, "price": 1}).status_code == 404


def test_api_edit_and_remove_are_adjustments(client):
    client.post("/api/portfolio", json={"ticker": "AAPL", "shares": 2, "avg_cost": 100})
    client.put("/api/portfolio/AAPL", json={"shares": 3, "avg_cost": 90})
    client.delete("/api/portfolio/AAPL")
    rows = client.get("/api/portfolio/transactions?ticker=AAPL").json()
    assert [(t["side"], t["qty"], t["realized_pnl"]) for t in rows] == [
        ("ADJUST", 0, None), ("ADJUST", 3, None), ("BUY", 2, None)]
    assert client.get("/api/portfolio").json() == []


def test_api_set_cash_records_the_gap(client):
    client.put("/api/portfolio/cash/ILS", json={"balance": 500})
    client.put("/api/portfolio/cash/ILS", json={"balance": 200})
    rows = client.get("/api/portfolio/transactions").json()
    assert [(t["side"], t["qty"], t["source"]) for t in rows] == [
        ("WITHDRAW", 300, "adjust"), ("DEPOSIT", 500, "adjust")]


def test_api_accounts(client):
    assert client.get("/api/portfolio?account=paper").json() == []
    assert client.get("/api/portfolio?account=live").status_code == 409
    assert client.get("/api/portfolio?account=bogus").status_code == 400
    r = client.post("/api/portfolio/cash", json={
        "currency": "ILS", "amount": 1000, "side": "deposit", "account": "paper"})
    assert r.status_code == 200
    assert client.get("/api/portfolio/cash?account=paper").json() == [
        {"currency": "ILS", "balance": 1000.0}]
    assert client.get("/api/portfolio/cash").json() == []


def test_api_rejects_future_execution_times(client):
    r = client.post("/api/portfolio", json={
        "ticker": "AAPL", "shares": 1, "avg_cost": 1, "executed_at": "2999-01-01"})
    assert r.status_code == 400
