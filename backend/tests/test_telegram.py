"""The Telegram bot: linking by one-time code, read-only commands, the poll
loop and its offset, outbound alerts and bot fills, and the API."""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, db, notify, telegram
from app.models import Alert, EquitySnapshot, NotifyProfile, WatchItem
from tests.conftest import authenticate

TOKEN = "123456:SECRET-token"
NOW = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(telegram, "_bot_username", "signal_test_bot")
    telegram._outbox.clear()
    telegram._bad_codes.clear()


def _msg(text, chat_id=42, chat_type="private", username="minka"):
    return {"update_id": 1, "message": {"chat": {"id": chat_id, "type": chat_type},
                                        "from": {"username": username}, "text": text}}


def _linked(conn, chat_id=42):
    u = db.create_user(conn, f"{chat_id}@x.co", "h", "t")
    code, _ = telegram.issue_code(conn, u.id, NOW)
    assert telegram.handle(conn, _msg(f"/start {code}", chat_id), NOW)[1].startswith("Linked")
    return u


class Bot:
    """A fake Bot API: queued updates in, every sendMessage recorded."""

    def __init__(self, updates=(), fail_send=False):
        self.updates, self.sent, self.offsets, self.fail_send = list(updates), [], [], fail_send

    def __call__(self, req: httpx.Request):
        method, body = req.url.path.rsplit("/", 1)[1], json.loads(req.content or b"{}")
        if method == "getUpdates":
            self.offsets.append(body["offset"])
            return httpx.Response(200, json={"ok": True, "result": [u for u in self.updates
                                                                    if u["update_id"] >= body["offset"]]})
        if method == "sendMessage":
            if self.fail_send:
                return httpx.Response(403, json={"ok": False, "description": "Forbidden: bot was blocked"})
            self.sent.append((body["chat_id"], body["text"]))
            return httpx.Response(200, json={"ok": True, "result": {}})
        return httpx.Response(404, json={"ok": False, "description": "Not Found"})

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


# ---------- linking ----------

def test_a_one_time_code_links_a_private_chat_and_is_spent(conn):
    u = db.create_user(conn, "a@x.co", "h", "t")
    code, expires = telegram.issue_code(conn, u.id, NOW)
    assert len(code) == 8 and expires == (NOW + timedelta(minutes=10)).isoformat(timespec="seconds")
    assert telegram.handle(conn, _msg(f"/start {code.lower()}"), NOW)[1].startswith("Linked")
    link = db.get_telegram_link(conn, user_id=u.id)
    assert link["chat_id"] == 42 and link["username"] == "minka" and link["enabled"] is True
    assert "wrong or has expired" in telegram.handle(conn, _msg(f"/start {code}", 43), NOW)[1]


def test_an_expired_code_and_a_group_chat_are_refused(conn):
    u = db.create_user(conn, "a@x.co", "h", "t")
    code, _ = telegram.issue_code(conn, u.id, NOW)
    assert "only work in a private chat" in telegram.handle(conn, _msg(f"/start {code}", chat_type="group"), NOW)[1]
    later = NOW + timedelta(minutes=11)
    assert "wrong or has expired" in telegram.handle(conn, _msg(f"/start {code}"), later)[1]
    assert db.get_telegram_link(conn, user_id=u.id) is None


def test_guessing_codes_is_cut_off_after_five_tries(conn):
    for i in range(5):
        assert "wrong" in telegram.handle(conn, _msg(f"/start WRONG{i}"), NOW)[1]
    assert "Too many wrong codes" in telegram.handle(conn, _msg("/start ANYTHING"), NOW)[1]


def test_relinking_moves_the_chat_and_stop_unlinks(conn):
    a = _linked(conn)
    b = db.create_user(conn, "b@x.co", "h", "t")
    code, _ = telegram.issue_code(conn, b.id, NOW)
    telegram.handle(conn, _msg(f"/start {code}"), NOW)
    assert db.get_telegram_link(conn, user_id=a.id) is None and db.get_telegram_link(conn, chat_id=42)["user_id"] == b.id
    assert telegram.handle(conn, _msg("/stop"), NOW)[1].startswith("Unlinked")
    assert "isn't linked" in telegram.handle(conn, _msg("/status"), NOW)[1]


# ---------- commands ----------

