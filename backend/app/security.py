"""Web-layer protections: security headers and in-process rate limiting.

Both are deliberately in-memory and single-process — this app runs as one
uvicorn worker (the scheduler, TTL caches and shared SQLite connection all
assume that). See docs/scaling-roadmap.md before adding workers.
"""
import logging
import threading
import time

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

from app import config

logger = logging.getLogger(__name__)


def _is_https(request) -> bool:
    """True for requests that really arrived over TLS.

    request.url.scheme is already rewritten to https by uvicorn's
    ProxyHeadersMiddleware when a trusted peer sends X-Forwarded-Proto; the
    header check is the fallback for when that layer is disabled.
    """
    if request.url.scheme == "https":
        return True
    peer = request.client.host if request.client else ""
    return (
        peer in config.TRUSTED_PROXY_IPS
        and request.headers.get("x-forwarded-proto", "") == "https"
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        _probe_once(request)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        # "Anyone with the link" should not mean "anyone with a search engine".
        response.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        # HSTS only on real HTTPS: see config.HSTS_SECONDS for why an
        # unconditional header would break local dev permanently.
        if config.HSTS_SECONDS and _is_https(request):
            response.headers.setdefault(
                "Strict-Transport-Security", f"max-age={config.HSTS_SECONDS}")
        if request.url.path.startswith("/api/auth/"):
            # Auth responses carry codes/secrets — never let a cache keep them.
            response.headers["Cache-Control"] = "no-store"
        return response


_probe_logged = False


def _probe_once(request) -> None:
    """Log the proxy-header shape of the first request from outside loopback.

    Whether an ingress forwards the client IP decides whether the anonymous
    rate-limit buckets are per-visitor or one shared bucket, and the only
    reliable way to find out for a given tunnel is to look. Fires once per
    process, at INFO, into the normal backend log.
    """
    global _probe_logged
    if _probe_logged:
        return
    host = request.headers.get("host", "")
    if host.startswith(("127.0.0.1", "localhost", "[::1]")):
        return  # a local health poll tells us nothing
    _probe_logged = True
    logger.info(
        "ingress probe: peer=%s host=%s xff=%r xfp=%r funnel=%r resolved=%s",
        request.client.host if request.client else None,
        host,
        request.headers.get("x-forwarded-for"),
        request.headers.get("x-forwarded-proto"),
        request.headers.get("tailscale-funnel-request"),
        _client_ip(request),
    )


class RateLimiter:
    """Fixed-window counter keyed by (bucket, key). Thread-safe.

    Windows are coarse by design: good enough to blunt brute force and
    accidental hammering, tiny enough to need no external store.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._counts: dict[tuple[str, str], tuple[float, int]] = {}

    def check(self, bucket: str, key: str, limit: int, window_seconds: int,
              now: float | None = None) -> float | None:
        """Count one hit. Returns None if allowed, else seconds until reset."""
        now = time.monotonic() if now is None else now
        with self._lock:
            window_start, count = self._counts.get((bucket, key), (now, 0))
            if now - window_start >= window_seconds:
                window_start, count = now, 0
            count += 1
            self._counts[(bucket, key)] = (window_start, count)
            if count > limit:
                return max(1.0, window_seconds - (now - window_start))
            return None

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


limiter = RateLimiter()


def _client_ip(request: Request) -> str:
    """The caller's IP, honouring X-Forwarded-For only from a trusted proxy.

    Behind an ingress that terminates TLS on this machine (Tailscale Funnel,
    nginx, Caddy) every socket peer is loopback, so keying the rate limiter on
    the peer alone puts every anonymous visitor in ONE bucket — a single
    attacker then locks out everybody. Trusting the header unconditionally is
    the opposite mistake: anyone could send a made-up X-Forwarded-For and get
    themselves a private bucket. So the header is read only from a trusted peer.

    uvicorn's own ProxyHeadersMiddleware (on by default, trusted_hosts
    "127.0.0.1") may already have rewritten request.client from this header. If
    it did, the peer we see is the real client and is NOT in the trusted set, so
    we return it unchanged and never double-parse. The two layers compose.
    """
    peer = request.client.host if request.client else ""
    if not peer:
        return "unknown"
    if peer not in config.TRUSTED_PROXY_IPS:
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    # Rightmost untrusted hop: each proxy appends, so the leftmost entry is
    # whatever the client chose to claim. Same rule uvicorn applies.
    for hop in reversed([p.strip() for p in forwarded.split(",")]):
        if hop and hop not in config.TRUSTED_PROXY_IPS:
            return hop
    return peer


def rate_limit(bucket: str, limit: int, window_seconds: int):
    """FastAPI dependency factory: 429 (+ Retry-After) past `limit` hits per window.

    Keys on the authenticated user when available (set by the auth middleware),
    falling back to client IP for anonymous endpoints like login.
    """

    def dependency(request: Request) -> None:
        user = getattr(request.state, "user", None)
        key = f"user:{user.id}" if user is not None else f"ip:{_client_ip(request)}"
        retry_after = limiter.check(bucket, key, limit, window_seconds)
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail="too many requests",
                headers={"Retry-After": str(int(retry_after))},
            )

    return dependency
