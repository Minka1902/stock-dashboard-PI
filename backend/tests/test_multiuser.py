"""Multi-tenancy: per-user isolation, legacy claim migration, admin gating."""
import pytest
from fastapi.testclient import TestClient

from app import db
from app.models import NotifyProfile, WatchItem
from tests.conftest import buy, authenticate


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "multi.db"))
    import importlib
    from app import config, main as main_module
    importlib.reload(config)
    importlib.reload(main_module)
    with TestClient(main_module.app) as c:
        yield c


# ---------- db-level ----------

def test_watchlists_are_isolated_per_user(conn):
    db.add_watch(conn, 1, WatchItem(ticker="AAPL", note="", added_at="t1"))
    db.add_watch(conn, 2, WatchItem(ticker="MSFT", note="", added_at="t2"))
    assert [w.ticker for w in db.get_watchlist(conn, 1)] == ["AAPL"]
    assert [w.ticker for w in db.get_watchlist(conn, 2)] == ["MSFT"]
    # Union feeds the shared ingestion pipeline.
    assert db.get_all_watched_tickers(conn) == ["AAPL", "MSFT"]
    # Removing user 1's AAPL leaves user 2 untouched.
    db.add_watch(conn, 2, WatchItem(ticker="AAPL", note="", added_at="t3"))
    db.remove_watch(conn, 1, "AAPL")
    assert db.get_watchlist(conn, 1) == []
    assert {w.ticker for w in db.get_watchlist(conn, 2)} == {"AAPL", "MSFT"}


def test_portfolio_and_profile_isolated(conn):
    buy(conn, 1, "NOC", 5, 100)
    buy(conn, 2, "NOC", 9, 50)
    assert db.get_portfolio(conn, 1)[0].shares == 5
    assert db.get_portfolio(conn, 2)[0].shares == 9
    assert db.get_all_portfolio_tickers(conn) == ["NOC"]

    db.upsert_notify_profile(conn, 1, NotifyProfile(email="a@x.co", updated_at="t"))
    assert db.get_notify_profile(conn, 1).email == "a@x.co"
    assert db.get_notify_profile(conn, 2).email is None


def test_alert_read_state_is_per_user(conn):
    from app.models import Alert
    db.upsert_alerts(conn, [Alert(
        dedup_key="k1", created_at="t", ticker="AAPL", type="boom",
        severity="high", title="T", message="M",
    )])
    assert db.count_unread_alerts(conn, 1) == 1
    assert db.count_unread_alerts(conn, 2) == 1
    db.mark_alerts_read(conn, 1, read_at="t2")
    assert db.count_unread_alerts(conn, 1) == 0
    assert db.count_unread_alerts(conn, 2) == 1
    assert db.get_alerts(conn, 1)[0].read is True
    assert db.get_alerts(conn, 2)[0].read is False


def test_claim_legacy_rows(conn):
    # Pre-auth data parked under user_id=0 …
    db.add_watch(conn, 0, WatchItem(ticker="LMT", note="legacy", added_at="t"))
    buy(conn, 0, "LMT", 3, 400)
    db.upsert_notify_profile(conn, 0, NotifyProfile(email="old@x.co", updated_at="t"))
    # … is claimed by the first registered user.
    user = db.create_user(conn, "owner@x.co", "hash", "t", is_admin=True)
    db.claim_legacy_rows(conn, user.id)
    assert [w.ticker for w in db.get_watchlist(conn, user.id)] == ["LMT"]
    assert db.get_portfolio(conn, user.id)[0].ticker == "LMT"
    assert db.get_notify_profile(conn, user.id).email == "old@x.co"
    assert db.get_watchlist(conn, 0) == []


def test_migration_rebuilds_old_single_user_tables(tmp_path):
    import sqlite3
    path = str(tmp_path / "legacy.db")
    raw = sqlite3.connect(path)
    raw.executescript(
        """
        CREATE TABLE watchlist (ticker TEXT PRIMARY KEY, note TEXT NOT NULL, added_at TEXT NOT NULL);
        INSERT INTO watchlist VALUES ('AAPL', 'old note', 't1');
        CREATE TABLE portfolio (ticker TEXT PRIMARY KEY, shares REAL NOT NULL, avg_cost REAL NOT NULL, added_at TEXT NOT NULL);
        INSERT INTO portfolio VALUES ('NOC', 4, 450.0, 't2');
        CREATE TABLE notify_profile (
            id INTEGER PRIMARY KEY CHECK (id = 1), email TEXT, phone TEXT,
            email_enabled INTEGER NOT NULL DEFAULT 0, sms_enabled INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT ''
        );
        INSERT INTO notify_profile (id, email, email_enabled) VALUES (1, 'me@x.co', 1);
        """
    )
    raw.commit()
    raw.close()

    connection = db.connect(path)
    db.init_schema(connection)  # must rebuild without losing rows
    assert [w.ticker for w in db.get_watchlist(connection, 0)] == ["AAPL"]
    assert db.get_watchlist(connection, 0)[0].note == "old note"
    assert db.get_portfolio(connection, 0)[0].ticker == "NOC"
    p = db.get_notify_profile(connection, 0)
    assert p.email == "me@x.co" and p.email_enabled is True
    # Idempotent: a second init_schema is harmless.
    db.init_schema(connection)
    assert [w.ticker for w in db.get_watchlist(connection, 0)] == ["AAPL"]
    connection.close()


# ---------- API-level ----------

def test_two_users_see_disjoint_books_and_first_is_admin(client):
    authenticate(client, email="first@x.co")
    client.post("/api/watchlist", json={"ticker": "AAPL", "note": "mine"})
    client.post("/api/portfolio", json={"ticker": "NOC", "shares": 2, "avg_cost": 100})
    assert client.get("/api/auth/me").json()["is_admin"] is True
    client.post("/api/auth/logout")

    from app import security
    security.limiter.reset()
    authenticate(client, email="second@x.co")
    me = client.get("/api/auth/me").json()
    assert me["is_admin"] is False
    assert client.get("/api/watchlist").json() == []
    assert client.get("/api/portfolio").json() == []
    # Second user's adds don't leak back to the first.
    client.post("/api/watchlist", json={"ticker": "MSFT", "note": ""})
    assert [w["ticker"] for w in client.get("/api/watchlist").json()] == ["MSFT"]


def test_settings_put_is_admin_only(client):
    authenticate(client, email="first@x.co")
    client.post("/api/auth/logout")
    from app import security
    security.limiter.reset()
    authenticate(client, email="second@x.co")
    assert client.get("/api/settings").status_code == 200
    r = client.put("/api/settings", json={"analysis_time": "10:00"})
    assert r.status_code == 403


def test_first_user_claims_legacy_data_via_register(client):
    # Seed pre-auth rows directly (user_id=0), then register.
    from app import main as main_module
    db.add_watch(main_module.conn, 0, WatchItem(ticker="LMT", note="", added_at="t"))
    authenticate(client, email="owner@x.co")
    assert [w["ticker"] for w in client.get("/api/watchlist").json()] == ["LMT"]
