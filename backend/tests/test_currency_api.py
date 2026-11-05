"""API surface for currencies, the per-user FX watch list and TASE search."""
import pytest
from fastapi.testclient import TestClient

from app import quotes as quotes_module
from app import search as search_module
from app.models import LiveQuote
from tests.conftest import authenticate


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "ccy.db"))
    import importlib
    from app import config, main as main_module
    importlib.reload(config)
    importlib.reload(main_module)

    def stub_fetch_quotes(tickers):
        rates = {"USDILS=X": 3.0, "EURILS=X": 3.5, "EURUSD=X": 1.1, "TA35.TA": 4200.0}
        return [
            LiveQuote(ticker=t, price=rates.get(t, 100.0), change_pct=0.5, previous_close=99.0,
                      market_state="LIVE", fetched_at="t",
                      currency="ILS" if t.endswith(".TA") else "USD")
            for t in tickers
            if not (t.endswith("=X") and t not in rates)  # unknown pairs: no quote
        ]
    monkeypatch.setattr(quotes_module, "fetch_quotes", stub_fetch_quotes)
    quotes_module._cache.clear()
    with TestClient(main_module.app) as c:
        authenticate(c)
        yield c
    quotes_module._cache.clear()


# ---- FX watch list ----

def test_fx_watch_defaults_to_dollar_and_euro(client):
    body = client.get("/api/fx-watch").json()
    assert body["pairs"] == ["USDILS=X", "EURILS=X"]
    assert "ILS" in body["currencies"]


def test_fx_watch_put_reorders_and_is_used_by_quotes(client):
    r = client.put("/api/fx-watch", json={"pairs": ["eurusd=x", "USDILS=X"]})
    assert r.status_code == 200, r.text
    assert r.json()["pairs"] == ["EURUSD=X", "USDILS=X"]
    fx = [q for q in client.get("/api/quotes").json()["quotes"] if q["kind"] == "fx"]
    assert [q["label"] for q in fx] == ["EUR/USD", "USD/ILS"]


def test_fx_watch_rejects_invalid_pairs(client):
    for bad in (["USDUSD=X"], ["ABCDEF=X"], ["USDILS"], ["AAPL"]):
        r = client.put("/api/fx-watch", json={"pairs": bad})
        assert r.status_code == 400, bad


def test_fx_watch_can_be_emptied(client):
    assert client.put("/api/fx-watch", json={"pairs": []}).json()["pairs"] == []
    assert client.get("/api/fx-watch").json()["pairs"] == []
    kinds = {q["kind"] for q in client.get("/api/quotes").json()["quotes"]}
    assert "fx" not in kinds


def test_quotes_include_the_tase_index_tagged(client):
    qs = client.get("/api/quotes").json()["quotes"]
    ta = next(q for q in qs if q["ticker"] == "TA35.TA")
    assert (ta["kind"], ta["label"], ta["market"]) == ("index", "TA-35", "TASE")
    assert ta["market_state"] in ("LIVE", "CLOSED")


# ---- portfolio currency ----

def test_add_tase_holding_detects_shekels_without_network(client):
    rows = client.post("/api/portfolio",
                       json={"ticker": "TEVA.TA", "shares": 10, "avg_cost": 120}).json()
    teva = next(h for h in rows if h["ticker"] == "TEVA.TA")
    assert (teva["currency"], teva["market"]) == ("ILS", "TASE")
    rows = client.post("/api/portfolio",
                       json={"ticker": "AAPL", "shares": 1, "avg_cost": 200}).json()
    assert next(h for h in rows if h["ticker"] == "AAPL")["currency"] == "USD"


def test_add_with_explicit_currency_override(client):
    rows = client.post("/api/portfolio", json={
        "ticker": "TEVA", "shares": 1, "avg_cost": 400, "currency": "ils"}).json()
    assert next(h for h in rows if h["ticker"] == "TEVA")["currency"] == "ILS"


def test_add_in_a_different_currency_than_held_is_refused(client):
    client.post("/api/portfolio", json={"ticker": "TEVA.TA", "shares": 1, "avg_cost": 100})
    r = client.post("/api/portfolio", json={
        "ticker": "TEVA.TA", "shares": 1, "avg_cost": 30, "currency": "USD"})
    assert r.status_code == 400
    assert "ILS" in r.json()["detail"]


