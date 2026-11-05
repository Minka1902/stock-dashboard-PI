"""Request input validation helpers.

Ticker strings arrive from clients and end up interpolated into outbound
Yahoo/GDELT URLs, so they are strictly whitelisted here before any route
touches them.
"""
import math
import re

from fastapi import HTTPException

# Uppercase symbols like AAPL, BRK.B, BF-B, ^GSPC, EURUSD=X. Max 12 chars.
TICKER_RE = re.compile(r"[A-Z0-9^][A-Z0-9.\-^=]{0,11}")


def clean_ticker(raw: str) -> str:
    """Normalize and validate a ticker; raise 400 on anything suspicious."""
    ticker = (raw or "").strip().upper()
    # fullmatch (not match+$): "$" would accept a trailing newline.
    if not TICKER_RE.fullmatch(ticker):
        raise HTTPException(status_code=400, detail="invalid ticker symbol")
    return ticker


EXIT_FIELDS = ("stop_price", "stop_pct", "take_profit", "trailing_pct")
STOP_PCT_RANGE = (0.5, 90.0)
TRAILING_PCT_RANGE = (0.5, 50.0)


def _positive(raw) -> float | None | str:
    """None for blank, a finite float > 0, or an error message."""
    if raw is None or raw == "":
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return "Enter a number"
    if not math.isfinite(v) or v <= 0:
        return "Enter a number above 0"
    return v


def validate_exit_rule(body: dict, price: float, avg_cost: float) -> tuple[dict, dict]:
    """(clean levels, {}) or ({}, {field: message}) for a long position's exit
    rule against the current `price`. A stop must sit below the price and a
    take-profit above it, or the rule would fire the moment it's saved."""
    errors: dict[str, str] = {}
    clean: dict = {"active": bool(body.get("active", True))}
    for f in EXIT_FIELDS:
        v = _positive(body.get(f))
        if isinstance(v, str):
            errors[f] = v
        else:
            clean[f] = v
    if errors:
        return {}, errors
    if clean["stop_price"] is not None and clean["stop_pct"] is not None:
        errors["stop_pct"] = "Use a stop price or a % below your cost, not both"
    lo, hi = STOP_PCT_RANGE
    if clean["stop_pct"] is not None and not lo <= clean["stop_pct"] <= hi:
        errors["stop_pct"] = f"Between {lo:g}% and {hi:g}%"
    lo, hi = TRAILING_PCT_RANGE
    if clean["trailing_pct"] is not None and not lo <= clean["trailing_pct"] <= hi:
        errors["trailing_pct"] = f"Between {lo:g}% and {hi:g}%"
    stop = clean["stop_price"] if clean["stop_price"] is not None else (
        avg_cost * (1 - clean["stop_pct"] / 100) if clean["stop_pct"] is not None else None)
    if stop is not None and stop >= price and "stop_pct" not in errors:
        field = "stop_price" if clean["stop_price"] is not None else "stop_pct"
        errors[field] = f"The stop ({stop:,.2f}) must be below the current price ({price:,.2f})"
    if clean["take_profit"] is not None and clean["take_profit"] <= price:
        errors["take_profit"] = f"The take-profit must be above the current price ({price:,.2f})"
    if all(clean[f] is None for f in EXIT_FIELDS):
        errors["stop_price"] = "Set a stop, a take-profit or a trailing stop"
    return ({}, errors) if errors else (clean, {})
