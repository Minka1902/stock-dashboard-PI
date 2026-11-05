import pytest
from fastapi.testclient import TestClient

from app import config, db
from app import quotes as quotes_module
from app.models import ContractRecord, LiveQuote
from tests.conftest import authenticate, drain_refresh


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point the app at a temp DB and a stub fetch before importing the app.
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "api.db"))
    import importlib
    from app import config, main as main_module
    importlib.reload(config)
    importlib.reload(main_module)

    # Replace the real contracts fetch with a stub (no network in tests).
    def stub_fetch(conn):
        return [
            ContractRecord(
                external_id="A", award_id="AWD-A", recipient_name="Acme",
                amount=10.0, awarding_agency="DoD", start_date="2026-06-01",
            )
        ]
    main_module.contracts_fetch = stub_fetch

    quotes_module._cache.clear()
    with TestClient(main_module.app) as c:
        authenticate(c)
        yield c
    quotes_module._cache.clear()


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] in ("ok", "degraded")
    assert body["checks"]["db"] is True
    assert "version" in body and "uptime_seconds" in body


def test_contracts_empty_then_populated_after_refresh(client):
    assert client.get("/api/contracts").json() == []
    queued = client.post("/api/refresh/usaspending")
    assert queued.status_code == 202
    assert queued.json()["queued"] is True
    drain_refresh()
    contracts = client.get("/api/contracts").json()
    assert len(contracts) == 1
    assert contracts[0]["recipient_name"] == "Acme"


def test_sources_reports_freshness(client):
    client.post("/api/refresh/usaspending")
    drain_refresh()
    sources = client.get("/api/sources").json()
    assert sources[0]["source"] == "usaspending"
    assert sources[0]["last_refreshed_at"] is not None


def test_refresh_unknown_source_returns_404(client):
    assert client.post("/api/refresh/bogus").status_code == 404


def test_sentiment_endpoint_ok_on_empty_db(client):
    resp = client.get("/api/sentiment")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["indicators"]) == {"fear_greed", "vix", "aaii", "put_call", "margin_debt"}
    assert body["overall"]["lean"] == "NEUTRAL"


def _stub_quotes(monkeypatch, record=None):
    """Stub the one network boundary in the quotes module.

    Needed on every /api/quotes test now that the route also fetches FX: an
    unstubbed call would reach Yahoo for the currency pairs even when the
    watchlist is empty.
    """
    def stub_fetch_quotes(tickers):
        if record is not None:
            record.append(list(tickers))
        return [
            LiveQuote(ticker=t, price=100.0, change_pct=1.5, previous_close=98.5,
                      market_state="PRE", fetched_at="2026-07-04T12:00:00+00:00")
            for t in tickers
        ]
    monkeypatch.setattr(quotes_module, "fetch_quotes", stub_fetch_quotes)
    quotes_module._cache.clear()   # no leakage between tests


def test_quotes_empty_watchlist_and_portfolio(client, monkeypatch):
    """No holdings still yields the FX strip — it is not derived from the
    watchlist, so there is something to show on a brand-new account."""
    _stub_quotes(monkeypatch)
    resp = client.get("/api/quotes")
    assert resp.status_code == 200
    body = resp.json()
    fx = [q for q in body["quotes"] if q["kind"] == "fx"]
    assert [q["ticker"] for q in fx] == config.FX_PAIRS
    # Besides the user's FX pairs, only the market-overview indexes (TA-35).
    assert {q["kind"] for q in body["quotes"]} == {"fx", "index"}
    assert "as_of" in body
    assert set(body["market_statuses"]) == {"NYSE", "TASE"}


def test_quotes_union_of_watchlist_and_portfolio(client, monkeypatch):
    requested = []
    _stub_quotes(monkeypatch, record=requested)
    client.post("/api/watchlist", json={"ticker": "LMT", "note": ""})
    client.post("/api/portfolio", json={"ticker": "NOC", "shares": 1, "avg_cost": 10})

    body = client.get("/api/quotes").json()
    # FX is fetched separately, so the equity request is still exactly the union.
    assert ["LMT", "NOC"] in requested
    equities = [q for q in body["quotes"] if q["kind"] == "equity"]
    assert {q["ticker"] for q in equities} == {"LMT", "NOC"}
    assert all("price" in q and "market_state" in q for q in body["quotes"])


