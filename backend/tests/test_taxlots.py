"""Tax lots from the ledger, sale checks, wash sales, harvesting, and the
rebalance plan (STC-85)."""
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import config, db, ledger, rebalance, taxlots
from app.models import OHLCSeries, Transaction
from tests.conftest import authenticate


def _tx(side, ticker, qty, price, day, fees=0.0, account="manual"):
    return Transaction(user_id=1, account=account, ticker=ticker, side=side, qty=qty, price=price, fees=fees,
                       currency="USD", executed_at=f"{day}T15:00:00+00:00")


LEDGER = [_tx("BUY", "KO", 10, 60.0, "2025-01-02", fees=1.0), _tx("BUY", "KO", 5, 70.0, "2026-09-20"),
          _tx("SELL", "KO", 8, 72.0, "2026-10-01")]


# ---------- lots ----------

def test_sales_consume_lots_first_in_first_out_and_realize_by_term():
    lots, realized = taxlots.build(LEDGER)
    assert [(lot.id, lot.qty, lot.per_share) for lot in lots["KO"]] == [("KO-1", 2, 60.1), ("KO-2", 5, 70.0)]
    assert realized == [{"ticker": "KO", "currency": "USD", "lot": "KO-1", "acquired": "2025-01-02", "sold": "2026-10-01", "qty": 8,
                         "proceeds": 576.0, "basis": pytest.approx(480.8), "term": "long", "exact": True}]
    assert taxlots.realized_by_year(realized, 2026) == {"year": 2026, "by_currency": {"USD": {"short": 0, "long": 95.2}},
                                                       "sales": 1, "approximate": False}


def test_an_edit_replaces_the_lots_with_one_marked_approximate():
    lots, _ = taxlots.build([*LEDGER, _tx("ADJUST", "KO", 12, 65.0, "2026-10-02")])
    assert [(lot.qty, lot.per_share, lot.acquired, lot.exact) for lot in lots["KO"]] == [
        (12, 65.0, date(2026, 10, 2), False)]


def test_long_term_starts_the_day_after_the_first_anniversary():
    bought = date(2025, 3, 10)
    assert taxlots.term(bought, date(2026, 3, 10)) == "short" and taxlots.term(bought, date(2026, 3, 11)) == "long"
    assert taxlots.long_term_from(date(2024, 2, 29)) == date(2025, 2, 28)


def test_lot_methods_and_their_refusals():
    lots, _ = taxlots.build(LEDGER)
    assert [lot.id for lot, _ in taxlots.pick(lots["KO"], 3, "hifo")] == ["KO-2"]
    assert [(lot.id, q) for lot, q in taxlots.pick(lots["KO"], 6, "fifo")] == [("KO-1", 2), ("KO-2", 4)]
    with pytest.raises(taxlots.TaxError, match="name the lots"):
        taxlots.pick(lots["KO"], 1, "specific")
    with pytest.raises(taxlots.TaxError, match="only 7 are held"):
        taxlots.pick(lots["KO"], 8)


def test_a_sale_splits_its_gain_by_term():
    lots, _ = taxlots.build(LEDGER)
    out = taxlots.simulate_sale(lots["KO"], 7, 66.0, date(2026, 10, 15))
    assert (out["long"], out["short"], out["gain"]) == (11.8, -20.0, -8.2)
    assert [u["term"] for u in out["lots"]] == ["long", "short"]


# ---------- wash sales and harvesting ----------

def test_a_loss_with_a_recent_buy_in_the_family_is_partly_disallowed():
    lots, _ = taxlots.build([_tx("BUY", "GOOGL", 10, 200.0, "2026-06-01"), _tx("BUY", "GOOG", 4, 150.0, "2026-10-10")])
    cands = taxlots.harvest(lots, {"GOOGL": 150.0, "GOOG": 150.0}, date(2026, 10, 20), min_loss=100)
    googl = cands[0]
    assert googl["ticker"] == "GOOGL" and googl["loss"] == -500.0
    assert googl["wash"] == [{"ticker": "GOOG", "lot": "GOOG-1", "acquired": "2026-10-10", "qty": 4}]
    assert googl["disallowed"] == 200.0 and googl["rebuy_on_or_after"] == "2026-11-20"


def test_harvest_skips_small_losses_and_winners():
    lots, _ = taxlots.build(LEDGER)
    assert taxlots.harvest(lots, {"KO": 69.0}, date(2026, 10, 20), min_loss=100) == []


# ---------- rebalance ----------

USD = {"USD": 1.0}