def test_commands_answer_from_the_users_own_data(conn):
    u = _linked(conn)
    db.add_watch(conn, u.id, WatchItem(ticker="AAPL", note="", added_at="t"))
    db.upsert_equity_snapshots(conn, [EquitySnapshot(
        user_id=u.id, account="manual", date="2026-10-26", base_currency="USD", value_base=12_345.6,
        cash_base=0, invested_base=12_345.6, flow_base=0, computed_at="t")])
    db.upsert_alerts(conn, [Alert(dedup_key="k", created_at="2026-10-27T14:00:00+00:00", ticker="AAPL",
                                  type="exit", severity="high", title="Stop hit", message="AAPL hit 180",
                                  user_id=u.id)])
    status = telegram.handle(conn, _msg("/status"), NOW)[1]
    assert "Trading: on" in status and "manual: 12,345.60 USD at the 2026-10-26 close" in status
    assert "Unread alerts: 1" in status
    assert telegram.handle(conn, _msg("/watchlist"), NOW)[1] == "AAPL"
    assert "AAPL: Stop hit" in telegram.handle(conn, _msg("/alerts"), NOW)[1]
    assert telegram.handle(conn, _msg("/bots"), NOW)[1].startswith("No bots running")
    assert "don't know" in telegram.handle(conn, _msg("/buy AAPL"), NOW)[1]
    assert telegram.handle(conn, _msg("hello"), NOW) is None


# ---------- the poll loop and outbound ----------

def test_poll_answers_keeps_the_offset_and_flushes_queued_fills(conn):
    u = _linked(conn)
    bot = Bot([{**_msg("/help"), "update_id": 7}])
    telegram.queue(u.id, "Bot fill (bot:1): BUY 3 AAPL at 180 USD.")
    assert telegram.poll(conn, NOW, bot.client()) == 1
    assert db.get_telegram_offset(conn) == 8
    assert [t[:9] for _, t in bot.sent] == ["Commands ", "Bot fill "]
    assert telegram.poll(conn, NOW, bot.client()) == 0 and bot.offsets == [0, 8]


def test_a_turned_off_chat_gets_no_fills_or_alerts(conn):
    u = _linked(conn)
    db.set_telegram_enabled(conn, u.id, False)
    bot = Bot()
    telegram.queue(u.id, "fill")
    telegram.poll(conn, NOW, bot.client())
    assert bot.sent == [] and telegram.send_to_user(conn, u.id, "x") == "skipped"


def test_a_failed_call_never_carries_the_token(conn, monkeypatch):
    def boom(req):
        raise httpx.ConnectError(f"cannot reach {req.url}")
    with pytest.raises(telegram.TelegramError) as e:
        telegram.call(httpx.Client(transport=httpx.MockTransport(boom)), "getMe")
    assert TOKEN not in str(e.value) and "ConnectError" in str(e.value)
    u = _linked(conn)
    bot = Bot(fail_send=True)
    status = telegram.send_to_user(conn, u.id, "x", bot.client())
    assert status.startswith("error: Telegram sendMessage: HTTP 403") and TOKEN not in status


def test_a_high_severity_alert_reaches_a_linked_chat(conn, monkeypatch):
    u = _linked(conn)
    db.upsert_notify_profile(conn, u.id, NotifyProfile())
    sent = []
    monkeypatch.setattr(telegram, "send_to_user", lambda conn, uid, text, client=None: sent.append((uid, text)) or "sent")
    alert = Alert(dedup_key="x", created_at=NOW.isoformat(), ticker="AAPL", type="exit", severity="high",
                  title="Stop hit", message="AAPL traded at 179.5", user_id=u.id)
    notify.push_alert(conn, alert)
    assert alert.pushed and sent == [(u.id, "[HIGH] AAPL: Stop hit\nAAPL traded at 179.5")]


def test_nothing_happens_without_a_token(conn, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    u = db.create_user(conn, "a@x.co", "h", "t")
    telegram.queue(u.id, "x")
    assert telegram.poll(conn, NOW) == 0 and telegram.send_to_user(conn, u.id, "x") == "skipped"
    assert not telegram._outbox


# ---------- API ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "tg.db"))
    import importlib

    from app import main as main_module
    importlib.reload(config)
    importlib.reload(main_module)
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", TOKEN)
    with TestClient(main_module.app) as c:
        authenticate(c)
        yield c, main_module


def test_api_link_toggle_and_unlink(client):
    c, m = client
    assert c.get("/api/telegram").json() == {"configured": True, "bot_username": "signal_test_bot", "linked": False,
                                             "username": None, "linked_at": None, "enabled": False}
    assert c.put("/api/telegram", json={"enabled": True}).status_code == 409
    issued = c.post("/api/telegram/link").json()
    assert issued["deep_link"] == f"https://t.me/signal_test_bot?start={issued['code']}"
    telegram.handle(m.conn, _msg(f"/start {issued['code']}"), datetime.now(timezone.utc))
    assert c.get("/api/telegram").json()["linked"] is True
    assert c.put("/api/telegram", json={"enabled": False}).json()["enabled"] is False
    assert c.delete("/api/telegram").json()["linked"] is False


def test_api_says_when_the_server_has_no_token(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    assert c.get("/api/telegram").json()["configured"] is False
    r = c.post("/api/telegram/link")
    assert r.status_code == 409 and "STOCKS_TELEGRAM_BOT_TOKEN" in r.json()["detail"]