def test_quotes_keeps_fx_out_of_the_equity_fetch(client, monkeypatch):
    """FX must never enter the watchlist path: a watchlist ticker is fed to
    technicals, boom score and earnings, none of which mean anything for a
    currency pair."""
    requested = []
    _stub_quotes(monkeypatch, record=requested)
    client.post("/api/watchlist", json={"ticker": "LMT", "note": ""})

    body = client.get("/api/quotes").json()
    equity_calls = [c for c in requested if c == ["LMT"]]
    assert equity_calls, requested
    assert not any(t.endswith("=X") for call in equity_calls for t in call)
    fx = [q for q in body["quotes"] if q["kind"] == "fx"]
    # Seeded per user with dollar and euro against the shekel.
    assert [q["label"] for q in fx] == ["USD/ILS", "EUR/ILS"]


def test_portfolio_add_twice_merges(client):
    client.post("/api/portfolio", json={"ticker": "AAPL", "shares": 10, "avg_cost": 100})
    body = client.post(
        "/api/portfolio", json={"ticker": "AAPL", "shares": 10, "avg_cost": 200}).json()
    row = next(h for h in body if h["ticker"] == "AAPL")
    assert row["shares"] == 20
    assert row["avg_cost"] == 150.0


def test_portfolio_put_replaces_outright(client):
    client.post("/api/portfolio", json={"ticker": "AAPL", "shares": 10, "avg_cost": 100})
    body = client.put("/api/portfolio/AAPL", json={"shares": 3, "avg_cost": 500}).json()
    row = next(h for h in body if h["ticker"] == "AAPL")
    assert row["shares"] == 3
    assert row["avg_cost"] == 500.0


def test_portfolio_put_unknown_ticker_404(client):
    resp = client.put("/api/portfolio/ZZZZ", json={"shares": 1, "avg_cost": 1})
    assert resp.status_code == 404


def test_portfolio_auto_category_from_fundamentals(client):
    import app.main as main_module
    from app.models import Fundamentals
    db.upsert_fundamentals(main_module.conn, [Fundamentals(
        ticker="XYZ", fetched_at="2026-07-04T00:00:00+00:00",
        sector="Technology", industry="Semiconductors",
        pe_ratio=None, forward_pe=None, peg_ratio=None, pb_ratio=None,
        revenue_growth=None, profit_margin=None, market_cap=None)])
    client.post("/api/portfolio", json={"ticker": "XYZ", "shares": 1, "avg_cost": 10})
    body = client.get("/api/portfolio").json()
    row = next(h for h in body if h["ticker"] == "XYZ")
    assert row["category"] == "Semiconductors"
    assert row["category_source"] == "auto"


def test_portfolio_manual_category_override_persists(client):
    client.post("/api/portfolio", json={"ticker": "XYZ", "shares": 1, "avg_cost": 10})
    body = client.put("/api/portfolio/XYZ/category", json={"category": "AI"}).json()
    row = next(h for h in body if h["ticker"] == "XYZ")
    assert row["category"] == "AI"
    assert row["category_source"] == "manual"
    # survives a fresh read
    row2 = next(h for h in client.get("/api/portfolio").json() if h["ticker"] == "XYZ")
    assert row2["category"] == "AI" and row2["category_source"] == "manual"


def test_portfolio_category_invalid_rejected(client):
    client.post("/api/portfolio", json={"ticker": "XYZ", "shares": 1, "avg_cost": 10})
    resp = client.put("/api/portfolio/XYZ/category", json={"category": "Nonsense"})
    assert resp.status_code == 400


def test_portfolio_category_null_clears_override(client):
    client.post("/api/portfolio", json={"ticker": "XYZ", "shares": 1, "avg_cost": 10})
    client.put("/api/portfolio/XYZ/category", json={"category": "AI"})
    body = client.put("/api/portfolio/XYZ/category", json={"category": None}).json()
    row = next(h for h in body if h["ticker"] == "XYZ")
    assert row["category_source"] == "auto"


