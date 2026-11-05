"""Transactions ledger: the one write path for positions and cash.

Every change to a position or a cash balance is a ledger row; `portfolio` and
`cash_balances` are materialized views of it, updated in the same SQLite
transaction (db.record_transaction). Manual edits, paper fills and (later)
live fills all come through `record`, so they share one set of rules.

The arithmetic here is pure, so `replay` can rebuild the materialized tables
from the ledger alone and tests can assert the two agree.
"""
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from app import currency, db
from app.models import Holding, Transaction

SIDES = ("BUY", "SELL", "ADJUST", "DEPOSIT", "WITHDRAW")
CASH_SIDES = ("DEPOSIT", "WITHDRAW")
ACCOUNTS = ("manual", "paper", "live", "alpaca")
# Each demo bot trades in an account of its own (app/bots.py).
BOT_ACCOUNT = re.compile(r"bot:\d+")
EPS = 1e-9


class LedgerError(ValueError):
    """A transaction the rules refuse (oversell, not enough cash, ...)."""


@dataclass(frozen=True)
class Effect:
    position: Holding | None   # the position after the row; None = closed / no position
    cash_delta: float          # in the row's currency
    realized: float | None
    flow: float


def apply(position: Holding | None, tx: Transaction, funded: bool) -> Effect:
    """What `tx` does to `position` and to cash. `funded` means a BUY is paid
    from cash and a SELL's proceeds go to cash; otherwise the money moved
    outside this account and counts as an external flow."""
    if tx.side in CASH_SIDES:
        delta = tx.qty if tx.side == "DEPOSIT" else -tx.qty
        return Effect(position, delta, None, delta)

    shares = position.shares if position else 0.0
    cost = position.avg_cost if position else 0.0
    added_at = position.added_at if position else tx.executed_at
    gross = tx.qty * tx.price

    if tx.side == "BUY":
        new_shares = shares + tx.qty
        avg = (shares * cost + gross) / new_shares
        after = Holding(ticker=tx.ticker, shares=new_shares, avg_cost=avg,
                        added_at=added_at, currency=tx.currency)
        if funded:
            return Effect(after, -(gross + tx.fees), None, 0.0)
        return Effect(after, 0.0, None, gross + tx.fees)

    if tx.side == "SELL":
        if tx.qty > shares + EPS:
            raise LedgerError(f"can't sell {tx.qty:g} {tx.ticker}: only {shares:g} held")
        left = shares - tx.qty
        after = None if left <= EPS else Holding(
            ticker=tx.ticker, shares=left, avg_cost=cost, added_at=added_at,
            currency=tx.currency)
        realized = (tx.price - cost) * tx.qty - tx.fees
        proceeds = gross - tx.fees
        if funded:
            return Effect(after, proceeds, realized, 0.0)
        return Effect(after, 0.0, realized, -proceeds)

    # ADJUST: set the position outright (a correction, not a trade). Its flow
    # values the share change at the new cost basis — the ledger has no market
    # price at that moment, and a correction is rare enough for that to do.
    after = None if tx.qty <= EPS else Holding(
        ticker=tx.ticker, shares=tx.qty, avg_cost=tx.price, added_at=added_at,
        currency=tx.currency)
    return Effect(after, 0.0, None, (tx.qty - shares) * tx.price)


def replay(txs: list[Transaction]) -> tuple[dict, dict]:
    """Rebuild ({(account, ticker): Holding}, {(account, currency): balance})
    from ledger rows alone, oldest first. Used to check the materialized
    tables never drift from the ledger."""
    positions: dict[tuple[str, str], Holding] = {}
    cash: dict[tuple[str, str], float] = {}
    for tx in sorted(txs, key=lambda t: t.id or 0):
        if tx.side not in CASH_SIDES:
            eff = apply(positions.get((tx.account, tx.ticker)), tx, funded=False)
            if eff.position is None:
                positions.pop((tx.account, tx.ticker), None)
            else:
                positions[(tx.account, tx.ticker)] = eff.position
        if tx.cash_delta:
            key = (tx.account, tx.currency)
            cash[key] = cash.get(key, 0.0) + tx.cash_delta
    return positions, cash


def _finite(name: str, value: float, minimum: float = 0.0, strict: bool = False) -> float:
    if value is None or not math.isfinite(value) or value < minimum or (strict and value <= minimum):
        raise LedgerError(f"{name} must be {'>' if strict else '>='} {minimum:g}")
    return float(value)


def build(
    user_id: int,
    *,
    ticker: str,
    side: str,
    qty: float,
    price: float = 1.0,
    currency_code: str,
    fees: float = 0.0,
    account: str = "manual",
    executed_at: str | None = None,
    source: str = "manual",
    order_id: int | None = None,
) -> Transaction:
    """Validate and normalize one ledger row without writing it (for callers
    that write it inside their own transaction, like an order fill). Raises
    LedgerError for anything the rules refuse."""
    if side not in SIDES:
        raise LedgerError(f"side must be one of {', '.join(SIDES)}")
    if account not in ACCOUNTS and not BOT_ACCOUNT.fullmatch(account):
        raise LedgerError(f"account must be one of {', '.join(ACCOUNTS)} or bot:<id>")
    qty = _finite("quantity", qty, strict=side != "ADJUST")
    price = _finite("price", price)
    fees = _finite("fees", fees)
    price, ccy = currency.normalize_price(price, currency_code)
    if side in CASH_SIDES:
        qty, price, ticker = qty * price, 1.0, ccy
    tx = Transaction(
        user_id=user_id, account=account, ticker=ticker, side=side, qty=qty, price=price,
        fees=fees, currency=ccy,
        executed_at=executed_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source=source, order_id=order_id,
    )
    return tx


def record(conn, user_id: int, *, funded: bool = False, **fields) -> Transaction:
    """Validate, normalize and write one ledger row (plus the position and cash
    it moves) atomically. Raises LedgerError for anything the rules refuse."""
    return db.record_transaction(conn, build(user_id, **fields), funded)
