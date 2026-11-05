"""Portfolio analytics: TWR through flows, drawdown, volatility, Sharpe/Sortino,
beta, exposure, correlation and the plain-language risk read."""
from datetime import date, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import analytics, db
from app.models import Bot, EquitySnapshot
from app.sources.yield_curve import parse_response
from tests.conftest import authenticate


def _snaps(values, flows=None, start=date(2026, 6, 1)):
    flows = flows or [0.0] * len(values)
    return [EquitySnapshot(user_id=1, account="manual", date=(start + timedelta(days=i)).isoformat(),
                           base_currency="USD", value_base=v, cash_base=v, invested_base=0.0,
                           flow_base=f, computed_at="t") for i, (v, f) in enumerate(zip(values, flows))]


def _index(levels, start=date(2026, 6, 1)):
    return [((start + timedelta(days=i)).isoformat(), lv) for i, lv in enumerate(levels)]


# ---------- returns ----------

def test_twr_ignores_deposits_and_withdrawals():
    # +10% on 1000, then a 500 deposit, then +10% on 1600, then a 300 withdrawal.
    snaps = _snaps([1000, 1100, 1600, 1760, 1460], flows=[0, 0, 500, 0, -300])
    levels = [round(v, 6) for _, v in analytics.twr_index(snaps)]
    assert levels == [1.0, 1.1, 1.1, 1.21, 1.21]


def test_max_drawdown_on_a_known_series():
    levels = [1.0, 1.2, 0.9, 1.1] + [1.1] * 20
    m = analytics.performance_metrics(_index(levels), None, None)
    assert m["max_drawdown_pct"] == 25.0
    assert analytics.drawdown_series(_index([1.0, 1.2, 0.9]))[-1] == 25.0


def test_nothing_is_reported_below_twenty_snapshots():
    m = analytics.performance_metrics(_index([1.0 + i / 100 for i in range(19)]), 4.0, None)
    assert all(v is None for v in m.values())


def test_sharpe_needs_a_risk_free_rate():
    rng = np.random.default_rng(1)
    levels = list(np.cumprod(1 + rng.normal(0.001, 0.01, 60)))
    assert analytics.performance_metrics(_index(levels), None, None)["sharpe"] is None
    m = analytics.performance_metrics(_index(levels), 4.0, None)
    assert m["sharpe"] is not None and m["sortino"] is not None and m["volatility_pct"] > 0


def test_beta_against_a_benchmark_fixture():
    rng = np.random.default_rng(7)
    rb = rng.normal(0.0005, 0.01, 60)
    bench_levels = np.cumprod(np.concatenate([[1.0], 1 + rb]))
    port_levels = np.cumprod(np.concatenate([[1.0], 1 + 2 * rb]))
    idx = _index(list(port_levels))
    bench = {d: float(b) * 400 for (d, _), b in zip(idx, bench_levels)}
    assert analytics.performance_metrics(idx, 4.0, bench)["beta"] == pytest.approx(2.0, abs=1e-6)


# ---------- exposure, correlation, risk read ----------

def test_exposure_weights_concentration_and_unknown_sector():
    e = analytics.exposure([
        {"ticker": "NVDA", "value": 600, "sector": "Technology", "currency": "USD"},
        {"ticker": "AMD", "value": 300, "sector": "Technology", "currency": "USD"},
        {"ticker": "XYZ", "value": 100, "sector": None, "currency": "ILS"},
    ])
    assert [p["ticker"] for p in e["positions"]] == ["NVDA", "AMD", "XYZ"]
    assert e["by_sector"] == [{"name": "Technology", "weight": 0.9}, {"name": "unknown", "weight": 0.1}]
    assert e["top3_weight"] == 1.0 and e["hhi"] == pytest.approx(0.46) and e["effective_n"] == 2.2


def _closes(returns, start=date(2026, 3, 2)):
    out, px = [], 100.0
    for i, r in enumerate([0.0, *returns]):
        px *= 1 + r
        out.append(((start + timedelta(days=i)).isoformat(), px))
    return out


