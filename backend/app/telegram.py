"""The Telegram bot (STC-79): alerts out, read-only commands in.

Over the Bot API with the existing httpx; no new dependency. The token is a
secret, env only (STOCKS_TELEGRAM_BOT_TOKEN), and everything here no-ops while
it is unset. It is also the URL path of every request, so HTTP failures are
re-raised without the URL: a traceback must never carry it into a job-run row.

Linking: Settings issues a one-time code (stored hashed, 10-minute life); the
user sends `/start CODE` to the bot in a *private* chat. Group chats are
refused, so nobody can link a portfolio into a group.

Inbound updates come from a short-poll getUpdates job (timeout 0, so a run is
one quick request), with the offset kept in the DB so a restart never replays
a command. Outbound: high-severity alerts (notify.push_alert) are sent at
once; bot fills are queued and sent by the next poll, so a slow Telegram
never holds up the bot engine.
"""
import hashlib
import logging
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

import httpx

from app import audit, config, db

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"
LINK_TTL = timedelta(minutes=10)
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CODE_LEN = 8
_MAX_BAD_CODES = 5
_BAD_CODE_WINDOW = 600
_TIMEOUT = 10.0
_LIMIT = 4000          # Telegram's message cap is 4096

_outbox: deque[tuple[int, str]] = deque(maxlen=200)
_bad_codes: dict[int, list[float]] = {}
_lock = threading.Lock()
_bot_username: str | None = None
_username_tried_at = float("-inf")
_USERNAME_RETRY_SECONDS = 300

HELP = ("Commands (read-only):\n"
        "/status — market regime, kill switches, latest account values\n"
        "/bots — your trading bots\n"
        "/watchlist — your watched tickers\n"
        "/alerts — your latest alerts\n"
        "/stop — unlink this chat")


class TelegramError(RuntimeError):
    pass


def configured() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN)


def _client() -> httpx.Client:
    return httpx.Client(timeout=_TIMEOUT)


def call(client: httpx.Client, method: str, **params):
    """One Bot API call. Raises TelegramError without the URL (the token)."""
    try:
        r = client.post(_API.format(token=config.TELEGRAM_BOT_TOKEN, method=method), json=params)
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise TelegramError(f"Telegram {method} failed: {type(exc).__name__}") from None
    if not data.get("ok"):
        raise TelegramError(f"Telegram {method}: HTTP {r.status_code} {data.get('description', '')}".strip())
    return data["result"]


def bot_username() -> str | None:
    """The bot's @name, for the t.me deep link: cached once known, and after
    a failure not asked again for a few minutes (None meanwhile)."""
    global _bot_username, _username_tried_at
    if _bot_username is None and configured() and time.monotonic() - _username_tried_at > _USERNAME_RETRY_SECONDS:
        _username_tried_at = time.monotonic()
        try:
            with _client() as client:
                _bot_username = call(client, "getMe").get("username")
        except TelegramError as exc:
            logger.warning("%s", exc)
    return _bot_username


def hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def issue_code(conn, user_id: int, now: datetime | None = None) -> tuple[str, str]:
    """A fresh one-time link code for `user_id` (replacing any earlier one)
    and when it expires."""
    now = now or datetime.now(timezone.utc)
    code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LEN))
    expires = (now + LINK_TTL).isoformat(timespec="seconds")
    db.set_telegram_code(conn, user_id, hash_code(code), expires)
    return code, expires


# ---------- commands ----------

def _fmt(value: float | None, ccy: str = "") -> str:
    return "—" if value is None else f"{value:,.2f}{(' ' + ccy) if ccy else ''}"


def _status(conn, uid: int) -> str:
    regime = db.get_latest_regime(conn)
    risk = db.get_risk_settings(conn, uid)
    lines = [f"Regime: {regime.label.replace('_', '-') if regime else 'unknown'}"
             + (f" ({regime.date})" if regime else "")]
    lines.append(f"Trading: HALTED since {risk.trading_halted_at}" if risk.trading_halted_at else "Trading: on")
    if risk.drawdown_tripped_at:
        lines.append(f"Drawdown kill switch: tripped {risk.drawdown_tripped_at} — acknowledge it in the app")
    accounts = ["manual", "paper", *(f"bot:{b.id}" for b in db.get_bots(conn, uid) if b.status != "stopped")]
    for acct in accounts:
        snaps = db.get_equity_snapshots(conn, uid, acct)
        if snaps:
            s = snaps[-1]
            lines.append(f"{acct}: {_fmt(s.value_base, s.base_currency)} at the {s.date} close")
    lines.append(f"Unread alerts: {db.count_unread_alerts(conn, uid)}")
    return "\n".join(lines)


def _bots(conn, uid: int) -> str:
    bots = [b for b in db.get_bots(conn, uid) if b.status != "stopped"]
    if not bots:
        return "No bots running. Create one on the Trading Bot page."
    lines = []
    for b in bots:
        held = db.get_portfolio(conn, uid, f"bot:{b.id}")
        cash = sum(c.balance for c in db.get_cash(conn, uid, f"bot:{b.id}"))
        lines.append(f"{b.name} — {b.strategy}, {b.status}; {len(held)} open "
                     f"position{'s' if len(held) != 1 else ''}"
                     + (f" ({', '.join(h.ticker for h in held)})" if held else "")
                     + f"; cash {_fmt(cash, b.currency)}")
    return "\n".join(lines)


