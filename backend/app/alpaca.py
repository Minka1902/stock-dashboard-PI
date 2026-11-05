"""Alpaca's Trading API, the first real broker (STC-55, STC-86).

Paper first:
- STOCKS_ALPACA_PAPER (default on) targets paper-api.alpaca.markets.
- Live is refused until the per-order TOTP step-up (STC-51) exists.

Keys are secrets, so they come from env only (STOCKS_ALPACA_KEY_ID /
STOCKS_ALPACA_SECRET_KEY). They travel in request headers, never URLs, and
errors are rebuilt from the status and Alpaca's message, never from a request.

One Alpaca account per instance: env keys can't be per user. It belongs to
STOCKS_ALPACA_OWNER (an email; default the first admin), and every other user
sees no broker. Per-user keys need secure storage (STC-48).

US listings only. This app trades cash-only: no margin, no shorting.
"""
import httpx

from app import config, db
from app.models import Order

ACCOUNT = "alpaca"
BASE = {"paper": "https://paper-api.alpaca.markets", "live": "https://api.alpaca.markets"}
# Alpaca order status -> ours.
STATUS = {"new": "submitted", "accepted": "submitted", "pending_new": "submitted", "accepted_for_bidding": "submitted",
          "held": "submitted", "pending_cancel": "submitted", "pending_replace": "submitted", "stopped": "submitted",
          "suspended": "submitted", "calculated": "submitted", "done_for_day": "submitted",
          "partially_filled": "partially_filled", "filled": "filled", "canceled": "cancelled", "expired": "cancelled",
          "replaced": "cancelled", "rejected": "rejected"}


class BrokerError(RuntimeError):
    pass


def configured() -> bool:
    return bool(config.ALPACA_KEY_ID and config.ALPACA_SECRET_KEY)


def mode() -> str:
    return "paper" if config.ALPACA_PAPER else "live"


def owner(conn) -> int | None:
    """The user the configured Alpaca account belongs to."""
    if not configured():
        return None
    users = db.get_users(conn)
    if config.ALPACA_OWNER:
        return next((u.id for u in users if u.email.lower() == config.ALPACA_OWNER.lower()), None)
    return next((u.id for u in sorted(users, key=lambda u: u.id) if u.is_admin), None)


def client() -> httpx.Client:
    return httpx.Client(base_url=BASE[mode()], timeout=15.0, headers={
        "APCA-API-KEY-ID": config.ALPACA_KEY_ID, "APCA-API-SECRET-KEY": config.ALPACA_SECRET_KEY})


def call(c: httpx.Client, method: str, path: str, **kw):
    """One API call. BrokerError carries the status and Alpaca's message only."""
    try:
        r = c.request(method, path, **kw)
    except httpx.HTTPError as exc:
        raise BrokerError(f"Alpaca {method} {path}: {type(exc).__name__}") from None
    if r.status_code >= 400:
        try:
            msg = r.json().get("message", "")
        except ValueError:
            msg = ""
        raise BrokerError(f"Alpaca refused ({r.status_code}): {msg}".strip())
    return r.json() if r.content else None


def symbol(ticker: str) -> str:
    """Yahoo's "BRK-B" is Alpaca's "BRK.B"."""
    return ticker.replace("-", ".")


def ticker(sym: str) -> str:
    return sym.replace(".", "-")


def payload(order: Order) -> dict:
    """The order request. A buy carrying both a plan stop and target goes as a
    bracket, so the exits live at the broker too (STC-59)."""
    body = {"symbol": symbol(order.ticker), "qty": f"{order.qty:g}", "side": order.side.lower(),
            "type": order.type, "time_in_force": order.tif, "client_order_id": f"sd-{order.id}"}
    if order.limit_price is not None:
        body["limit_price"] = f"{order.limit_price:g}"
    if order.stop_price is not None:
        body["stop_price"] = f"{order.stop_price:g}"
    if order.side == "BUY" and order.plan_stop and order.plan_target and order.type in ("market", "limit"):
        body.update({"order_class": "bracket", "take_profit": {"limit_price": f"{order.plan_target:g}"},
                     "stop_loss": {"stop_price": f"{order.plan_stop:g}"}})
    return body


def checks(account: dict, configuration: dict) -> list[dict]:
    """What the connection is allowed to do, each {ok, text}."""
    margin = str(configuration.get("max_margin_multiplier", "1"))
    return [
        {"ok": account.get("status") == "ACTIVE", "text": f"account {account.get('status', 'unknown').lower()}"},
        {"ok": not account.get("trading_blocked") and not account.get("account_blocked"),
         "text": "trading allowed" if not account.get("trading_blocked") else "trading is blocked by Alpaca"},
        {"ok": mode() == "paper", "text": "paper account" if mode() == "paper" else "LIVE account — real money"},
        {"ok": margin == "1", "text": "no margin" if margin == "1" else
            f"margin up to {margin}× is on at Alpaca; this app still buys with cash only"},
        {"ok": bool(configuration.get("no_shorting")), "text": "shorting off" if configuration.get("no_shorting")
            else "shorting is on at Alpaca; this app never sells more than it holds"},
    ]