def test_rebalance_trades_only_outside_the_band_sells_first_and_leaves_untracked_alone():
    out = rebalance.plan({"AAPL": 10, "MSFT": 10, "OLD": 1},
                         {"AAPL": 100.0, "MSFT": 50.0, "OLD": 20.0, "NVDA": 100.0},
                         USD, {t: "USD" for t in ("AAPL", "MSFT", "OLD", "NVDA")}, 480.0,
                         {"AAPL": 30, "MSFT": 30, "NVDA": 20}, band=5, min_trade=10)
    assert out["account_value"] == 2000
    assert [(o["side"], o["ticker"], o["qty"]) for o in out["orders"]] == [("SELL", "AAPL", 4), ("BUY", "NVDA", 4)]
    assert next(r for r in out["rows"] if r["ticker"] == "MSFT")["action"] == "hold"     # -5 points: on the band
    old = next(r for r in out["rows"] if r["ticker"] == "OLD")
    assert old["action"] == "untracked, left alone" and old["target_pct"] is None


def test_buys_scale_down_to_the_cash_there_is():
    out = rebalance.plan({}, {"AAPL": 100.0, "MSFT": 100.0}, USD, {"AAPL": "USD", "MSFT": "USD"}, 1000.0,
                         {"AAPL": 80, "MSFT": 80}, band=5, min_trade=10)
    assert [o["qty"] for o in out["orders"]] == [5, 5] and "scaled to 62%" in out["notes"][0]


def test_a_foreign_listing_is_valued_in_the_base_currency():
    out = rebalance.plan({"TEVA.TA": 100}, {"TEVA.TA": 60.0}, {"ILS": 0.25, "USD": 1.0}, {"TEVA.TA": "ILS"},
                         0.0, {"TEVA.TA": 50}, band=5, min_trade=10)
    assert out["account_value"] == 1500 and out["orders"] == [
        {"ticker": "TEVA.TA", "side": "SELL", "qty": 50, "value": 750.0,
         "why": "drifted +50.0 points from its 50% target (band 5)"}]


def test_no_price_is_an_error_not_a_guess():
    with pytest.raises(ValueError, match="no price or exchange rate for NVDA"):
        rebalance.plan({}, {}, USD, {"NVDA": "USD"}, 100.0, {"NVDA": 10})


# ---------- API ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "lots.db"))
    import importlib

    from app import main as main_module, quotes
    importlib.reload(config)
    importlib.reload(main_module)
    monkeypatch.setattr(quotes, "get_quotes", lambda tickers, ttl_seconds=None: [])
    with TestClient(main_module.app) as c:
        authenticate(c)
        yield c, main_module


def _close(conn, ticker, price):
    db.upsert_ohlc(conn, [OHLCSeries(ticker=ticker, interval="daily", fetched_at="t", bars_json=json.dumps(
        [{"date": "2026-10-30", "open": price, "high": price, "low": price, "close": price, "volume": 1}]))])


def test_api_lots_sale_targets_and_rebalance(client):
    c, m = client
    uid = c.get("/api/auth/me").json()["id"]
    today = date.today()
    ledger.record(m.conn, uid, ticker="KO", side="BUY", qty=10, price=80.0, currency_code="USD",
                  executed_at=f"{today.replace(year=today.year - 2).isoformat()}T15:00:00+00:00")
    ledger.record(m.conn, uid, ticker="KO", side="BUY", qty=2, price=65.0, currency_code="USD",
                  executed_at=f"{today.isoformat()}T15:00:00+00:00")
    _close(m.conn, "KO", 70.0)
    view = c.get("/api/portfolio/lots?account=manual&min_loss=50").json()
    ko = view["positions"][0]
    assert [lot["term"] for lot in ko["lots"]] == ["long", "short"] and ko["price_source"] == "the 2026-10-30 close"
    assert view["harvest"][0]["wash"][0]["lot"] == "KO-2" and view["wash_scope"] == "your real accounts"
    sale = c.post("/api/portfolio/lots/sale", json={"ticker": "KO", "qty": 10, "method": "fifo"}).json()
    assert sale["gain"] == -100.0 and sale["disallowed"] == 20.0 and sale["rebuy_on_or_after"]
    bad = c.post("/api/portfolio/lots/sale", json={"ticker": "KO", "qty": 99})
    assert bad.status_code == 422 and "qty" in bad.json()["detail"]["errors"]
    over = c.put("/api/portfolio/targets", json={"account": "manual", "targets": {"KO": 70, "PEP": 40}})
    assert over.status_code == 422 and "total" in over.json()["detail"]["errors"]
    assert c.put("/api/portfolio/targets", json={"targets": {"ko": 50}}).json()["targets"] == {"KO": 50}
    plan = c.get("/api/portfolio/rebalance?account=manual&band=5").json()
    assert plan["rows"][0]["ticker"] == "KO" and plan["rows"][0]["current_pct"] == 100.0
    assert plan["orders"][0]["side"] == "SELL" and plan["orders"][0]["qty"] == 6
