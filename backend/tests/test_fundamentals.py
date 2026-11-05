"""Unit tests for the fundamentals source module."""
import json

import httpx
import pytest

from app import db
import app.sources.fundamentals as fundamentals_source
from app.models import CompanyHolder, Fundamentals, InsiderTrade
from app.sources.fundamentals import parse_holders, parse_response

_NOW = "2026-06-22T10:00:00+00:00"

FULL_PAYLOAD = {
    "quoteSummary": {
        "result": [{
            "summaryProfile": {
                "sector": "Technology",
                "industry": "Software—Application",
            },
            "summaryDetail": {
                "trailingPE": {"raw": 28.5},
                "forwardPE": {"raw": 22.1},
                "priceToBook": {"raw": 12.3},
                "marketCap": {"raw": 2_500_000_000_000},
            },
            "defaultKeyStatistics": {
                "pegRatio": {"raw": 1.8},
                "profitMargins": {"raw": 0.24},
                "revenueGrowth": {"raw": 0.12},
            },
        }],
        "error": None,
    }
}

EMPTY_PAYLOAD = {"quoteSummary": {"result": [], "error": None}}
MALFORMED_PAYLOAD = {"quoteSummary": None}


def test_parse_response_extracts_all_fields():
    f = parse_response(FULL_PAYLOAD, "MSFT", _NOW)
    assert f is not None
    assert f.ticker == "MSFT"
    assert f.fetched_at == _NOW
    assert f.sector == "Technology"
    assert f.industry == "Software—Application"
    assert f.pe_ratio == 28.5
    assert f.forward_pe == 22.1
    assert f.pb_ratio == 12.3
    assert f.peg_ratio == 1.8
    assert f.profit_margin == 0.24
    assert f.revenue_growth == 0.12
    assert f.market_cap == 2_500_000_000_000.0


def test_parse_response_empty_result_returns_none():
    assert parse_response(EMPTY_PAYLOAD, "X", _NOW) is None


def test_parse_response_malformed_returns_none():
    assert parse_response(MALFORMED_PAYLOAD, "X", _NOW) is None


def test_parse_response_partial_data_fills_none():
    payload = {
        "quoteSummary": {
            "result": [{
                "summaryProfile": {"sector": "Energy"},
                "summaryDetail": {},
                "defaultKeyStatistics": {},
            }]
        }
    }
    f = parse_response(payload, "XOM", _NOW)
    assert f is not None
    assert f.sector == "Energy"
    assert f.pe_ratio is None
    assert f.market_cap is None


def test_parse_response_ticker_uppercased():
    f = parse_response(FULL_PAYLOAD, "msft", _NOW)
    assert f is not None
    assert f.ticker == "MSFT"


# ---- company profile + ownership (Task 11) ----

PROFILE_PAYLOAD = {
    "quoteSummary": {
        "result": [{
            "price": {"longName": "NVIDIA Corporation", "shortName": "NVIDIA Corp"},
            "assetProfile": {
                "sector": "Technology",
                "industry": "Semiconductors",
                "website": "https://www.nvidia.com",
                "country": "United States",
                "city": "Santa Clara",
                "fullTimeEmployees": 29600,
                "longBusinessSummary": "NVIDIA Corporation provides graphics and compute platforms.",
                "companyOfficers": [
                    {"name": "Mr. Jen-Hsun Huang", "title": "CEO", "age": {"raw": 62},
                     "totalPay": {"raw": 34_000_000}},
                    {"name": "Ms. Colette Kress", "title": "CFO"},
                    {"name": "", "title": "ignored — no name"},
                ],
            },
            "majorHoldersBreakdown": {
                "insidersPercentHeld": {"raw": 0.0412},
                "institutionsPercentHeld": {"raw": 0.6688},
            },
            "institutionOwnership": {
                "ownershipList": [
                    {"organization": "Vanguard Group Inc", "pctHeld": {"raw": 0.0871},
                     "position": {"raw": 2_100_000_000}, "value": {"raw": 300_000_000_000},
                     "reportDate": {"fmt": "2026-03-31"}},
                    {"organization": "Blackrock Inc.", "pctHeld": {"raw": 0.0734}},
                    {"pctHeld": {"raw": 0.01}},  # no organization — skipped
                ],
            },
            "summaryDetail": {},
            "defaultKeyStatistics": {},
        }],
    }
}