def test_correlation_flags_pairs_that_move_together():
    rng = np.random.default_rng(3)
    a = rng.normal(0, 0.01, 60)
    c = analytics.correlation({"A": _closes(a), "B": _closes(a * 1.5), "C": _closes(-a),
                               "D": _closes(a)[:10]})
    m = dict(zip(c["tickers"], c["matrix"]))
    assert m["A"][1] == pytest.approx(1.0) and m["A"][2] == pytest.approx(-1.0)
    assert m["A"][3] is None                                  # not enough overlap
    assert [(p["a"], p["b"]) for p in c["pairs"]] == [("A", "B")]


def test_risk_read_names_concentrations_or_says_there_are_none():
    expo = analytics.exposure([
        {"ticker": t, "value": v, "sector": s, "currency": "USD"}
        for t, v, s in [("NVDA", 40, "Semis"), ("AMD", 25, "Semis"), ("AVGO", 20, "Semis"),
                        ("KO", 5, "Staples"), ("PEP", 5, "Staples"), ("JNJ", 5, "Health")]])
    corr = {"pairs": [{"a": "AMD", "b": "NVDA", "rho": 0.9}, {"a": "AVGO", "b": "NVDA", "rho": 0.85},
                      {"a": "AMD", "b": "AVGO", "rho": 0.82}]}
    assert analytics.risk_read(expo, corr) == "85% Semis; top 3 are 85% of the book; the top 3 move together."
    corr["pairs"] = corr["pairs"][:1]
    assert analytics.risk_read(expo, corr).endswith("AMD & NVDA move together.")
    flat = analytics.exposure([{"ticker": t, "value": 10, "sector": s, "currency": "USD"}
                               for t, s in [("A", "x"), ("B", "y"), ("C", "z"), ("D", "w"), ("E", "v")]])
    assert analytics.risk_read(flat, {"pairs": []}).startswith("No concentration flags")


def test_the_three_month_bill_is_parsed():
    xml = """<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"
      xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"><entry><content><m:properties>
      <d:NEW_DATE>2026-10-30T00:00:00</d:NEW_DATE><d:BC_3MONTH>4.21</d:BC_3MONTH><d:BC_2YEAR>3.9</d:BC_2YEAR>
      <d:BC_10YEAR>4.1</d:BC_10YEAR><d:BC_30YEAR>4.5</d:BC_30YEAR></m:properties></content></entry></feed>"""
    [p] = parse_response(xml)
    assert p.yr3m == 4.21 and p.spread == pytest.approx(0.2)


# ---------- API ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "analytics.db"))
    import importlib

    from app import config, main as main_module, quotes
    importlib.reload(config)
    importlib.reload(main_module)
    monkeypatch.setattr(quotes, "get_quotes", lambda tickers, ttl_seconds=None: [])
    analytics._cache.clear()
    with TestClient(main_module.app) as c:
        authenticate(c)
        yield c, main_module


def test_api_report_shape_and_account_ownership(client):
    c, m = client
    uid = db.get_users(m.conn)[0].id
    db.upsert_equity_snapshots(m.conn, [s.model_copy(update={"user_id": uid}) for s in
                                        _snaps([1000 + i * 3 for i in range(25)])])
    r = c.get("/api/portfolio/analytics").json()
    assert r["performance"]["n_snapshots"] == 25 and r["performance"]["metrics"]["twr_pct"] == 7.2
    assert r["performance"]["risk_free"] is None and r["performance"]["metrics"]["sharpe"] is None
    assert {"source", "as_of"} <= set(r["exposure"]) and {"source", "as_of"} <= set(r["correlation"])
    assert c.get("/api/portfolio/analytics?account=live").status_code == 400
    other = db.create_user(m.conn, "o@x.co", "h", "t")
    bot = db.create_bot(m.conn, Bot(user_id=other.id, name="b", strategy="trend", currency="USD",
                                    allocated=0, created_at="t"))
    assert c.get(f"/api/portfolio/analytics?account=bot:{bot.id}").status_code == 404
