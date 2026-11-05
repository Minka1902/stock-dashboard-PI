"""Phase-0 hardening: rate limiter, ticker validation, security headers."""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.security import RateLimiter
from app.validation import clean_ticker


# ---------- RateLimiter ----------

def test_limiter_allows_up_to_limit_then_blocks():
    rl = RateLimiter()
    now = 1000.0
    for _ in range(3):
        assert rl.check("b", "k", limit=3, window_seconds=60, now=now) is None
    retry = rl.check("b", "k", limit=3, window_seconds=60, now=now)
    assert retry is not None and retry > 0


def test_limiter_window_resets():
    rl = RateLimiter()
    assert rl.check("b", "k", 1, 60, now=0.0) is None
    assert rl.check("b", "k", 1, 60, now=1.0) is not None
    # A new window opens after window_seconds elapse.
    assert rl.check("b", "k", 1, 60, now=61.0) is None


def test_limiter_keys_are_independent():
    rl = RateLimiter()
    assert rl.check("b", "alice", 1, 60, now=0.0) is None
    assert rl.check("b", "alice", 1, 60, now=0.0) is not None
    assert rl.check("b", "bob", 1, 60, now=0.0) is None
    assert rl.check("other", "alice", 1, 60, now=0.0) is None


# ---------- ticker validation ----------

@pytest.mark.parametrize("raw,expected", [
    ("aapl", "AAPL"),
    (" MSFT ", "MSFT"),
    ("BRK.B", "BRK.B"),
    ("BF-B", "BF-B"),
    ("^GSPC", "^GSPC"),
    ("EURUSD=X", "EURUSD=X"),
])
def test_clean_ticker_accepts_valid(raw, expected):
    assert clean_ticker(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "  ", "AAPL/../etc", "AAPL?range=max", "A B", "AAPL%20", "@EVIL",
    "TOOLONGTICKER", "-AAPL", "aapl;drop", "AA\nPL", "..",
])
def test_clean_ticker_rejects_invalid(raw):
    with pytest.raises(HTTPException) as e:
        clean_ticker(raw)
    assert e.value.status_code == 400


# ---------- app-level: headers + sanitized errors ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "sec.db"))
    import importlib
    from app import config, main as main_module
    importlib.reload(config)
    importlib.reload(main_module)
    with TestClient(main_module.app) as c:
        from tests.conftest import authenticate
        authenticate(c)
        yield c


def test_security_headers_present(client):
    r = client.get("/api/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "same-origin"


def test_invalid_ticker_rejected_by_route(client):
    assert client.get("/api/chart/AA_PL").status_code == 400
    assert client.get("/api/chart/AAPL?interval=nope").status_code == 400


def test_chart_error_is_sanitized(client, monkeypatch):
    from app import chart_data

    def boom(ticker, interval):
        raise RuntimeError("secret internal path /home/user/stocks.db")

    monkeypatch.setattr(chart_data, "get_bars", boom)
    r = client.get("/api/chart/AAPL")
    assert r.status_code == 502
    assert "secret" not in r.text
    assert r.json()["detail"] == "chart data unavailable"


def test_refresh_rate_limited(client, monkeypatch):
    from app import security
    monkeypatch.setattr(security.limiter, "check", lambda *a, **k: 30.0)
    r = client.post("/api/refresh/usaspending")
    assert r.status_code == 429
    assert r.headers.get("Retry-After") == "30"


# ---------- _client_ip: the proxy trust boundary ----------

def _req(peer, **headers):
    """A bare Request with a chosen socket peer and headers."""
    from starlette.requests import Request
    return Request({
        "type": "http", "method": "GET", "path": "/", "query_string": b"",
        "scheme": "http", "client": (peer, 1234) if peer else None,
        "headers": [(k.replace("_", "-").lower().encode(), v.encode())
                    for k, v in headers.items()],
    })


@pytest.fixture
def loopback_only(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "TRUSTED_PROXY_IPS", frozenset({"127.0.0.1", "::1"}))


def test_client_ip_direct_peer_is_used(loopback_only):
    from app.security import _client_ip
    assert _client_ip(_req("203.0.113.9")) == "203.0.113.9"


def test_client_ip_ignores_forged_header_from_untrusted_peer(loopback_only):
    """The spoofing case: anyone may send XFF, only a trusted peer is believed."""
    from app.security import _client_ip
    req = _req("203.0.113.9", x_forwarded_for="1.2.3.4")
    assert _client_ip(req) == "203.0.113.9"


def test_client_ip_trusts_header_from_loopback_proxy(loopback_only):
    from app.security import _client_ip
    req = _req("127.0.0.1", x_forwarded_for="203.0.113.9")
    assert _client_ip(req) == "203.0.113.9"


def test_client_ip_takes_rightmost_untrusted_hop(loopback_only):
    """Each proxy appends, so the leftmost entry is client-controlled."""
    from app.security import _client_ip
    req = _req("127.0.0.1", x_forwarded_for="1.1.1.1, 203.0.113.9, 127.0.0.1")
    assert _client_ip(req) == "203.0.113.9"


def test_client_ip_falls_back_to_peer_without_header(loopback_only):
    """Degrades to today's behaviour when the ingress forwards nothing."""
    from app.security import _client_ip
    assert _client_ip(_req("127.0.0.1")) == "127.0.0.1"


def test_client_ip_unknown_without_client(loopback_only):
    from app.security import _client_ip
    assert _client_ip(_req(None)) == "unknown"


# ---------- HSTS: only on real HTTPS ----------

def test_hsts_absent_when_disabled(loopback_only, monkeypatch):
    from app import config
    from app.security import _is_https
    monkeypatch.setattr(config, "HSTS_SECONDS", 0)
    # Nothing to assert on the header itself; the guard is the config value.
    assert config.HSTS_SECONDS == 0
    assert _is_https(_req("127.0.0.1", x_forwarded_proto="https")) is True


def test_is_https_false_for_plain_local_request(loopback_only):
    """The load-bearing case: an HSTS header on http://localhost would pin that
    origin to HTTPS in the browser profile forever and break dev."""
    from app.security import _is_https
    assert _is_https(_req("127.0.0.1")) is False


def test_is_https_ignores_forwarded_proto_from_untrusted_peer(loopback_only):
    from app.security import _is_https
    assert _is_https(_req("203.0.113.9", x_forwarded_proto="https")) is False
