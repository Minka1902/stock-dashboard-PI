"""Yahoo search parsing + caching."""
import pytest

from app import search

PAYLOAD = {
    "quotes": [
        {"symbol": "MSFT", "longname": "Microsoft Corporation", "shortname": "Microsoft",
         "exchDisp": "NASDAQ", "quoteType": "EQUITY"},
        {"symbol": "msft.mx", "shortname": "Microsoft (Mexico)", "exchange": "MEX",
         "quoteType": "EQUITY"},
        {"symbol": "MSFT230C", "shortname": "some option", "quoteType": "OPTION"},
        {"symbol": "", "shortname": "broken row", "quoteType": "EQUITY"},
        {"symbol": "SPY", "shortname": "SPDR S&P 500", "exchDisp": "NYSEArca",
         "quoteType": "ETF"},
        {"symbol": "^GSPC", "shortname": "S&P 500", "quoteType": "INDEX"},
        {"symbol": "BTC-USD", "shortname": "Bitcoin", "quoteType": "CRYPTOCURRENCY"},
    ]
}


def test_parse_results_filters_and_shapes():
    rows = search.parse_results(PAYLOAD)
    symbols = [r["symbol"] for r in rows]
    assert symbols == ["MSFT", "MSFT.MX", "SPY", "^GSPC"]  # options/crypto/empty dropped
    assert rows[0] == {
        "symbol": "MSFT", "name": "Microsoft Corporation",
        "exchange": "NASDAQ", "type": "EQUITY", "market": "US", "currency": "USD",
    }
    # longname preferred, shortname fallback, exchange fallback
    assert rows[1]["name"] == "Microsoft (Mexico)"
    assert rows[1]["exchange"] == "MEX"


def test_parse_results_respects_limit_and_empty():
    assert search.parse_results(PAYLOAD, limit=1) == [{
        "symbol": "MSFT", "name": "Microsoft Corporation",
        "exchange": "NASDAQ", "type": "EQUITY", "market": "US", "currency": "USD",
    }]
    assert search.parse_results({}) == []


def test_search_caches_per_query(monkeypatch):
    calls = []

    def stub_fetch(query, limit=8):
        calls.append(query)
        return [{"symbol": "MSFT", "name": "Microsoft", "exchange": "", "type": "EQUITY"}]

    monkeypatch.setattr(search, "fetch", stub_fetch)
    search._cache.clear()
    assert search.search("micro")[0]["symbol"] == "MSFT"
    assert search.search("  MICRO ")[0]["symbol"] == "MSFT"  # normalized key hits cache
    assert calls == ["micro"]
    search._cache.clear()


def test_search_does_not_cache_empty(monkeypatch):
    calls = []

    def stub_fetch(query, limit=8):
        calls.append(query)
        return []

    monkeypatch.setattr(search, "fetch", stub_fetch)
    search._cache.clear()
    assert search.search("zzz") == []
    assert search.search("zzz") == []
    assert len(calls) == 2  # transient failures don't stick


# ---------- market filter (All / US / TASE) ----------

MIXED = [
    {"symbol": "TEVA", "name": "Teva", "exchange": "NYSE", "type": "EQUITY",
     "market": "US", "currency": "USD"},
    {"symbol": "TEVA.TA", "name": "Teva", "exchange": "Tel Aviv", "type": "EQUITY",
     "market": "TASE", "currency": "ILS"},
    {"symbol": "EB2.F", "name": "Elbit", "exchange": "Frankfurt", "type": "EQUITY",
     "market": "OTHER", "currency": None},
]


def test_parse_results_tags_tase_market_and_shekel_currency():
    rows = search.parse_results({"quotes": [
        {"symbol": "TEVA.TA", "shortname": "TEVA", "exchDisp": "Tel Aviv", "quoteType": "EQUITY"},
        {"symbol": "EB2.F", "shortname": "ELBIT", "exchDisp": "Frankfurt", "quoteType": "EQUITY"},
    ]})
    assert (rows[0]["market"], rows[0]["currency"]) == ("TASE", "ILS")
    assert (rows[1]["market"], rows[1]["currency"]) == ("OTHER", None)


@pytest.mark.parametrize("market,expected", [
    ("all", ["TEVA", "TEVA.TA", "EB2.F"]),
    ("us", ["TEVA"]),
    ("tase", ["TEVA.TA"]),
])
def test_search_filters_by_market(monkeypatch, market, expected):
    monkeypatch.setattr(search, "fetch", lambda q, limit=8: list(MIXED))
    search._cache.clear()
    assert [r["symbol"] for r in search.search("teva", market=market)] == expected
    search._cache.clear()


def test_search_market_filter_reuses_one_upstream_fetch(monkeypatch):
    calls = []

    def stub(q, limit=8):
        calls.append((q, limit))
        return list(MIXED)

    monkeypatch.setattr(search, "fetch", stub)
    search._cache.clear()
    search.search("teva", market="us")
    search.search("teva", market="tase")
    search.search("teva")
    assert len(calls) == 1
    search._cache.clear()


def test_search_rejects_unknown_market():
    with pytest.raises(ValueError):
        search.search("teva", market="nyse")
