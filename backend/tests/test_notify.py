"""Tests for delivery resilience + portfolio/profile DB round-trips."""
import sqlite3

import pytest

from app import db, ledger, notify
from app.models import NotifyProfile
from tests.conftest import buy


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    db.init_schema(c)
    yield c
    c.close()


# ---- DB round-trips ----

def test_portfolio_roundtrip(conn):
    buy(conn, 0, "AAPL", 10, 100.0, executed_at="2026-01-01T00:00:00+00:00")
    # Adding an existing ticker merges shares and weighted-averages the cost
    # (10@100 + 15@110 -> 25 @ (1000+1650)/25 = 106.0), never overwrites.
    buy(conn, 0, "AAPL", 15, 110.0, executed_at="2026-02-01T00:00:00+00:00")
    rows = db.get_portfolio(conn, 0)
    assert len(rows) == 1
    assert rows[0].shares == 25 and rows[0].avg_cost == 106.0
    assert rows[0].added_at == "2026-01-01T00:00:00+00:00"  # first-buy date preserved
    ledger.record(conn, 0, ticker="AAPL", side="ADJUST", qty=0, price=106.0, currency_code="USD")
    assert db.get_portfolio(conn, 0) == []


def test_profile_roundtrip_and_default(conn):
    # Default before anything is saved.
    p = db.get_notify_profile(conn, 0)
    assert p.email is None and p.email_enabled is False
    db.upsert_notify_profile(conn, 0, NotifyProfile(
        email="a@b.com", phone="+14155551234", email_enabled=True, sms_enabled=False, updated_at="t",
    ))
    p2 = db.get_notify_profile(conn, 0)
    assert p2.email == "a@b.com"
    assert p2.email_enabled is True
    assert p2.sms_enabled is False


# ---- delivery is gated + resilient ----

def test_send_email_skipped_when_disabled(conn):
    p = NotifyProfile(email="a@b.com", email_enabled=False)
    assert notify.send_email(p, "s", "t", "<b>h</b>") == "skipped: email not enabled"


def test_send_email_skipped_when_smtp_unconfigured(conn, monkeypatch):
    monkeypatch.setattr(notify.config, "SMTP_HOST", None)
    p = NotifyProfile(email="a@b.com", email_enabled=True)
    assert notify.send_email(p, "s", "t", "<b>h</b>") == "skipped: smtp not configured"


def test_send_sms_skipped_when_unconfigured(conn, monkeypatch):
    monkeypatch.setattr(notify.config, "TWILIO_ACCOUNT_SID", None)
    p = NotifyProfile(phone="+14155551234", sms_enabled=True)
    assert notify.send_sms(p, "hi") == "skipped: twilio not configured"


def test_send_digest_logs_each_channel(conn, monkeypatch):
    monkeypatch.setattr(notify.config, "SMTP_HOST", None)
    monkeypatch.setattr(notify.config, "TWILIO_ACCOUNT_SID", None)
    # Scheduled delivery loops over registered users with an enabled channel.
    user = db.create_user(conn, "a@b.com", "x", "t")
    db.upsert_notify_profile(conn, user.id, NotifyProfile(
        email="a@b.com", email_enabled=True, updated_at="t",
    ))
    results = notify.send_digest(conn, "2026-06-29")
    channels = {r["channel"] for r in results}
    assert channels == {"email", "sms"}
    log = db.get_recent_suggestions(conn)
    assert len(log) == 2
    assert all(e.for_date == "2026-06-29" for e in log)


def test_send_digest_skips_users_without_channels(conn):
    db.create_user(conn, "quiet@b.com", "x", "t")  # no profile at all
    assert notify.send_digest(conn, "2026-06-29") == []