def test_unknown_currency_is_rejected(client):
    r = client.post("/api/portfolio", json={
        "ticker": "AAPL", "shares": 1, "avg_cost": 1, "currency": "XYZ"})
    assert r.status_code == 400


def test_edit_can_change_currency(client):
    client.post("/api/portfolio", json={"ticker": "TEVA", "shares": 1, "avg_cost": 10})
    rows = client.put("/api/portfolio/TEVA",
                      json={"shares": 2, "avg_cost": 11, "currency": "ILS"}).json()
    assert next(h for h in rows if h["ticker"] == "TEVA")["currency"] == "ILS"


# ---- base currency + rates ----

def test_profile_base_currency(client):
    assert client.get("/api/profile").json()["base_currency"] == "USD"
    r = client.put("/api/profile", json={"base_currency": "ILS"})
    assert r.json()["base_currency"] == "ILS"
    # A partial PUT (other form) keeps it.
    assert client.put("/api/profile", json={"risk_pct": 2}).json()["base_currency"] == "ILS"
    assert client.put("/api/profile", json={"base_currency": "EUR"}).status_code == 400


def test_fx_rates_to_base(client):
    client.post("/api/portfolio", json={"ticker": "TEVA.TA", "shares": 1, "avg_cost": 1})
    client.post("/api/portfolio", json={"ticker": "AAPL", "shares": 1, "avg_cost": 1})
    body = client.get("/api/fx/rates?base=ILS").json()
    assert body["base"] == "ILS"
    assert body["rates"]["USD"] == pytest.approx(3.0)
    assert body["rates"]["ILS"] == 1.0
    assert body["unavailable"] == []
    inv = client.get("/api/fx/rates?base=USD").json()
    # No ILSUSD=X quote: the inverse of USDILS=X is used.
    assert inv["rates"]["ILS"] == pytest.approx(1 / 3.0)


def test_fx_rates_missing_rate_is_null_not_guessed(client, monkeypatch):
    monkeypatch.setattr(quotes_module, "fetch_quotes", lambda tickers: [])
    quotes_module._cache.clear()
    body = client.get("/api/fx/rates?base=USD&currencies=ILS").json()
    assert body["rates"]["ILS"] is None
    assert body["unavailable"] == ["ILS"]


# ---- search ----

def test_search_market_param(client, monkeypatch):
    monkeypatch.setattr(search_module, "fetch", lambda q, limit=8: [
        {"symbol": "TEVA", "name": "Teva", "exchange": "NYSE", "type": "EQUITY",
         "market": "US", "currency": "USD"},
        {"symbol": "TEVA.TA", "name": "Teva", "exchange": "Tel Aviv", "type": "EQUITY",
         "market": "TASE", "currency": "ILS"},
    ])
    search_module._cache.clear()
    assert [r["symbol"] for r in client.get("/api/search?q=teva&market=tase").json()] == ["TEVA.TA"]
    assert [r["symbol"] for r in client.get("/api/search?q=teva&market=us").json()] == ["TEVA"]
    assert client.get("/api/search?q=teva&market=moon").status_code == 422
    search_module._cache.clear()


# ---- switching base currency carries the account size across ----

def test_switching_base_converts_account_size_at_the_live_rate(client):
    client.put("/api/profile", json={"account_size": 10000})
    body = client.put("/api/profile", json={"base_currency": "ILS"}).json()
    assert body["base_currency"] == "ILS"
    assert body["account_size"] == pytest.approx(30000.0)  # USDILS=X 3.0
    assert "3.0000" in body["note"]
    back = client.put("/api/profile", json={"base_currency": "USD"}).json()
    assert back["account_size"] == pytest.approx(10000.0)


def test_switching_base_without_fx_keeps_the_number_and_says_so(client, monkeypatch):
    client.put("/api/profile", json={"account_size": 10000})
    monkeypatch.setattr(quotes_module, "fetch_quotes", lambda tickers: [])
    quotes_module._cache.clear()
    body = client.put("/api/profile", json={"base_currency": "ILS"}).json()
    assert body["base_currency"] == "ILS"
    assert body["account_size"] == 10000
    assert "FX unavailable" in body["note"]


def test_explicit_account_size_with_a_base_switch_is_taken_as_given(client):
    client.put("/api/profile", json={"account_size": 10000})
    body = client.put("/api/profile", json={"base_currency": "ILS", "account_size": 50000}).json()
    assert body["account_size"] == 50000