def test_vix_aaii_put_call_endpoints_empty(client):
    assert client.get("/api/vix").json() == []
    assert client.get("/api/aaii").json() == []
    assert client.get("/api/put-call").json() == []
    assert client.get("/api/margin-debt").json() == []


def test_margin_debt_endpoint_returns_yoy(client):
    from app import main as main_module
    from app.models import MarginDebtPoint

    db.upsert_margin_debt(main_module.conn, [
        MarginDebtPoint(month="2025-05", debit_balances=677_499.0),
        MarginDebtPoint(month="2026-05", debit_balances=1_050_123.0),
    ])
    body = client.get("/api/margin-debt").json()
    assert body[0] == {"month": "2025-05", "debit_balances": 677499.0, "yoy_pct": None}
    assert body[1]["month"] == "2026-05"
    assert body[1]["yoy_pct"] == 55.0


def test_settings_defaults_and_roundtrip(client):
    body = client.get("/api/settings").json()
    assert body["analysis_time"] == "15:30"
    assert body["analysis_tz"] == "Asia/Jerusalem"
    assert body["quotes_refresh_seconds"] == 30
    assert "next_analysis_run" in body

    updated = client.put("/api/settings", json={
        "analysis_time": "09:45", "analysis_tz": "America/New_York",
        "quotes_refresh_seconds": 60,
    }).json()
    assert updated["analysis_time"] == "09:45"
    assert updated["analysis_tz"] == "America/New_York"
    assert updated["quotes_refresh_seconds"] == 60

    # Partial update keeps the other fields.
    updated = client.put("/api/settings", json={"analysis_time": "15:30"}).json()
    assert updated["analysis_time"] == "15:30"
    assert updated["analysis_tz"] == "America/New_York"


def test_settings_validation(client):
    assert client.put("/api/settings", json={"analysis_time": "25:00"}).status_code == 400
    assert client.put("/api/settings", json={"analysis_time": "noon"}).status_code == 400
    assert client.put("/api/settings", json={"analysis_tz": "Mars/Olympus"}).status_code == 400
    # Refresh cadence clamps into the 10-300s range instead of erroring.
    body = client.put("/api/settings", json={"quotes_refresh_seconds": 3}).json()
    assert body["quotes_refresh_seconds"] == 10
    body = client.put("/api/settings", json={"quotes_refresh_seconds": 9999}).json()
    assert body["quotes_refresh_seconds"] == 300


def test_analysis_trigger_next_fire_is_weekday():
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo
    from app.main import analysis_trigger, parse_analysis_time
    from app.models import AppSettings

    assert parse_analysis_time("15:30") == (15, 30)

    trigger = analysis_trigger(AppSettings())
    tz = ZoneInfo("Asia/Jerusalem")
    # From a Saturday, the next run must land on Monday 15:30 local time.
    saturday = dt(2026, 7, 4, 12, 0, tzinfo=tz)
    fire = trigger.get_next_fire_time(None, saturday)
    assert fire.weekday() == 0
    assert (fire.hour, fire.minute) == (15, 30)


def test_analysis_report_endpoint(client):
    import json as _json
    from app import analysis as analysis_engine
    from app import main as main_module
    from app.models import OHLCBar, OHLCSeries

    assert client.get("/api/analysis/ZZZQ/report").status_code == 404

    bars = []
    px = 100.0
    for i in range(80):
        px *= 1.002
        bars.append(OHLCBar(date=f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
                            open=px * 0.99, high=px * 1.01, low=px * 0.98,
                            close=px, volume=1000))
    db.upsert_ohlc(main_module.conn, [OHLCSeries(
        ticker="NOC", interval="daily",
        bars_json=_json.dumps([b.model_dump() for b in bars]),
        fetched_at="2026-07-06T00:00:00+00:00",
    )])
    a = analysis_engine.build("NOC", bars, px, None, 1.0)
    db.upsert_analyses(main_module.conn, [a])

    resp = client.get("/api/analysis/NOC/report")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "attachment" in resp.headers.get("content-disposition", "")
    assert "NOC" in resp.text

    inline = client.get("/api/analysis/NOC/report?print=1")
    assert "content-disposition" not in inline.headers
    assert "window.print" in inline.text
