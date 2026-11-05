import os
import tempfile

# Point file logging at a scratch dir before anything imports app.config, so a
# test run never writes into the real backend/logs/backend.log that we ask
# people to read after an incident.
os.environ.setdefault(
    "STOCKS_LOG_DIR", os.path.join(tempfile.gettempdir(), "stocks-test-logs"))

import pytest  # noqa: E402
from app import db, security  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    """A fresh, schema-initialized SQLite connection backed by a temp file."""
    db_file = tmp_path / "test.db"
    connection = db.connect(str(db_file))
    db.init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """The limiter is module-global; keep its counters test-scoped."""
    security.limiter.reset()
    yield
    security.limiter.reset()


def drain_refresh(timeout=30):
    """Block until every queued /api/refresh job has finished.

    POST /api/refresh returns 202 and does the work on a single-worker executor,
    so a test that asserts on the result has to wait. The executor is FIFO with
    exactly one thread, so a sentinel submitted afterwards can only run once the
    real jobs are done — no polling, no sleeps.
    """
    from app import main

    main._refresh_executor.submit(lambda: None).result(timeout=timeout)


def authenticate(client, email="tester@example.com", password="hunter2-secure"):
    """Register + enroll TOTP on a TestClient so protected routes pass auth.

    Returns the TOTP secret so tests can mint further codes with pyotp.
    """
    import pyotp

    r = client.post("/api/auth/register", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    setup = client.get("/api/auth/totp/setup")
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    enabled = client.post(
        "/api/auth/totp/enable", json={"code": pyotp.TOTP(secret).now()})
    assert enabled.status_code == 200, enabled.text
    return secret


def buy(conn, user_id, ticker, shares, avg_cost, currency="USD",
        executed_at="2026-01-01T00:00:00+00:00", account="manual"):
    """Seed a position through the ledger (the only write path)."""
    from app import ledger

    return ledger.record(
        conn, user_id, ticker=ticker, side="BUY", qty=shares, price=avg_cost,
        currency_code=currency, executed_at=executed_at, account=account)
