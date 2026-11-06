"""Account creation is gated separately from reachability.

Once the dashboard is served on a public URL, "anyone can load it" must not
mean "anyone can create an account on it". There are exactly two account
factories -- the password form and the OAuth auto-create -- and both are
covered here, because half a gate reads as done while leaving the door open.

The mode is monkeypatched onto app.config directly rather than set via env +
reload: app/registration.py reads config at call time precisely so this works.
"""
import importlib

import pytest
from fastapi.testclient import TestClient

from app import config, db

EMAIL = "newcomer@example.com"
PASSWORD = "correct-horse-9"
CODE = "let-me-in-please"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "gate.db"))
    from app import main as main_module
    importlib.reload(config)
    importlib.reload(main_module)
    with TestClient(main_module.app) as c:
        yield c


def _register(client, **extra):
    return client.post(
        "/api/auth/register",
        json={"email": EMAIL, "password": PASSWORD, **extra},
    )


def _set_mode(monkeypatch, mode, code=""):
    monkeypatch.setattr(config, "REGISTRATION_MODE", mode)
    monkeypatch.setattr(config, "INVITE_CODE", code)


# ---------- password path ----------

def test_open_mode_still_registers(client):
    """The default. Guards every other test in the suite, which relies on it."""
    assert config.REGISTRATION_MODE == "open"
    r = _register(client)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "totp_setup_required"


def test_invite_mode_rejects_missing_code(client, monkeypatch):
    _set_mode(monkeypatch, "invite", CODE)
    assert _register(client).status_code == 403


def test_invite_mode_rejects_wrong_code(client, monkeypatch):
    _set_mode(monkeypatch, "invite", CODE)
    assert _register(client, invite_code="guess").status_code == 403


def test_invite_mode_accepts_correct_code(client, monkeypatch):
    _set_mode(monkeypatch, "invite", CODE)
    r = _register(client, invite_code=CODE)
    assert r.status_code == 200, r.text


def test_invite_mode_with_blank_configured_code_stays_shut(client, monkeypatch):
    """compare_digest("", "") is True -- the empty code must not open the door."""
    _set_mode(monkeypatch, "invite", "")
    assert _register(client).status_code == 403
    assert _register(client, invite_code="").status_code == 403


def test_closed_mode_rejects_even_with_a_code(client, monkeypatch):
    _set_mode(monkeypatch, "closed", CODE)
    assert _register(client, invite_code=CODE).status_code == 403


def test_closed_mode_has_no_first_user_bootstrap_exemption(client, monkeypatch):
    """An "allow when there are no users yet" escape hatch would be a race
    between the operator and the first stranger to load the URL -- and the
    winner gets is_admin plus claim_legacy_rows."""
    from app import main as main_module
    assert db.count_users(main_module.conn) == 0
    _set_mode(monkeypatch, "closed")
    assert _register(client).status_code == 403
    assert db.count_users(main_module.conn) == 0


def test_gate_runs_before_validation(client, monkeypatch):
    """A stranger should not be able to probe the email/password rules."""
    _set_mode(monkeypatch, "closed")
    r = client.post("/api/auth/register", json={"email": "no", "password": "x"})
    assert r.status_code == 403  # not 400


# ---------- the mode endpoint the frontend reads ----------

def test_registration_mode_is_public(client, monkeypatch):
    _set_mode(monkeypatch, "invite", CODE)
    r = client.get("/api/auth/registration")
    assert r.status_code == 200
    assert r.json() == {"mode": "invite"}


# ---------- OAuth path ----------

@pytest.fixture
def oauth(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "gate-oauth.db"))
    monkeypatch.setenv("STOCKS_OAUTH_GITHUB_CLIENT_ID", "gh-id")
    monkeypatch.setenv("STOCKS_OAUTH_GITHUB_CLIENT_SECRET", "gh-secret")
    from app import main as main_module, routes_oauth
    importlib.reload(config)
    importlib.reload(routes_oauth)
    importlib.reload(main_module)
    with TestClient(main_module.app) as c:
        yield c, main_module, routes_oauth


