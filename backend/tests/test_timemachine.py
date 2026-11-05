"""The Time Machine: seeded replays from the stock's own days, stop first on
a day that touches both, gaps filled at the open, the history replay, the
verdict, and the API."""
import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import config, db, timemachine as tm
from app.models import OHLCBar, OHLCSeries
from tests.conftest import authenticate


def _bars(days, start=100.0):
    """`days` is a list of (open, high, low, close) ratios to the previous close."""
    out, prev = [OHLCBar(date="2024-01-01", open=start, high=start, low=start, close=start, volume=1)], start
    for i, (o, h, l, c) in enumerate(days, 1):
        out.append(OHLCBar(date=(date(2024, 1, 1) + timedelta(days=i)).isoformat(), open=prev * o,
                           high=prev * h, low=prev * l, close=prev * c, volume=1))
        prev *= c
    return out


UP = [(1.0, 1.011, 0.999, 1.01)] * 120          # every day +1%, never dips
WIDE = [(1.0, 1.2, 0.8, 1.0)] * 120             # every day touches ±20%
GAP = [(0.85, 0.86, 0.84, 0.85)] * 120          # every day gaps 15% down


def test_replays_are_repeatable_by_seed():
    bars = _bars([(1.0, 1.02, 0.98, 1.0 + (0.01 if i % 3 else -0.012)) for i in range(200)])
    e = bars[-1].close
    a, b = tm.run(bars, e, e * 0.95, e * 1.1), tm.run(bars, e, e * 0.95, e * 1.1)
    c = tm.run(bars, e, e * 0.95, e * 1.1, seed=7)
    assert a["p_target"] == b["p_target"] and a["bands"] == b["bands"] and a["seed"] == 19
    assert (c["p_target"], c["p_stop"]) != (a["p_target"], a["p_stop"])


def test_a_steady_climb_hits_the_target_and_says_when():
    bars = _bars(UP)
    e = bars[-1].close
    r = tm.run(bars, e, e * 0.95, e * 1.05, horizon=20)
    assert r["p_target"] == 1.0 and r["median_days_to_exit"] == 5 and r["avg_r"] == pytest.approx(1.0, abs=0.02)
    assert r["verdict"].startswith("Strongly toward the target")
    assert r["history"] == {"sessions": 100, "target": 100, "stop": 0, "neither": 0, "avg_r": pytest.approx(1.0, abs=0.02)}


def test_a_day_touching_both_counts_as_the_stop():
    bars = _bars(WIDE)
    e = bars[-1].close
    r = tm.run(bars, e, e * 0.9, e * 1.1)
    assert r["p_stop"] == 1.0 and r["p_target"] == 0.0 and r["median_days_to_exit"] == 1


def test_a_gap_through_the_stop_fills_at_the_open_and_loses_more_than_1r():
    bars = _bars(GAP)
    e = bars[-1].close
    r = tm.run(bars, e, e * 0.95, e * 1.05)
    assert r["p_stop"] == 1.0 and r["avg_r"] == pytest.approx(-3.0)        # opened 15% down, risk was 5%


def test_without_a_target_it_reports_only_the_stop():
    bars = _bars(UP)
    r = tm.run(bars, bars[-1].close, bars[-1].close * 0.9, None)
    assert r["r_at_target"] is None and r["p_target"] == 0 and r["verdict"].startswith("Stop hit within 20")


@pytest.mark.parametrize("pt,ps,r,head", [
    (0.30, 0.30, 1.0, "Close to a coin flip"),
    (0.25, 0.50, 2.0, "Close to break-even"),          # 33% of hits; break-even 33%
    (0.40, 0.30, 1.0, "Leans toward the target"),       # 57% vs 50%
    (0.10, 0.60, 1.0, "Strongly toward the stop"),
])
def test_the_verdict_measures_against_break_even_at_the_targets_r(pt, ps, r, head):
    line, _ = tm.verdict({"p_target": pt, "p_stop": ps, "p_neither": 1 - pt - ps, "r_at_target": r,
                          "avg_r": 0.0}, 20)
    assert line.startswith(head)


def test_the_notes_flag_many_undecided_and_an_average_r_against_the_lean():
    _, notes = tm.verdict({"p_target": 0.10, "p_stop": 0.30, "p_neither": 0.60, "r_at_target": 1.0,
                           "avg_r": 0.2}, 20)
    assert "60.0% of the replays hit neither" in notes[0] and "Average R is +0.200" in notes[1]


def test_a_plan_that_cant_be_replayed_says_which_field():
    bars = _bars(UP)
    e = bars[-1].close
    for kw, field in (({"stop": e * 1.01}, "stop"), ({"stop": e * 0.9, "target": e * 0.99}, "target")):
        with pytest.raises(ValueError, match=f"^{field}\\|"):
            tm.run(bars, e, kw["stop"], kw.get("target"))
    with pytest.raises(ValueError, match="^ticker\\|needs at least 60 days"):
        tm.run(_bars(UP[:30]), e, e * 0.9, None)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "tm.db"))
    import importlib

    from app import main as main_module, quotes
    from app.sources import ohlc
    importlib.reload(config)
    importlib.reload(main_module)
    monkeypatch.setattr(quotes, "get_quotes", lambda tickers, ttl_seconds=None: [])
    monkeypatch.setattr(ohlc, "fetch", lambda tickers, series=None: [])
    with TestClient(main_module.app) as c:
        authenticate(c)
        yield c, main_module


def test_api_replays_from_the_last_close_and_names_bad_fields(client):
    c, m = client
    bars = _bars(UP)
    db.upsert_ohlc(m.conn, [OHLCSeries(ticker="AAPL", interval="daily", fetched_at="t",
                                       bars_json=json.dumps([b.model_dump() for b in bars]))])
    e = bars[-1].close
    r = c.post("/api/timemachine", json={"ticker": "aapl", "stop": e * 0.95, "target": e * 1.05}).json()
    assert r["ticker"] == "AAPL" and r["entry"] == e and r["entry_source"] == f"the {bars[-1].date} close"
    assert r["data"].startswith("the stored 2 years") and r["p_target"] == 1.0 and len(r["bands"]) == 20
    bad = c.post("/api/timemachine", json={"ticker": "AAPL", "stop": e * 1.1})
    assert bad.status_code == 422 and "stop" in bad.json()["detail"]["errors"]
    assert c.post("/api/timemachine", json={"ticker": "AAPL", "stop": 1, "horizon_days": 500}).status_code == 422