def test_parse_response_extracts_company_profile():
    f = parse_response(PROFILE_PAYLOAD, "NVDA", _NOW)
    assert f is not None
    assert f.name == "NVIDIA Corporation"
    assert f.website == "https://www.nvidia.com"
    assert f.country == "United States" and f.city == "Santa Clara"
    assert f.employees == 29600
    assert f.summary.startswith("NVIDIA Corporation provides")
    assert f.insider_pct == 0.0412
    assert f.institution_pct == 0.6688
    # assetProfile is preferred over summaryProfile for sector/industry
    assert f.sector == "Technology" and f.industry == "Semiconductors"


def test_financial_currency_is_parsed_and_absent_stays_none():
    """Officer pay is reported in the company's financial currency (LUMI.TA:
    ILS, TEVA.TA: USD) — keep the code so the UI labels pay correctly."""
    import copy
    payload = copy.deepcopy(PROFILE_PAYLOAD)
    assert parse_response(payload, "NVDA", _NOW).financial_currency is None
    payload["quoteSummary"]["result"][0]["financialData"] = {"financialCurrency": "ils"}
    assert parse_response(payload, "LUMI.TA", _NOW).financial_currency == "ILS"
    payload["quoteSummary"]["result"][0]["financialData"] = {"financialCurrency": "shekels"}
    assert parse_response(payload, "LUMI.TA", _NOW).financial_currency is None


def test_financial_currency_roundtrips(conn):
    db.upsert_fundamentals(conn, [_fundamentals(financial_currency="ILS")])
    assert db.get_fundamentals_for(conn, "NVDA").financial_currency == "ILS"


def test_parse_officers_skips_nameless_and_keeps_order():
    f = parse_response(PROFILE_PAYLOAD, "NVDA", _NOW)
    officers = json.loads(f.officers_json)
    assert [o["name"] for o in officers] == ["Mr. Jen-Hsun Huang", "Ms. Colette Kress"]
    assert officers[0]["title"] == "CEO" and officers[0]["age"] == 62
    assert officers[0]["pay"] == 34_000_000
    assert officers[1]["age"] is None and officers[1]["pay"] is None


def test_parse_holders_skips_rows_without_an_organization():
    holders = parse_holders(PROFILE_PAYLOAD, "nvda")
    assert [h.holder for h in holders] == ["Vanguard Group Inc", "Blackrock Inc."]
    assert holders[0].ticker == "NVDA"
    assert holders[0].pct_held == 0.0871
    assert holders[0].shares == 2_100_000_000
    assert holders[0].reported_at == "2026-03-31"
    assert holders[1].shares is None  # absent stays absent


def test_profile_fields_absent_stay_none():
    f = parse_response(FULL_PAYLOAD, "MSFT", _NOW)
    assert f.name is None and f.website is None and f.employees is None
    assert f.officers_json == ""
    assert f.insider_pct is None
    assert parse_holders(FULL_PAYLOAD, "MSFT") == []


def test_parse_holders_malformed_returns_empty():
    assert parse_holders(MALFORMED_PAYLOAD, "X") == []
    assert parse_holders(EMPTY_PAYLOAD, "X") == []


# ---- storage ----

def _fundamentals(**over) -> Fundamentals:
    base = dict(
        ticker="NVDA", fetched_at=_NOW, sector=None, industry=None, pe_ratio=None,
        forward_pe=None, peg_ratio=None, pb_ratio=None, revenue_growth=None,
        profit_margin=None, market_cap=None,
    )
    base.update(over)
    return Fundamentals(**base)


