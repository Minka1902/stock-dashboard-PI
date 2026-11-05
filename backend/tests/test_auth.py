"""Auth flows: registration, TOTP enrollment, login challenge, recovery, enforcement."""
import pyotp
import pytest
from fastapi.testclient import TestClient

from app import auth
from tests.conftest import authenticate

EMAIL = "owner@example.com"
PASSWORD = "correct-horse-9"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "auth.db"))
    import importlib
    from app import config, main as main_module
    importlib.reload(config)
    importlib.reload(main_module)
    with TestClient(main_module.app) as c:
        yield c


# ---------- unit: password & TOTP primitives ----------

def test_password_hash_roundtrip():
    h = auth.hash_password("s3cret-enough")
    assert h != "s3cret-enough"
    assert auth.verify_password(h, "s3cret-enough")
    assert not auth.verify_password(h, "wrong")


def test_verify_totp_rejects_garbage():
    secret = auth.new_totp_secret()
    assert not auth.verify_totp(secret, "")
    assert not auth.verify_totp(secret, "12345")     # too short
    assert not auth.verify_totp(secret, "abcdef")    # not digits
    assert auth.verify_totp(secret, pyotp.TOTP(secret).now())


def test_provisioning_uri_and_qr():
    secret = auth.new_totp_secret()
    uri = auth.provisioning_uri(secret, "a@b.co")
    assert uri.startswith("otpauth://totp/")
    assert "Stock%20Signal%20Dashboard" in uri
    qr = auth.qr_data_uri(uri)
    assert qr.startswith("data:image/png;base64,")


def test_recovery_codes_shape():
    codes, hashes = auth.generate_recovery_codes()
    assert len(codes) == len(hashes) == 8
    assert all(len(c) == 14 and c.count("-") == 2 for c in codes)
    assert auth.hash_recovery_code(codes[0]) == hashes[0]
    # Entry is forgiving about case/whitespace.
    assert auth.hash_recovery_code(f"  {codes[0].upper()} ") == hashes[0]


# ---------- flows ----------

