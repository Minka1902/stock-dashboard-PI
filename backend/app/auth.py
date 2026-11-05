"""Accounts, sessions and TOTP 2FA.

Design:
- Passwords hashed with Argon2id (argon2-cffi defaults).
- Sessions are opaque random tokens; only their SHA-256 lands in the DB. The
  raw token travels in an httpOnly SameSite=Lax cookie, so the browser (and the
  plain <a href> report links) authenticate without any JS-visible secret.
- 2FA is mandatory: a session moves 'totp_setup' -> 'active' at enrollment, or
  'pending_totp' -> 'active' at login. The token is rotated on every state
  upgrade so a leaked pre-2FA cookie can never become an active one.
- Recovery codes are single-use, stored hashed.
"""
import hashlib
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone

import pyotp
import segno
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import HTTPException, Request, Response

from app import config, db
from app.models import AuthSession, User

logger = logging.getLogger(__name__)

_hasher = PasswordHasher()

SESSION_COOKIE = "session"
TOTP_ISSUER = "Stock Signal Dashboard"

STATE_SETUP = "totp_setup"      # registered, must enroll an authenticator
STATE_PENDING = "pending_totp"  # password OK, must enter a code
STATE_ACTIVE = "active"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ---------- passwords ----------

def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError:
        return False


# ---------- TOTP ----------

def new_totp_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=TOTP_ISSUER)


def qr_data_uri(uri: str) -> str:
    """PNG data URI of the otpauth:// QR.

    A crisp raster (black-on-white, baked in) rather than an inline stroke SVG:
    downscaling segno's stroke-based SVG blurs the modules enough that phone
    cameras can't lock on, whereas a high-res PNG stays sharp at any display size.
    """
    return segno.make(uri, error="m").png_data_uri(scale=8, border=4)


def verify_totp(secret: str, code: str) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


# Last TOTP time-step accepted for a step-up, per (user, secret): a code
# proves "the owner is here now" once, so the same code can't be used twice;
# re-enrolling an authenticator starts a fresh count.
_STEP_UP_USED: dict[tuple[int, str], int] = {}


def verify_step_up(user, code: str) -> bool:
    """A fresh authenticator code for a sensitive action (STC-51): valid now
    (±30s) and not already used for a step-up."""
    if not user.totp_secret or not verify_totp(user.totp_secret, code):
        return False
    totp = pyotp.TOTP(user.totp_secret)
    now = int(time.time())
    step = next((now // 30 + d for d in (0, -1, 1) if totp.at(now + d * 30) == code.strip()), None)
    key = (user.id, user.totp_secret)
    if step is None or _STEP_UP_USED.get(key, -1) >= step:
        return False
    _STEP_UP_USED[key] = step
    return True


# ---------- recovery codes ----------

def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_recovery_codes(n: int = 8) -> tuple[list[str], list[str]]:
    """Return (plaintext codes shown once, their hashes for storage)."""
    codes = []
    for _ in range(n):
        raw = secrets.token_hex(6)  # 12 hex chars
        codes.append(f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}")
    return codes, [_hash_token(c) for c in codes]


def hash_recovery_code(code: str) -> str:
    return _hash_token((code or "").strip().lower())


# ---------- sessions ----------

def start_session(conn, user_id: int, state: str) -> str:
    """Create a session row and return the raw cookie token."""
    token = secrets.token_urlsafe(32)
    now = _now()
    ttl = config.SESSION_TTL_SECONDS if state == STATE_ACTIVE \
        else config.PENDING_SESSION_TTL_SECONDS
    db.create_session(conn, AuthSession(
        token_hash=_hash_token(token),
        user_id=user_id,
        state=state,
        created_at=_iso(now),
        expires_at=_iso(now + timedelta(seconds=ttl)),
        last_seen_at=_iso(now),
    ))
    return token


def upgrade_session(conn, request: Request, new_state: str) -> str:
    """Rotate the current session token into `new_state`; returns the new token."""
    old = _session_from_request(conn, request)
    if old is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    db.delete_session(conn, old.token_hash)
    return start_session(conn, old.user_id, new_state)


def end_session(conn, request: Request) -> None:
    session = _session_from_request(conn, request)
    if session is not None:
        db.delete_session(conn, session.token_hash)


def set_session_cookie(response: Response, token: str, max_age: int | None = None) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age if max_age is not None else config.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=config.COOKIE_SECURE,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def _session_from_request(conn, request: Request) -> AuthSession | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    session = db.get_session(conn, _hash_token(token))
    if session is None:
        return None
    try:
        expires = datetime.fromisoformat(session.expires_at)
    except ValueError:
        return None
    if expires < _now():
        db.delete_session(conn, session.token_hash)
        return None
    return session


def resolve_user(conn, request: Request) -> User | None:
    """The active-session user, or None. Called once per request by middleware."""
    session = _session_from_request(conn, request)
    if session is None or session.state != STATE_ACTIVE:
        return None
    return db.get_user(conn, session.user_id)


# ---------- FastAPI dependencies ----------

def get_current_user(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


def get_pending_session(conn, request: Request, states: tuple[str, ...]) -> AuthSession:
    session = _session_from_request(conn, request)
    if session is None or session.state not in states:
        raise HTTPException(status_code=401, detail="not authenticated")
    return session
