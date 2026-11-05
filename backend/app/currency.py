"""Currencies: minor-unit normalization, symbol conventions and FX conversion.

The one rule that matters most: **Yahoo quotes Tel Aviv listings in ILA
(agorot, 1/100 of a shekel)**. A TEVA.TA price of 12050 means ₪120.50. Every
place a Yahoo chart payload enters the app runs it through
`normalize_chart_payload` (or a scalar through `normalize_price`) so that
prices, bars, stored OHLC, technical levels and analysis are all in the major
unit (ILS) and nothing downstream has to know agorot exist.

FX conversion never invents a rate. `fx_rate` returns None when neither the
direct nor the inverse Yahoo pair quotes, and callers must show "FX
unavailable" and leave that amount out of any converted total.
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Iterable

logger = logging.getLogger(__name__)

# Yahoo minor-unit currency codes -> (major ISO code, divisor).
_MINOR_UNITS: dict[str, tuple[str, int]] = {
    "ILA": ("ILS", 100),  # Israeli agorot (all TASE equities)
    "GBp": ("GBP", 100),  # London pence
    "GBX": ("GBP", 100),
    "ZAc": ("ZAR", 100),  # Johannesburg cents
}

# Money-valued meta fields on a Yahoo v8 chart payload.
_META_PRICE_FIELDS = (
    "regularMarketPrice", "chartPreviousClose", "previousClose",
    "regularMarketDayHigh", "regularMarketDayLow",
    "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
)

# ISO 4217 codes accepted for FX watch pairs. Deliberately a curated list of
# currencies Yahoo actually quotes as "<A><B>=X", not every code in the
# standard — a typo like "USDLIS=X" should be rejected, not silently empty.
ISO_CURRENCIES: tuple[str, ...] = (
    "USD", "EUR", "ILS", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "CNY",
    "HKD", "SGD", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "TRY", "ZAR",
    "MXN", "BRL", "INR", "KRW", "TWD", "THB", "AED", "SAR", "RUB",
)
_ISO_SET = frozenset(ISO_CURRENCIES)

# Display symbols for the currencies the UI shows most. Anything else renders
# as its ISO code (Intl.NumberFormat does the same on the frontend).
SYMBOLS: dict[str, str] = {
    "USD": "$", "ILS": "₪", "EUR": "€", "GBP": "£", "JPY": "¥",
}

# Base currencies a user can total their portfolio in.
BASE_CURRENCIES: tuple[str, ...] = ("USD", "ILS")

_PAIR_RE = re.compile(r"^([A-Z]{3})([A-Z]{3})=X$")


# ---------- minor units ----------

def _major(code: str | None) -> tuple[str | None, int]:
    if not code:
        return None, 1
    if code in _MINOR_UNITS:
        return _MINOR_UNITS[code]
    return code.upper(), 1


def quote_currency(meta: dict | None) -> str | None:
    """The major-unit ISO currency of a Yahoo chart `meta` ("ILA" -> "ILS")."""
    if not isinstance(meta, dict):
        return None
    raw = meta.get("currency")
    if not isinstance(raw, str) or not raw.strip():
        return None
    return _major(raw.strip())[0]


def normalize_price(value, currency: str | None):
    """(value, major_currency) with minor units divided out.

    `normalize_price(12050, "ILA") == (120.5, "ILS")`. None stays None; an
    unknown or missing currency passes the value through untouched.
    """
    major, divisor = _major(currency)
    if value is None or not isinstance(value, (int, float)) or divisor == 1:
        return value, major
    return round(float(value) / divisor, 6), major


def _scale_list(values, divisor: int):
    if not isinstance(values, list):
        return values
    return [round(float(v) / divisor, 6) if isinstance(v, (int, float)) else v
            for v in values]


def normalize_chart_payload(payload: dict) -> dict:
    """Rewrite a Yahoo v8 chart payload in place so every price is in the
    major unit, and return it.

    Scales OHLC (not volume), adjclose and the meta price fields, then stamps
    `meta.currency` with the major code — which also makes a second call a
    no-op. The original code is kept in `meta.priceCurrencySource` so the
    conversion stays auditable.
    """
    try:
        results = (payload.get("chart") or {}).get("result") or []
    except AttributeError:
        return payload
    for r in results:
        if not isinstance(r, dict):
            continue
        meta = r.get("meta") or {}
        raw = meta.get("currency")
        if raw not in _MINOR_UNITS:
            continue
        major, divisor = _MINOR_UNITS[raw]
        for block in (r.get("indicators") or {}).get("quote") or []:
            for key in ("open", "high", "low", "close"):
                if key in block:
                    block[key] = _scale_list(block[key], divisor)
        for block in (r.get("indicators") or {}).get("adjclose") or []:
            if "adjclose" in block:
                block["adjclose"] = _scale_list(block["adjclose"], divisor)
        for key in _META_PRICE_FIELDS:
            v = meta.get(key)
            if isinstance(v, (int, float)):
                meta[key] = round(float(v) / divisor, 6)
        meta["currency"] = major
        meta["priceCurrencySource"] = raw
    return payload


# ---------- symbol conventions ----------

def is_fx_symbol(ticker: str) -> bool:
    return str(ticker or "").upper().endswith("=X")


def market_for_symbol(ticker: str) -> str:
    """"US" | "TASE" | "FX" | "OTHER" from Yahoo's symbol convention.

    A bare symbol (AAPL, BRK-B, ^GSPC) is a US listing; ".TA" is Tel Aviv;
    "=X" is a currency pair. Any other exchange suffix is "OTHER" — the app
    doesn't model those markets, so it says so rather than guessing.
    """
    t = str(ticker or "").strip().upper()
    if not t:
        return "OTHER"
    if t.endswith("=X"):
        return "FX"
    if t.endswith(".TA"):
        return "TASE"
    if "." in t:
        return "OTHER"
    return "US"


def currency_for_symbol(ticker: str) -> str | None:
    """Trading currency implied by the symbol, or None when the symbol alone
    can't say (a foreign suffix we don't model — ask Yahoo instead)."""
    market = market_for_symbol(ticker)
    if market == "US":
        return "USD"
    if market == "TASE":
        return "ILS"
    if market == "FX":
        m = _PAIR_RE.match(str(ticker).upper())
        return m.group(2) if m else None
    return None


def is_valid_fx_pair(pair: str) -> bool:
    """"<A><B>=X" with two distinct, known ISO codes (already upper-case)."""
    m = _PAIR_RE.match(pair or "")
    if not m:
        return False
    a, b = m.groups()
    return a != b and a in _ISO_SET and b in _ISO_SET


def symbol_for(code: str | None) -> str:
    """"₪" for ILS, "$" for USD; the ISO code plus a space otherwise."""
    if not code:
        return ""
    return SYMBOLS.get(code, f"{code} ")


# ---------- conversion ----------

QuoteGetter = Callable[[list[str]], list]


def _default_getter(symbols: list[str]) -> list:
    from app import quotes  # local import: quotes imports this module
    return quotes.get_quotes(symbols)


def _price_of(getter: QuoteGetter, symbol: str) -> float | None:
    try:
        got = getter([symbol])
    except Exception:
        logger.warning("FX lookup failed for %s", symbol, exc_info=True)
        return None
    for q in got or []:
        if getattr(q, "ticker", "").upper() == symbol and isinstance(q.price, (int, float)):
            return float(q.price) if q.price > 0 else None
    return None


def fx_rate(from_ccy: str, to_ccy: str, getter: QuoteGetter | None = None) -> float | None:
    """Multiplier turning an amount in `from_ccy` into `to_ccy`, or None.

    Tries Yahoo's direct pair ("USDILS=X" for USD->ILS), then the inverse pair
    (1 / "ILSUSD=X"). Missing either way returns None — never a stale default
    or a hardcoded rate.
    """
    f, t = (from_ccy or "").upper(), (to_ccy or "").upper()
    if not f or not t:
        return None
    if f == t:
        return 1.0
    getter = getter or _default_getter
    direct = _price_of(getter, f"{f}{t}=X")
    if direct:
        return direct
    inverse = _price_of(getter, f"{t}{f}=X")
    if inverse:
        return 1.0 / inverse
    return None


def convert(amount: float | None, from_ccy: str, to_ccy: str,
            getter: QuoteGetter | None = None) -> float | None:
    if amount is None:
        return None
    rate = fx_rate(from_ccy, to_ccy, getter)
    return amount * rate if rate is not None else None


def rates_to_base(base: str, currencies: Iterable[str],
                  getter: QuoteGetter | None = None) -> dict:
    """{base, rates: {ccy: multiplier-or-None}, unavailable: [ccy...]} to turn
    each currency into `base`. The portfolio totals use this; a None rate
    means that position is left out of the converted total, visibly."""
    base = (base or "USD").upper()
    rates: dict[str, float | None] = {}
    for c in dict.fromkeys(x.upper() for x in currencies if x):
        rates[c] = fx_rate(c, base, getter)
    rates.setdefault(base, 1.0)
    return {
        "base": base,
        "rates": rates,
        "unavailable": [c for c, r in rates.items() if r is None],
    }
