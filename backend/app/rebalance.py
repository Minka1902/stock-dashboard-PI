"""Rebalance an account back to its target weights.

Ported from trader-rebalance/rebalance.py in Jake Schincariol's trader-skill
(MIT, github.com/Jakeschincariol/trader-skill; THIRD_PARTY_NOTICES.md).

The rules:
- value: the account is its cash plus every holding at its price, all in the
  base currency.
- targets: a % of the account per ticker; they may sum to under 100, and the
  rest stays in cash.
- drift: the current % minus the target %, in points. Only a stock drifting
  more than the band trades, and it goes all the way back to its target.
- sells come first, so their cash is there. Buys spend what cash remains; if
  it isn't enough, every buy is scaled down by the same share.
- whole shares only, and a trade under `min_trade` is skipped and said so.
- a holding without a target is left alone and listed as untracked.
- the proposals are never placed: each goes through the order ticket and
  its guardrails like any other order.
"""
import math

EPS = 1e-9


def plan(holdings: dict[str, float], prices: dict[str, float], fx: dict[str, float], ccy: dict[str, str],
         cash_base: float, targets: dict[str, float], band: float = 5.0, min_trade: float = 50.0) -> dict:
    """holdings {ticker: shares}, prices {ticker: native price}, fx {currency:
    multiplier to base}, ccy {ticker: currency}, cash in base, targets {ticker: %}."""
    tickers = sorted(set(holdings) | set(targets), key=lambda t: (-targets.get(t, 0.0), t))
    missing = [t for t in tickers if not prices.get(t) or fx.get(ccy[t]) is None]
    if missing:
        raise ValueError(f"no price or exchange rate for {', '.join(missing)}")
    base_px = {t: prices[t] * fx[ccy[t]] for t in tickers}
    value = {t: holdings.get(t, 0.0) * base_px[t] for t in tickers}
    total = cash_base + sum(value.values())
    if total <= 0:
        raise ValueError("the account is empty: nothing to rebalance")
    rows, sells, buys, notes = [], [], [], []
    for t in tickers:
        cur = value[t] / total * 100
        tracked = t in targets
        tgt = targets.get(t, 0.0)
        drift = cur - tgt
        row = {"ticker": t, "currency": ccy[t], "shares": holdings.get(t, 0.0), "price": prices[t],
               "value": round(value[t], 2), "current_pct": round(cur, 2), "target_pct": tgt if tracked else None,
               "drift": round(drift, 2) if tracked else None,
               "action": "hold" if tracked else "untracked, left alone"}
        rows.append(row)
        if not tracked or abs(drift) <= band + EPS:
            continue
        delta = tgt / 100 * total - value[t]
        why = f"drifted {drift:+.1f} points from its {tgt:g}% target (band {band:g})"
        (sells if delta < 0 else buys).append((t, abs(delta), why))
        row["action"] = "sell" if delta < 0 else "buy"

    orders, cash_after = [], cash_base
    for t, amount, why in sells:
        qty = min(math.floor(amount / base_px[t] + EPS), holdings.get(t, 0.0))
        if qty * base_px[t] < max(min_trade, EPS):
            notes.append(f"{t}: a sell of {qty * base_px[t]:,.0f} is under the {min_trade:,.0f} minimum, skipped")
            continue
        cash_after += qty * base_px[t]
        orders.append({"ticker": t, "side": "SELL", "qty": qty, "value": round(qty * base_px[t], 2), "why": why})
    want = sum(a for _, a, _ in buys)
    scale = min(1.0, cash_after / want) if want else 1.0
    if scale < 1 - EPS:
        notes.append(f"buys scaled to {scale:.0%}: only {cash_after:,.0f} is free after the sells")
    for t, amount, why in buys:
        qty = math.floor(amount * scale / base_px[t] + EPS)
        if qty < 1 or qty * base_px[t] < min_trade:
            notes.append(f"{t}: a buy of {amount * scale:,.0f} doesn't reach one share or the "
                         f"{min_trade:,.0f} minimum, skipped")
            continue
        orders.append({"ticker": t, "side": "BUY", "qty": qty, "value": round(qty * base_px[t], 2), "why": why})
    spent = sum(o["value"] for o in orders if o["side"] == "BUY")
    return {"account_value": round(total, 2), "cash": round(cash_base, 2), "band": band, "min_trade": min_trade,
            "target_total": round(sum(targets.values()), 2), "rows": rows, "orders": orders, "notes": notes,
            "cash_after": round(cash_after - spent, 2)}
