"""Cost-basis lots, what a sale would realize, wash sales and tax-loss
harvesting, rebuilt from the transaction ledger.

Ported from trader-tax/lots.py in Jake Schincariol's trader-skill (MIT,
github.com/Jakeschincariol/trader-skill; THIRD_PARTY_NOTICES.md).

The ledger itself keeps an average cost. Here every BUY is a lot of its own:
- Past sales consume lots first in, first out. The ledger never recorded
  which lots a sale used, and FIFO is what most brokers default to.
- An ADJUST (an edit that set a position outright) replaces the lots with one
  at the edited cost, dated the day of the edit and marked approximate,
  because when those shares were bought isn't known.

The rules applied (US federal; the arithmetic only, not tax advice):
- term: long if sold more than one year after purchase, else short
- lot methods: fifo, hifo (highest cost first), specific
- wash sale: a loss is disallowed to the extent the same or a substantially
  identical stock (GOOG/GOOGL, BRK-A/BRK-B... count as one) is bought within
  30 days before or after the sale. Real accounts are checked together;
  paper and bot accounts are simulated and never count.
"""
from dataclasses import dataclass, replace
from datetime import date, timedelta

from app.models import Transaction

FAMILIES = [{"GOOGL", "GOOG"}, {"BRK-A", "BRK-B"}, {"FOXA", "FOX"}, {"NWSA", "NWS"},
            {"LLYVA", "LLYVK"}, {"LEN", "LEN-B"}, {"UA", "UAA"}, {"BF-A", "BF-B"}, {"HEI", "HEI-A"},
            {"ZG", "Z"}]
REAL_ACCOUNTS = ("manual", "live")
METHODS = ("fifo", "hifo", "specific")
WASH_DAYS = 30
EPS = 1e-9
NOT_ADVICE = ("Not tax advice: arithmetic on the lots in your ledger. Wash-sale rules also count buys in your IRA, "
              "your spouse's accounts and other brokers, which this can't see.")


class TaxError(ValueError):
    pass


@dataclass(frozen=True)
class Lot:
    id: str
    ticker: str
    acquired: date
    qty: float
    per_share: float      # cost basis per share, fees included
    exact: bool           # False: an edit set this position; its purchase date isn't known


def family(ticker: str) -> set[str]:
    return next((f for f in FAMILIES if ticker in f), {ticker})


def long_term_from(d: date) -> date:
    """The first anniversary: a sale after it is long term."""
    try:
        return d.replace(year=d.year + 1)
    except ValueError:
        return d.replace(year=d.year + 1, day=28)


def term(acquired: date, sold: date) -> str:
    return "long" if sold > long_term_from(acquired) else "short"


def pick(lots: list[Lot], qty: float, method: str = "fifo", ids: list[str] | None = None) -> list[tuple[Lot, float]]:
    """[(lot, shares taken)] for selling `qty` by the lot method."""
    held = sum(lot.qty for lot in lots)
    if qty > held + EPS:
        raise TaxError(f"selling {qty:g} shares but only {held:g} are held")
    if method == "fifo":
        order = lots
    elif method == "hifo":
        order = sorted(lots, key=lambda lot: -lot.per_share)
    elif method == "specific":
        by_id = {lot.id: lot for lot in lots}
        unknown = [i for i in ids or [] if i not in by_id]
        if not ids or unknown:
            raise TaxError(f"name the lots to sell from: {', '.join(by_id)}")
        order = [by_id[i] for i in ids]
        if qty > sum(lot.qty for lot in order) + EPS:
            raise TaxError(f"the named lots hold fewer than {qty:g} shares")
    else:
        raise TaxError(f"lot method is one of: {', '.join(METHODS)}")
    out, left = [], qty
    for lot in order:
        if left <= EPS:
            break
        take = min(left, lot.qty)
        out.append((lot, take))
        left -= take
    return out


def build(txs: list[Transaction]) -> tuple[dict[str, list[Lot]], list[dict]]:
    """({ticker: open lots}, [realized lot sales]) from ledger rows, oldest first."""
    lots: dict[str, list[Lot]] = {}
    realized: list[dict] = []
    counter: dict[str, int] = {}

    def new_lot(t, when, qty, per_share, exact):
        counter[t] = counter.get(t, 0) + 1
        return Lot(f"{t}-{counter[t]}", t, when, qty, per_share, exact)

    for tx in txs:
        if tx.side not in ("BUY", "SELL", "ADJUST"):
            continue
        t, when = tx.ticker, date.fromisoformat(tx.executed_at[:10])
        if tx.side == "BUY":
            lots.setdefault(t, []).append(new_lot(t, when, tx.qty, tx.price + tx.fees / tx.qty, True))
        elif tx.side == "ADJUST":
            lots[t] = [new_lot(t, when, tx.qty, tx.price, False)] if tx.qty > EPS else []
        else:
            fee_per = tx.fees / tx.qty
            taken = pick(lots.get(t, []), tx.qty)
            for lot, q in taken:
                realized.append({"ticker": t, "currency": tx.currency, "lot": lot.id, "acquired": lot.acquired.isoformat(),
                                 "sold": when.isoformat(), "qty": q, "proceeds": q * (tx.price - fee_per),
                                 "basis": q * lot.per_share, "term": term(lot.acquired, when), "exact": lot.exact})
            used = {lot.id: q for lot, q in taken}
            lots[t] = [replace(lot, qty=lot.qty - used.get(lot.id, 0.0)) for lot in lots.get(t, [])
                       if lot.qty - used.get(lot.id, 0.0) > EPS]
    return {t: v for t, v in lots.items() if v}, realized