def _watchlist(conn, uid: int) -> str:
    tickers = db.get_watched_tickers_for_user(conn, uid)
    if not tickers:
        return "Your watchlist is empty."
    lines = []
    for t in tickers[:40]:
        a = db.get_analysis(conn, t)
        lines.append(f"{t}: {_fmt(a.price)} · {a.recommendation}" if a and a.price is not None else t)
    more = len(tickers) - 40
    return "\n".join(lines) + (f"\n…and {more} more" if more > 0 else "")


def _alerts(conn, uid: int) -> str:
    alerts = db.get_alerts(conn, uid, limit=5)
    if not alerts:
        return "No alerts."
    return "\n\n".join(f"{'•' if not a.read else '◦'} {a.created_at[:16].replace('T', ' ')} "
                       f"{a.ticker + ': ' if a.ticker else ''}{a.title}\n{a.message}" for a in alerts)


_COMMANDS = {"/status": _status, "/bots": _bots, "/watchlist": _watchlist, "/alerts": _alerts}


def _too_many_bad_codes(chat_id: int, now: float) -> bool:
    with _lock:
        recent = [t for t in _bad_codes.get(chat_id, []) if now - t < _BAD_CODE_WINDOW]
        _bad_codes[chat_id] = recent
        return len(recent) >= _MAX_BAD_CODES


def _note_bad_code(chat_id: int, now: float) -> None:
    with _lock:
        _bad_codes.setdefault(chat_id, []).append(now)


def handle(conn, update: dict, now: datetime | None = None) -> tuple[int, str] | None:
    """(chat_id, reply) for one update, or None to stay silent."""
    msg = update.get("message") or {}
    chat, text = msg.get("chat") or {}, (msg.get("text") or "").strip()
    chat_id = chat.get("id")
    if chat_id is None or not text.startswith("/"):
        return None
    if chat.get("type") != "private":
        return chat_id, "I only work in a private chat with you."
    now = now or datetime.now(timezone.utc)
    command, _, arg = text.partition(" ")
    command = command.split("@")[0].lower()
    link = db.get_telegram_link(conn, chat_id=chat_id)

    if command == "/start":
        if not arg.strip():
            return chat_id, (HELP if link else "To link this chat, open Settings → Telegram in the dashboard, "
                                               "press Connect and send me /start with the code shown.")
        if _too_many_bad_codes(chat_id, time.monotonic()):
            return chat_id, "Too many wrong codes. Wait ten minutes and get a fresh code."
        uid = db.redeem_telegram_code(conn, hash_code(arg), now.isoformat(timespec="seconds"), chat_id,
                                      (msg.get("from") or {}).get("username") or "")
        if uid is None:
            _note_bad_code(chat_id, time.monotonic())
            return chat_id, "That code is wrong or has expired. Get a fresh one in Settings → Telegram."
        audit.record(conn, uid, "telegram_linked", username=(msg.get("from") or {}).get("username") or "")
        return chat_id, "Linked. You'll get high-severity alerts and bot fills here.\n\n" + HELP
    if link is None:
        return chat_id, "This chat isn't linked yet. Open Settings → Telegram in the dashboard to connect it."
    if command == "/stop":
        db.delete_telegram_link(conn, chat_id=chat_id)
        return chat_id, "Unlinked. Nothing more will be sent here."
    if command == "/help":
        return chat_id, HELP
    if command in _COMMANDS:
        return chat_id, _COMMANDS[command](conn, link["user_id"])[:_LIMIT]
    return chat_id, "I don't know that one.\n\n" + HELP


# ---------- outbound ----------

def send_to_user(conn, user_id: int, text: str, client: httpx.Client | None = None) -> str:
    """"sent", "skipped" (not configured / not linked / turned off) or
    "error: …" — the same shape as notify.send_email."""
    if not configured():
        return "skipped"
    link = db.get_telegram_link(conn, user_id=user_id)
    if not link or not link["enabled"]:
        return "skipped"
    try:
        if client is not None:
            call(client, "sendMessage", chat_id=link["chat_id"], text=text[:_LIMIT])
        else:
            with _client() as c:
                call(c, "sendMessage", chat_id=link["chat_id"], text=text[:_LIMIT])
        return "sent"
    except TelegramError as exc:
        return f"error: {exc}"


def queue(user_id: int, text: str) -> None:
    """Send `text` to the user with the next poll (no-op when not configured)."""
    if configured():
        with _lock:
            _outbox.append((user_id, text))


def poll(conn, now: datetime | None = None, client: httpx.Client | None = None) -> int:
    """Answer new messages and flush the outbox. Returns updates handled."""
    if not configured():
        return 0
    own = client is None
    client = client or _client()
    try:
        offset = db.get_telegram_offset(conn)
        updates = call(client, "getUpdates", offset=offset, timeout=0, allowed_updates=["message"])
        for u in updates:
            db.set_telegram_offset(conn, u["update_id"] + 1)
            reply = handle(conn, u, now)
            if reply:
                call(client, "sendMessage", chat_id=reply[0], text=reply[1])
        with _lock:
            pending = list(_outbox)
            _outbox.clear()
        for uid, text in pending:
            status = send_to_user(conn, uid, text, client)
            if status.startswith("error"):
                logger.warning("telegram: %s", status)
        return len(updates)
    finally:
        if own:
            client.close()
