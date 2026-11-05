"""The one cost model for every simulated fill and every scored outcome (STC-21).

A fill pays config.COST_BPS_PER_SIDE of the price on each side, plus half the
bid/ask spread when one is known — never an invented spread. The track
record, the demo bots, paper orders and the bar backtester all call this, so
a strategy can't look better in one place than another.
"""
from app import config


def apply(price: float, side: str, bps: float | None = None, half_spread: float | None = None) -> float:
    """The price after costs: a BUY pays up, a SELL receives less."""
    bps = config.COST_BPS_PER_SIDE if bps is None else bps
    cost = price * bps / 10_000 + (half_spread or 0.0)
    return round(price + cost if side == "BUY" else price - cost, 4)


def round_trip(entry: float, exit: float, direction: str = "long", bps: float | None = None) -> float:
    """A position's return in %, after paying costs on the way in and out.
    Long: buy at entry, sell at exit. Short: sell at entry, buy back at exit."""
    if direction == "short":
        return (apply(entry, "SELL", bps) / apply(exit, "BUY", bps) - 1) * 100
    return (apply(exit, "SELL", bps) / apply(entry, "BUY", bps) - 1) * 100