def simulate_sale(lots: list[Lot], qty: float, price: float, sold: date, method: str = "fifo",
                  ids: list[str] | None = None) -> dict:
    used, out = [], {"proceeds": 0.0, "basis": 0.0, "short": 0.0, "long": 0.0}
    for lot, take in pick(lots, qty, method, ids):
        basis, proceeds, tm = lot.per_share * take, price * take, term(lot.acquired, sold)
        used.append({"id": lot.id, "acquired": lot.acquired.isoformat(), "qty": take, "basis": round(basis, 2),
                     "proceeds": round(proceeds, 2), "gain": round(proceeds - basis, 2), "term": tm,
                     "exact": lot.exact})
        out["proceeds"] += proceeds
        out["basis"] += basis
        out[tm] += proceeds - basis
    out = {k: round(v, 2) for k, v in out.items()}
    return {**out, "gain": round(out["proceeds"] - out["basis"], 2), "lots": used}


def rebuy_date(sold: date) -> str:
    """The first day buying back doesn't make a loss on this sale a wash sale."""
    return (sold + timedelta(days=WASH_DAYS + 1)).isoformat()


def wash_hits(ticker: str, sold: date, used: list[dict], lots: dict[str, list[Lot]]) -> list[dict]:
    """Shares of the family bought within 30 days before the sale and still
    held after it: they would make a loss on this sale a wash sale."""
    taken = {u["id"]: u["qty"] for u in used}
    lo = sold - timedelta(days=WASH_DAYS)
    return [{"ticker": t, "lot": lot.id, "acquired": lot.acquired.isoformat(), "qty": lot.qty - taken.get(lot.id, 0)}
            for t in family(ticker) for lot in lots.get(t, [])
            if lot.qty - taken.get(lot.id, 0) > EPS and lo <= lot.acquired <= sold]


def disallowed(loss: float, qty_sold: float, hits: list[dict]) -> float:
    """The loss disallowed: in proportion to the replacement shares."""
    if loss >= 0 or not hits or not qty_sold:
        return 0.0
    return round(-loss * min(1.0, sum(h["qty"] for h in hits) / qty_sold), 2)


def harvest(lots: dict[str, list[Lot]], prices: dict[str, float], today: date, min_loss: float = 100.0,
            wash_lots: dict[str, list[Lot]] | None = None) -> list[dict]:
    """Tax-loss candidates: the losing lots of each position, if together they
    lose at least `min_loss`, with any wash-sale exposure."""
    out = []
    for t, held in sorted(lots.items()):
        p = prices.get(t)
        losers = [lot for lot in held if p and lot.qty * p < lot.qty * lot.per_share - 0.005]
        if not losers:
            continue
        sale = simulate_sale(held, sum(lot.qty for lot in losers), p, today, "specific", [lot.id for lot in losers])
        if -sale["gain"] < min_loss:
            continue
        hits = wash_hits(t, today, sale["lots"], wash_lots if wash_lots is not None else lots)
        out.append({"ticker": t, "price": p, "qty": round(sum(lot.qty for lot in losers), 6),
                    "lots": [lot.id for lot in losers], "loss": sale["gain"], "short": sale["short"],
                    "long": sale["long"], "wash": hits,
                    "disallowed": disallowed(sale["gain"], sum(lot.qty for lot in losers), hits),
                    "rebuy_on_or_after": rebuy_date(today)})
    return sorted(out, key=lambda c: c["loss"])


def realized_by_year(realized: list[dict], year: int) -> dict:
    """Realized gains of `year` by term, per currency (never added across)."""
    rows = [r for r in realized if r["sold"].startswith(str(year))]
    by: dict[str, dict] = {}
    for r in rows:
        b = by.setdefault(r["currency"], {"short": 0.0, "long": 0.0})
        b[r["term"]] += r["proceeds"] - r["basis"]
    return {"year": year, "by_currency": {c: {k: round(v, 2) for k, v in b.items()} for c, b in by.items()},
            "sales": len(rows), "approximate": any(not r["exact"] for r in rows)}
