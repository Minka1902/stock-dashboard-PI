"""Famous investors' 13F holdings: Berkshire's real Q1 and Q2 2026
information tables (fixtures by way of trader-skill's tests), the quarter
diff, ticker matching, read-once, alerts and the API."""
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, db
from app.models import WatchItem
from app.sources import holdings13f
from tests.conftest import authenticate

FIX = Path(__file__).parent / "fixtures" / "13f"
Q2 = ((FIX / "000119312526352200" / "56757.xml").read_bytes(), "2026-08-14")
Q1 = ((FIX / "000119312526226661" / "53405.xml").read_bytes(), "2026-05-15")
TICKERS = json.loads((FIX / "company_tickers.json").read_text())


class Sec:
    def __init__(self):
        self.urls = []

    def __call__(self, req):
        url = str(req.url)
        self.urls.append(url)
        if url.endswith("CIK0001067983.json"):
            return httpx.Response(200, content=(FIX / "CIK0001067983.json").read_bytes())
        if url.endswith("company_tickers.json"):
            return httpx.Response(200, content=(FIX / "company_tickers.json").read_bytes())
        folder, name = url.split("/")[-2:]
        path = FIX / folder / name
        return httpx.Response(200, content=path.read_bytes()) if path.exists() else httpx.Response(404)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


@pytest.fixture(autouse=True)
def berkshire_only(monkeypatch):
    monkeypatch.setattr(config, "FILERS_13F", ["1067983"])
    monkeypatch.setattr(holdings13f, "_PAUSE_SECONDS", 0)


def test_a_quarter_sums_rows_per_cusip_in_dollars():
    positions = holdings13f.parse_info_table(*Q2)
    assert len(positions) == 29 and round(sum(p["value"] for p in positions)) == 299253556246
    apple = positions[0]
    assert apple["issuer"] == "APPLE INC" and round(apple["value"] / 299253556246 * 100, 2) == 22.04
    assert holdings13f.parse_info_table(Q2[0], "2022-11-14")[0]["value"] == apple["value"] * 1000


def test_the_diff_finds_whats_new_and_sold_out():
    ticks = holdings13f.TickerIndex(TICKERS)
    moves = holdings13f.diff(holdings13f.parse_info_table(*Q2), holdings13f.parse_info_table(*Q1))
    holdings13f.resolve(moves, ticks)
    by = {}
    for m in moves:
        by.setdefault(m["status"], []).append(m["ticker"])
    assert by["NEW"] == ["DHI"] and by["SOLD OUT"] == ["STZ"]
    sold = next(m for m in moves if m["status"] == "SOLD OUT")
    assert sold["shares"] == 0 and sold["prev_value"] > 0 and sold["change_pct"] == -100.0


def test_share_classes_come_from_the_verified_table_and_names_from_sec():
    ticks = holdings13f.TickerIndex(TICKERS)
    positions = holdings13f.parse_info_table(*Q2)
    holdings13f.resolve(positions, ticks)
    by_cusip = {p["cusip"]: (p["ticker"], p["ticker_source"]) for p in positions}
    assert by_cusip["02079K305"] == ("GOOGL", "share-class table") and by_cusip["02079K107"][0] == "GOOG"
    assert by_cusip["037833100"] == ("AAPL", "name")


def test_a_filing_is_read_once_and_alerts_who_follows_the_ticker(conn):
    u = db.create_user(conn, "f@x.co", "h", "t")
    for t in ("DHI", "AAPL"):
        db.add_watch(conn, u.id, WatchItem(ticker=t, note="", added_at="t"))
    sec = Sec()
    out = holdings13f.fetch(conn, sec.client())
    holdings13f.store(conn, out)
    assert "new: Berkshire Hathaway 2026-06-30" in out.note and not out.warning
    f = db.get_filings_13f(conn)[0]
    assert (f.period, f.prior_period, f.accession, f.positions) == ("2026-06-30", "2026-03-31",
                                                                    "0001193125-26-352200", 29)
    alerts = db.get_alerts(conn, u.id)
    assert [a.title for a in alerts] == ["Berkshire Hathaway opened a position in DHI"]
    again = Sec()
    out = holdings13f.fetch(conn, again.client())
    assert len(again.urls) == 1 and "no new 13F" in out.note and list(out) == []


def test_a_filer_that_fails_is_named_and_one_that_works_is_kept(conn, monkeypatch):
    monkeypatch.setattr(config, "FILERS_13F", ["1067983", "1336528"])
    out = holdings13f.fetch(conn, Sec().client())
    assert "Pershing Square: HTTPStatusError" in out.warning and len(out.filings) == 1
    monkeypatch.setattr(config, "FILERS_13F", ["1336528"])
    with pytest.raises(RuntimeError, match="Pershing Square"):
        holdings13f.fetch(conn, Sec().client())


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "13f.db"))
    import importlib

    from app import main as main_module
    importlib.reload(config)
    importlib.reload(main_module)
    monkeypatch.setattr(config, "FILERS_13F", ["1067983"])
    with TestClient(main_module.app) as c:
        authenticate(c)
        yield c, main_module


def test_api_overview_and_holders(client):
    c, m = client
    holdings13f.store(m.conn, holdings13f.fetch(m.conn, Sec().client()))
    view = c.get("/api/13f").json()
    assert view["filers"][0]["filer"] == "Berkshire Hathaway"
    assert view["moves"][0]["status"] == "NEW" and view["moves"][0]["ticker"] == "DHI"
    held = c.get("/api/13f/aapl").json()
    assert held["ticker"] == "AAPL" and held["holders"][0]["pct"] == 22.04
    assert held["holders"][0]["person"] == "Warren Buffett / Greg Abel"