def _fake_provider(routes_oauth, monkeypatch, provider_user_id, email):
    monkeypatch.setattr(routes_oauth, "_exchange_code",
                        lambda client, provider, code: "tok")
    monkeypatch.setattr(routes_oauth, "_fetch_identity",
                        lambda client, provider, tok: (provider_user_id, email))


def _callback(client, state="s1"):
    client.cookies.set("oauth_state", state)
    return client.get(
        f"/api/auth/oauth/github/callback?code=x&state={state}",
        follow_redirects=False)


def test_oauth_signup_blocked_when_not_open(oauth, monkeypatch):
    """The second account factory. Without this gate, a stranger's Google
    account becomes an account here just because the callback is reachable."""
    c, main_module, routes_oauth = oauth
    _set_mode(monkeypatch, "invite", CODE)
    _fake_provider(routes_oauth, monkeypatch, "gh-1", "stranger@example.com")

    r = _callback(c)
    assert r.status_code == 302
    assert "oauth_error=registration_closed" in r.headers["location"]
    assert db.count_users(main_module.conn) == 0


def test_oauth_linking_to_existing_account_still_works(oauth, monkeypatch):
    """The gate blocks creation, not linking -- otherwise social login would
    break for people who already have an account."""
    c, main_module, routes_oauth = oauth
    existing = db.create_user(main_module.conn, "owner@example.com", "hash", "t")
    _set_mode(monkeypatch, "invite", CODE)
    _fake_provider(routes_oauth, monkeypatch, "gh-2", "owner@example.com")

    r = _callback(c)
    assert r.status_code == 302
    assert "oauth_error" not in r.headers["location"]
    assert db.count_users(main_module.conn) == 1
    ident = db.get_oauth_identity(main_module.conn, "github", "gh-2")
    assert ident is not None and ident.user_id == existing.id


# ---------- import-time config validation ----------

def test_invite_mode_without_a_code_refuses_to_boot(monkeypatch):
    """Fail loudly at import rather than silently accepting empty codes."""
    monkeypatch.setenv("STOCKS_REGISTRATION", "invite")
    monkeypatch.delenv("STOCKS_INVITE_CODE", raising=False)
    try:
        with pytest.raises(ValueError, match="STOCKS_INVITE_CODE"):
            importlib.reload(config)
    finally:
        # Mandatory: config is module-global, and leaving it half-loaded
        # poisons every test that runs after this one.
        monkeypatch.delenv("STOCKS_REGISTRATION", raising=False)
        importlib.reload(config)


def test_unknown_registration_mode_refuses_to_boot(monkeypatch):
    monkeypatch.setenv("STOCKS_REGISTRATION", "sometimes")
    try:
        with pytest.raises(ValueError, match="open, invite, approval, closed"):
            importlib.reload(config)
    finally:
        monkeypatch.delenv("STOCKS_REGISTRATION", raising=False)
        importlib.reload(config)


def test_approval_mode_without_an_admin_email_refuses_to_boot(monkeypatch):
    monkeypatch.setenv("STOCKS_REGISTRATION", "approval")
    monkeypatch.delenv("STOCKS_ADMIN_EMAIL", raising=False)
    try:
        with pytest.raises(ValueError, match="STOCKS_ADMIN_EMAIL"):
            importlib.reload(config)
    finally:
        monkeypatch.delenv("STOCKS_REGISTRATION", raising=False)
        importlib.reload(config)


# ---------- approval queue ----------

ADMIN = "owner@example.com"


def _approval(monkeypatch):
    monkeypatch.setattr(config, "REGISTRATION_MODE", "approval")
    monkeypatch.setattr(config, "ADMIN_EMAIL", ADMIN)


def _enroll(client, email):
    from tests.conftest import authenticate
    from app import security
    client.post("/api/auth/logout")
    security.limiter.reset()
    return authenticate(client, email=email)