def test_fundamentals_roundtrip_keeps_profile(conn):
    db.upsert_fundamentals(conn, [_fundamentals(
        name="NVIDIA Corporation", website="https://www.nvidia.com",
        employees=29600, officers_json='[{"name":"X"}]', institution_pct=0.66,
    )])
    got = db.get_fundamentals_for(conn, "NVDA")
    assert got.name == "NVIDIA Corporation"
    assert got.employees == 29600
    assert got.institution_pct == 0.66
    # a second write updates rather than duplicating
    db.upsert_fundamentals(conn, [_fundamentals(name="Renamed Inc")])
    assert db.get_fundamentals_for(conn, "NVDA").name == "Renamed Inc"


def test_company_holders_roundtrip_sorted_by_stake(conn):
    db.upsert_company_holders(conn, [
        CompanyHolder(ticker="NVDA", holder="Small Fund", pct_held=0.01),
        CompanyHolder(ticker="NVDA", holder="Vanguard Group Inc", pct_held=0.087),
        CompanyHolder(ticker="AAPL", holder="Other", pct_held=0.9),
    ])
    got = db.get_company_holders_for(conn, "NVDA")
    assert [h.holder for h in got] == ["Vanguard Group Inc", "Small Fund"]


# ---- a source that can't fetch must not report success ----

class _FakeResponse:
    def __init__(self, text="", payload=None, status=200):
        self.text = text
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeClient:
    """Hands out a crumb, then fails every quoteSummary request."""

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **kw):
        if "getcrumb" in url:
            return _FakeResponse(text="abc123")
        return _FakeResponse(status=500)


def test_fetch_raises_when_every_ticker_fails(monkeypatch):
    monkeypatch.setattr(fundamentals_source.httpx, "Client", _FakeClient)
    with pytest.raises(RuntimeError, match="no fundamentals returned"):
        fundamentals_source.fetch(["NVDA", "AAPL"])


def test_fetch_with_no_tickers_is_not_an_error(monkeypatch):
    monkeypatch.setattr(fundamentals_source.httpx, "Client", _FakeClient)
    assert list(fundamentals_source.fetch([])) == []


def test_get_crumb_raises_on_an_html_response():
    class _HtmlClient(_FakeClient):
        def get(self, url, **kw):
            return _FakeResponse(text="<html>consent</html>")

    with pytest.raises(ValueError, match="did not issue a crumb"):
        fundamentals_source.get_crumb(_HtmlClient())


def test_company_names_prefer_the_profile_over_the_filing_name(conn):
    db.upsert_trades(conn, [InsiderTrade(
        accession="a1", ticker="NVDA", company="NVIDIA CORP", owner="O", role="CEO",
        transaction_date="2026-07-01", transaction_type="Buy", shares=1.0, value=1.0,
        filing_url="u", filed_at="2026-07-01",
    )])
    # only a filing name so far
    assert db.get_all_company_names(conn) == {"NVDA": "NVIDIA CORP"}
    # the properly-cased profile name wins once fundamentals has one
    db.upsert_fundamentals(conn, [_fundamentals(name="NVIDIA Corporation")])
    assert db.get_all_company_names(conn)["NVDA"] == "NVIDIA Corporation"
    # a ticker with neither is simply absent, so callers fall back to the symbol
    assert "TSLA" not in db.get_all_company_names(conn)


def test_get_trades_for_filters_by_ticker(conn):
    def trade(acc, ticker, filed):
        return InsiderTrade(
            accession=acc, ticker=ticker, company="C", owner="O", role="CEO",
            transaction_date=filed, transaction_type="Buy", shares=1.0, value=1.0,
            filing_url="u", filed_at=filed,
        )
    db.upsert_trades(conn, [
        trade("a1", "NVDA", "2026-07-01"),
        trade("a2", "AAPL", "2026-07-02"),
        trade("a3", "NVDA", "2026-07-03"),
    ])
    got = db.get_trades_for(conn, "NVDA")
    assert [t.accession for t in got] == ["a3", "a1"]  # newest first
    assert db.get_trades_for(conn, "TSLA") == []
