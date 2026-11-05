"""Currency helpers: minor-unit normalization (ILA agorot -> ILS), symbol ->
currency/market mapping, and FX conversion that never invents a rate."""
import pytest

from app import currency
from app.models import LiveQuote


# ---- minor units ----

def test_normalize_price_divides_agorot_into_shekels():
    assert currency.normalize_price(12050.0, "ILA") == (120.5, "ILS")


def test_normalize_price_passes_major_units_through():
    assert currency.normalize_price(341.07, "USD") == (341.07, "USD")
    assert currency.normalize_price(4242.12, "ILS") == (4242.12, "ILS")


def test_normalize_price_handles_pence_and_missing_values():
    assert currency.normalize_price(250.0, "GBp") == (2.5, "GBP")
    assert currency.normalize_price(None, "ILA") == (None, "ILS")
    assert currency.normalize_price(10.0, None) == (10.0, None)


def test_quote_currency_reads_meta_and_maps_minor_units():
    assert currency.quote_currency({"currency": "ILA"}) == "ILS"
    assert currency.quote_currency({"currency": "usd"}) == "USD"
    assert currency.quote_currency({}) is None
    assert currency.quote_currency(None) is None


def _chart_payload(ccy="ILA"):
    return {"chart": {"result": [{
        "meta": {"currency": ccy, "regularMarketPrice": 12050.0,
                 "chartPreviousClose": 11900.0, "previousClose": 11900.0,
                 "fiftyTwoWeekHigh": 13000.0, "fiftyTwoWeekLow": 9000.0,
                 "regularMarketDayHigh": 12100.0, "regularMarketDayLow": 11800.0},
        "timestamp": [1, 2],
        "indicators": {
            "quote": [{"open": [11900.0, None], "high": [12100.0, 12200.0],
                       "low": [11800.0, 12000.0], "close": [12000.0, 12050.0],
                       "volume": [1000, 2000]}],
            "adjclose": [{"adjclose": [11990.0, 12040.0]}],
        },
    }]}}


def test_normalize_chart_payload_scales_bars_and_meta():
    p = currency.normalize_chart_payload(_chart_payload())
    r = p["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    assert q["close"] == [120.0, 120.5]
    assert q["open"] == [119.0, None]          # gaps stay gaps
    assert q["volume"] == [1000, 2000]         # share counts are not money
    assert r["indicators"]["adjclose"][0]["adjclose"] == [119.9, 120.4]
    meta = r["meta"]
    assert meta["regularMarketPrice"] == 120.5
    assert meta["chartPreviousClose"] == 119.0
    assert meta["fiftyTwoWeekHigh"] == 130.0
    assert meta["currency"] == "ILS"
    assert meta["priceCurrencySource"] == "ILA"


def test_normalize_chart_payload_is_idempotent_and_leaves_usd_alone():
    once = currency.normalize_chart_payload(_chart_payload())
    twice = currency.normalize_chart_payload(once)
    assert twice["chart"]["result"][0]["indicators"]["quote"][0]["close"] == [120.0, 120.5]
    usd = currency.normalize_chart_payload(_chart_payload("USD"))
    assert usd["chart"]["result"][0]["indicators"]["quote"][0]["close"] == [12000.0, 12050.0]


def test_normalize_chart_payload_tolerates_empty_payloads():
    assert currency.normalize_chart_payload({}) == {}
    assert currency.normalize_chart_payload({"chart": {"result": None}}) == {"chart": {"result": None}}


# ---- symbol conventions ----

@pytest.mark.parametrize("sym,market,ccy", [
    ("AAPL", "US", "USD"),
    ("BRK-B", "US", "USD"),
    ("TEVA.TA", "TASE", "ILS"),
    ("^TA125.TA", "TASE", "ILS"),
    ("USDILS=X", "FX", "ILS"),
    ("EURUSD=X", "FX", "USD"),
    ("VOD.L", "OTHER", None),
])
def test_market_and_currency_for_symbol(sym, market, ccy):
    assert currency.market_for_symbol(sym) == market
    assert currency.currency_for_symbol(sym) == ccy


def test_valid_fx_pair():
    assert currency.is_valid_fx_pair("USDILS=X")
    assert currency.is_valid_fx_pair("EURUSD=X")
    assert not currency.is_valid_fx_pair("USDUSD=X")   # same currency twice
    assert not currency.is_valid_fx_pair("ABCXYZ=X")   # not ISO 4217
    assert not currency.is_valid_fx_pair("USDILS")     # missing Yahoo suffix
    assert not currency.is_valid_fx_pair("usdils=x")   # must already be upper


# ---- conversion ----

def _q(ticker, price):
    return LiveQuote(ticker=ticker, price=price, change_pct=None, previous_close=None,
                     market_state="LIVE", fetched_at="t")


def test_fx_rate_same_currency_is_one_without_a_lookup():
    def boom(_):
        raise AssertionError("no lookup expected")
    assert currency.fx_rate("USD", "USD", getter=boom) == 1.0


def test_fx_rate_direct_pair():
    got = currency.fx_rate("USD", "ILS", getter=lambda syms: [_q(s, 3.05) for s in syms])
    assert got == pytest.approx(3.05)


def test_fx_rate_falls_back_to_the_inverse_pair():
    def getter(syms):
        # Only the USDILS pair quotes; ILSUSD=X is "missing".
        return [_q(s, 3.05) for s in syms if s == "USDILS=X"]
    got = currency.fx_rate("ILS", "USD", getter=getter)
    assert got == pytest.approx(1 / 3.05)


def test_fx_rate_missing_is_none_never_a_guess():
    assert currency.fx_rate("USD", "ILS", getter=lambda syms: []) is None
    assert currency.fx_rate("USD", "ILS", getter=lambda syms: [_q(s, 0.0) for s in syms]) is None

    def broken(_):
        raise RuntimeError("yahoo down")
    assert currency.fx_rate("USD", "ILS", getter=broken) is None


def test_convert_uses_the_rate_or_returns_none():
    getter = lambda syms: [_q(s, 3.0) for s in syms if s == "USDILS=X"]  # noqa: E731
    assert currency.convert(10.0, "USD", "ILS", getter=getter) == pytest.approx(30.0)
    assert currency.convert(30.0, "ILS", "USD", getter=getter) == pytest.approx(10.0)
    assert currency.convert(10.0, "USD", "EUR", getter=lambda s: []) is None


def test_rates_to_base_reports_what_is_missing():
    getter = lambda syms: [_q(s, 3.0) for s in syms if s == "USDILS=X"]  # noqa: E731
    out = currency.rates_to_base("ILS", ["USD", "ILS", "EUR"], getter=getter)
    assert out["base"] == "ILS"
    assert out["rates"]["ILS"] == 1.0
    assert out["rates"]["USD"] == pytest.approx(3.0)
    assert out["rates"]["EUR"] is None
    assert out["unavailable"] == ["EUR"]