def test_approval_mode_a_stranger_first_is_pending_not_admin(client, monkeypatch):
    """Registering first wins nothing: only STOCKS_ADMIN_EMAIL is admin."""
    _approval(monkeypatch)
    _enroll(client, EMAIL)
    me = client.get("/api/auth/me").json()
    assert me["approved"] is False and me["is_admin"] is False
    assert client.get("/api/watchlist").status_code == 403
    assert client.get("/api/watchlist").json()["detail"] == "account pending approval"
    assert client.post("/api/auth/logout").status_code == 200


def test_approval_mode_admin_email_is_approved_admin_whatever_the_order(client, monkeypatch):
    _approval(monkeypatch)
    _enroll(client, EMAIL)
    _enroll(client, ADMIN.upper())
    me = client.get("/api/auth/me").json()
    assert me["approved"] is True and me["is_admin"] is True
    assert client.get("/api/watchlist").status_code == 200


def _as(app, email):
    """A second browser: its own cookie jar against the same running app."""
    from tests.conftest import authenticate
    other = TestClient(app)
    authenticate(other, email=email)
    return other


def test_admin_is_alerted_and_approves_a_pending_account(client, monkeypatch):
    from app import main

    _approval(monkeypatch)
    _enroll(client, ADMIN)
    newcomer = _as(main.app, EMAIL)
    pending_id = newcomer.get("/api/auth/me").json()["id"]

    alerts = client.get("/api/alerts").json()["alerts"]
    signup = [a for a in alerts if a["type"] == "signup"]
    assert len(signup) == 1 and EMAIL in signup[0]["message"]
    assert newcomer.get("/api/server/signups").status_code == 403

    queue = client.get("/api/server/signups").json()
    assert [r["email"] for r in queue] == [EMAIL] and queue[0]["totp_enabled"] is True

    assert client.post(f"/api/server/signups/{pending_id}/approve").status_code == 200
    assert newcomer.get("/api/watchlist").status_code == 200
    assert client.get("/api/server/signups").json() == []
    assert client.post(f"/api/server/signups/{pending_id}/approve").status_code == 404
    audit_log = client.get("/api/audit").json()
    assert "signup_approved" in [e["kind"] for e in audit_log["entries"]]
    assert audit_log["verify"]["ok"] is True


def test_admin_rejects_a_pending_account_and_it_is_gone(client, monkeypatch):
    from app import main

    _approval(monkeypatch)
    _enroll(client, ADMIN)
    newcomer = _as(main.app, EMAIL)
    pending_id = newcomer.get("/api/auth/me").json()["id"]

    assert client.post(f"/api/server/signups/{pending_id}/reject").status_code == 200
    assert db.get_user(main.conn, pending_id) is None
    assert newcomer.get("/api/auth/me").status_code == 401
    me = client.get("/api/auth/me").json()
    assert client.post(f"/api/server/signups/{me['id']}/reject").status_code == 404


def test_oauth_signup_in_approval_mode_is_pending(oauth, monkeypatch):
    c, main_module, routes_oauth = oauth
    _approval(monkeypatch)
    _fake_provider(routes_oauth, monkeypatch, "gh-9", "stranger@example.com")

    r = _callback(c)
    assert r.status_code == 302 and "oauth_error" not in r.headers["location"]
    user = db.get_user_by_email(main_module.conn, "stranger@example.com")
    assert user.approved_at is None and user.is_admin is False


def test_existing_accounts_are_approved_by_the_migration(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL "
                "UNIQUE COLLATE NOCASE, password_hash TEXT NOT NULL, totp_secret TEXT, "
                "totp_enabled INTEGER NOT NULL DEFAULT 0, is_admin INTEGER NOT NULL DEFAULT 0, "
                "created_at TEXT NOT NULL)")
    old.execute("INSERT INTO users (email, password_hash, created_at) VALUES ('a@x.co', 'h', '2026-01-01')")
    old.commit()
    old.close()
    conn = db.connect(str(path))
    db.init_schema(conn)
    assert db.get_user_by_email(conn, "a@x.co").approved_at == "2026-01-01"
    assert db.get_pending_users(conn) == []
    conn.close()