def test_register_enroll_and_me(client):
    r = client.post("/api/auth/register", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 200
    assert r.json() == {"status": "totp_setup_required"}

    # Not active yet: protected routes still 401.
    assert client.get("/api/watchlist").status_code == 401
    assert client.get("/api/auth/me").status_code == 401

    setup = client.get("/api/auth/totp/setup").json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/")
    assert setup["qr_png"].startswith("data:image/png;base64,")

    enabled = client.post(
        "/api/auth/totp/enable",
        json={"code": pyotp.TOTP(setup["secret"]).now()},
    )
    assert enabled.status_code == 200
    body = enabled.json()
    assert body["user"]["email"] == EMAIL
    assert body["user"]["is_admin"] is True  # first account
    assert len(body["recovery_codes"]) == 8

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == EMAIL
    assert client.get("/api/watchlist").status_code == 200


def test_register_validation(client):
    bad = client.post("/api/auth/register", json={"email": "nope", "password": PASSWORD})
    assert bad.status_code == 400
    short = client.post("/api/auth/register", json={"email": EMAIL, "password": "short"})
    assert short.status_code == 400
    authenticate(client, email=EMAIL, password=PASSWORD)
    dup = client.post("/api/auth/register", json={"email": EMAIL.upper(), "password": PASSWORD})
    assert dup.status_code == 409  # email unique, case-insensitive


def test_login_requires_totp(client):
    secret = authenticate(client, email=EMAIL, password=PASSWORD)
    client.post("/api/auth/logout")
    assert client.get("/api/watchlist").status_code == 401

    wrong = client.post("/api/auth/login", json={"email": EMAIL, "password": "bad-password"})
    assert wrong.status_code == 401
    unknown = client.post("/api/auth/login", json={"email": "x@y.zz", "password": "whatever1"})
    assert unknown.status_code == 401
    # Same body shape for unknown email and wrong password.
    assert wrong.json() == unknown.json()

    r = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.json() == {"status": "totp_required"}
    # Pending session cannot reach protected routes.
    assert client.get("/api/watchlist").status_code == 401

    bad_code = client.post("/api/auth/totp/verify", json={"code": "000000"})
    assert bad_code.status_code == 400

    ok = client.post("/api/auth/totp/verify", json={"code": pyotp.TOTP(secret).now()})
    assert ok.status_code == 200
    assert client.get("/api/watchlist").status_code == 200


def test_too_many_wrong_codes_revokes_pending_session(client):
    authenticate(client, email=EMAIL, password=PASSWORD)
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    from app import security
    security.limiter.reset()
    statuses = []
    for _ in range(6):
        statuses.append(
            client.post("/api/auth/totp/verify", json={"code": "000000"}).status_code)
        security.limiter.reset()  # exercise the attempt counter, not the rate limit
    assert statuses[:5] == [400] * 5
    assert statuses[5] == 401  # session revoked
    # Even a correct-looking retry is now unauthenticated.
    assert client.post("/api/auth/totp/verify", json={"code": "123456"}).status_code == 401


def test_recovery_code_single_use(client):
    client.post("/api/auth/register", json={"email": EMAIL, "password": PASSWORD})
    setup = client.get("/api/auth/totp/setup").json()
    codes = client.post(
        "/api/auth/totp/enable",
        json={"code": pyotp.TOTP(setup["secret"]).now()},
    ).json()["recovery_codes"]

    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    used = client.post("/api/auth/recovery", json={"code": codes[0]})
    assert used.status_code == 200
    assert used.json()["remaining_codes"] == 7
    assert client.get("/api/watchlist").status_code == 200

    # The same code cannot be replayed.
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    replay = client.post("/api/auth/recovery", json={"code": codes[0]})
    assert replay.status_code == 400


def test_logout_revokes_session(client):
    authenticate(client, email=EMAIL, password=PASSWORD)
    assert client.get("/api/auth/me").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/portfolio").status_code == 401


def test_protected_routes_401_without_cookie(client):
    for path in ("/api/watchlist", "/api/portfolio", "/api/sources", "/api/settings",
                 "/api/boom-scores", "/api/chart/AAPL", "/api/analysis"):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/refresh/usaspending").status_code == 401
    # Public endpoints stay open.
    assert client.get("/api/health").status_code == 200


def test_login_rate_limited(client, monkeypatch):
    from app import security
    monkeypatch.setattr(security.limiter, "check", lambda *a, **k: 42.0)
    r = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 429
    assert r.headers.get("Retry-After") == "42"


# ---------- onboarding flag (gates the auto-tour for returning users) ----------

def test_new_user_starts_not_onboarded(client):
    authenticate(client)
    assert client.get("/api/auth/me").json()["onboarded"] is False


def test_mark_onboarded_persists(client):
    authenticate(client)
    r = client.post("/api/auth/onboarded")
    assert r.status_code == 200
    assert r.json()["onboarded"] is True
    # persists on subsequent reads (would suppress the tour on any device)
    assert client.get("/api/auth/me").json()["onboarded"] is True


def test_mark_onboarded_requires_auth(client):
    assert client.post("/api/auth/onboarded").status_code == 401


def test_onboarded_migration_backfills_existing_users(tmp_path):
    """Pre-existing accounts (from before the column) are returning users and
    must be backfilled to onboarded=1; accounts created after start at 0."""
    from app import db

    conn = db.connect(str(tmp_path / "old.db"))
    # A users table shaped like the pre-onboarded schema.
    conn.execute(
        """CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL, totp_secret TEXT,
            totp_enabled INTEGER NOT NULL DEFAULT 0,
            is_admin INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)"""
    )
    conn.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES ('old@b.co','h','2026-01-01')")
    conn.commit()

    db.init_schema(conn)  # runs _migrate_users_onboarded

    assert db.get_user_by_email(conn, "old@b.co").onboarded is True
    fresh = db.create_user(conn, "new@b.co", "h", "2026-02-02")
    assert db.get_user(conn, fresh.id).onboarded is False
    conn.close()
