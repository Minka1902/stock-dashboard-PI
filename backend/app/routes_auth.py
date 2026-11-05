"""Auth API: register -> enroll TOTP -> active; login -> TOTP challenge -> active.

Built as a factory so the router closes over the app's shared SQLite
connection (mirrors how main.py's fetchers use it) without importing main.
"""
import logging
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app import auth, config, db, registration
from app.security import limiter, rate_limit

logger = logging.getLogger(__name__)

# A pending session gets this many wrong TOTP/recovery entries before it is
# revoked and the user must redo the password step.
MAX_CHALLENGE_ATTEMPTS = 5


class Credentials(BaseModel):
    email: str
    password: str


class RegisterBody(Credentials):
    invite_code: str = ""


class CodeBody(BaseModel):
    code: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_router(conn) -> APIRouter:
    router = APIRouter(prefix="/api/auth")

    # Wrong-code counter per pending-session token hash (in-memory: a process
    # restart resets counts, but also invalidates nothing security-critical).
    attempts_lock = threading.Lock()
    challenge_attempts: dict[str, int] = {}

    def _too_many_attempts(token_hash: str) -> bool:
        with attempts_lock:
            challenge_attempts[token_hash] = challenge_attempts.get(token_hash, 0) + 1
            return challenge_attempts[token_hash] > MAX_CHALLENGE_ATTEMPTS

    def _clear_attempts(token_hash: str) -> None:
        with attempts_lock:
            challenge_attempts.pop(token_hash, None)

    @router.get("/registration")
    def registration_mode():
        """Whether to offer a sign-up tab, and whether it needs a code."""
        return {"mode": config.REGISTRATION_MODE}

    # The IP bucket is a coarse global backstop, not the real limit: behind an
    # ingress that does not forward client IPs every caller shares it, and a
    # tight ceiling would lock *everyone* out. See the per-email bucket in
    # login() for the protection that survives a shared IP.
    @router.post("/register", dependencies=[Depends(rate_limit("auth_register", 20, 3600))])
    def register(body: RegisterBody, response: Response):
        # Gate first: a stranger should not learn the validation rules.
        registration.assert_may_register(body.invite_code)
        email = body.email.strip().lower()
        if "@" not in email or len(email) < 5 or len(email) > 254:
            raise HTTPException(status_code=400, detail="invalid email")
        if not (8 <= len(body.password) <= 128):
            raise HTTPException(status_code=400, detail="password must be 8-128 characters")
        if db.get_user_by_email(conn, email) is not None:
            raise HTTPException(status_code=409, detail="email already registered")
        first_user = db.count_users(conn) == 0
        user = db.create_user(
            conn, email, auth.hash_password(body.password), _now_iso(), is_admin=first_user,
        )
        if first_user:
            db.claim_legacy_rows(conn, user.id)
            logger.info("first user registered (admin): %s", email)
        token = auth.start_session(conn, user.id, auth.STATE_SETUP)
        auth.set_session_cookie(response, token, max_age=None)
        return {"status": "totp_setup_required"}

    @router.get("/totp/setup")
    def totp_setup(request: Request):
        session = auth.get_pending_session(
            conn, request, (auth.STATE_SETUP, auth.STATE_PENDING))
        user = db.get_user(conn, session.user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        if user.totp_enabled:
            raise HTTPException(status_code=409, detail="2FA already enabled")
        secret = user.totp_secret or auth.new_totp_secret()
        if user.totp_secret != secret:
            db.set_totp_secret(conn, user.id, secret)
        uri = auth.provisioning_uri(secret, user.email)
        return {"otpauth_uri": uri, "qr_png": auth.qr_data_uri(uri), "secret": secret}

    @router.post("/totp/enable", dependencies=[Depends(rate_limit("auth_totp", 60, 60))])
    def totp_enable(body: CodeBody, request: Request, response: Response):
        session = auth.get_pending_session(
            conn, request, (auth.STATE_SETUP, auth.STATE_PENDING))
        user = db.get_user(conn, session.user_id)
        if user is None or not user.totp_secret:
            raise HTTPException(status_code=400, detail="TOTP setup not started")
        if user.totp_enabled:
            raise HTTPException(status_code=409, detail="2FA already enabled")
        if not auth.verify_totp(user.totp_secret, body.code):
            raise HTTPException(status_code=400, detail="invalid code")
        db.enable_totp(conn, user.id)
        codes, hashes = auth.generate_recovery_codes()
        db.replace_recovery_codes(conn, user.id, hashes)
        token = auth.upgrade_session(conn, request, auth.STATE_ACTIVE)
        auth.set_session_cookie(response, token)
        return {"user": user.public(), "recovery_codes": codes}

    @router.post("/login", dependencies=[Depends(rate_limit("auth_login", 60, 60))])
    def login(body: Credentials, response: Response):
        db.purge_expired_sessions(conn, _now_iso())
        email = body.email.strip().lower()
        # Keyed on the account under attack, not the caller's address. Behind a
        # tunnel that does not forward the client IP an IP-keyed bucket is a
        # global one, where 5 wrong guesses would lock out every user; this
        # throttles exactly the account being guessed at.
        retry_after = limiter.check("auth_login_email", email, 5, 60)
        if retry_after is not None:
            raise HTTPException(
                status_code=429,
                detail="too many requests",
                headers={"Retry-After": str(int(retry_after))},
            )
        user = db.get_user_by_email(conn, email)
        # Constant response shape for wrong email vs wrong password.
        if user is None or not auth.verify_password(user.password_hash, body.password):
            raise HTTPException(status_code=401, detail="invalid credentials")
        if not user.totp_enabled:
            # Registration was abandoned before enrollment — resume it.
            token = auth.start_session(conn, user.id, auth.STATE_SETUP)
            auth.set_session_cookie(response, token, max_age=None)
            return {"status": "totp_setup_required"}
        token = auth.start_session(conn, user.id, auth.STATE_PENDING)
        auth.set_session_cookie(response, token, max_age=None)
        return {"status": "totp_required"}

    @router.post("/totp/verify", dependencies=[Depends(rate_limit("auth_totp", 60, 60))])
    def totp_verify(body: CodeBody, request: Request, response: Response):
        session = auth.get_pending_session(conn, request, (auth.STATE_PENDING,))
        user = db.get_user(conn, session.user_id)
        if user is None or not user.totp_secret or not user.totp_enabled:
            raise HTTPException(status_code=401, detail="not authenticated")
        if not auth.verify_totp(user.totp_secret, body.code):
            if _too_many_attempts(session.token_hash):
                db.delete_session(conn, session.token_hash)
                auth.clear_session_cookie(response)
                raise HTTPException(status_code=401, detail="too many attempts; log in again")
            raise HTTPException(status_code=400, detail="invalid code")
        _clear_attempts(session.token_hash)
        token = auth.upgrade_session(conn, request, auth.STATE_ACTIVE)
        auth.set_session_cookie(response, token)
        return {"user": user.public()}

    @router.post("/recovery", dependencies=[Depends(rate_limit("auth_totp", 60, 60))])
    def recovery(body: CodeBody, request: Request, response: Response):
        session = auth.get_pending_session(conn, request, (auth.STATE_PENDING,))
        user = db.get_user(conn, session.user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        code_hash = auth.hash_recovery_code(body.code)
        if not db.consume_recovery_code(conn, user.id, code_hash, _now_iso()):
            if _too_many_attempts(session.token_hash):
                db.delete_session(conn, session.token_hash)
                auth.clear_session_cookie(response)
                raise HTTPException(status_code=401, detail="too many attempts; log in again")
            raise HTTPException(status_code=400, detail="invalid recovery code")
        _clear_attempts(session.token_hash)
        token = auth.upgrade_session(conn, request, auth.STATE_ACTIVE)
        auth.set_session_cookie(response, token)
        remaining = db.count_unused_recovery_codes(conn, user.id)
        return {"user": user.public(), "remaining_codes": remaining}

    @router.post("/logout")
    def logout(request: Request, response: Response):
        auth.end_session(conn, request)
        auth.clear_session_cookie(response)
        return {"status": "logged_out"}

    @router.get("/me")
    def me(request: Request):
        user = auth.resolve_user(conn, request)
        if user is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        return user.public()

    @router.post("/onboarded")
    def mark_onboarded(request: Request):
        """Record that this account has seen the guided tour, so it is never
        auto-started again on any device."""
        user = auth.resolve_user(conn, request)
        if user is None:
            raise HTTPException(status_code=401, detail="not authenticated")
        db.set_onboarded(conn, user.id)
        return db.get_user(conn, user.id).public()

    return router
