"""Who may create an account, and the one function that creates it.

Being reachable and being open for signup are different decisions. Once the
dashboard is served on a URL anyone can load, account creation has to be gated
separately — an account here reads and writes one person's portfolio.

There are exactly two ways an account comes into existence and BOTH route
through here: the password form (``routes_auth.register``) and the auto-create
inside ``routes_oauth._resolve_user``. Both gate with ``may_register`` and
create with ``create_account``. A third creation path that skips either is the
regression to watch for.

In ``approval`` mode anyone may sign up, but only STOCKS_ADMIN_EMAIL is created
approved (and admin); every other account is pending until the admin approves
it on the Server page, and every admin is alerted when one arrives.
"""
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException

from app import config, db, notify
from app.models import Alert, User

OPEN, INVITE, APPROVAL, CLOSED = "open", "invite", "approval", "closed"


class RegistrationClosed(Exception):
    """Raised on the OAuth path, where the answer is a redirect, not a 403."""


def may_register(invite_code: str = "") -> bool:
    """Read config at call time, never bind at import.

    routes_oauth.py reads its credentials the same way, and it is what lets a
    test monkeypatch app.config without reloading four modules.
    """
    mode = config.REGISTRATION_MODE
    if mode in (OPEN, APPROVAL):
        return True
    # The INVITE_CODE truthiness check is belt-and-braces against config's
    # import-time validation being bypassed (a test setting it to ""):
    # compare_digest("", "") is True, which would silently open the door.
    if mode == INVITE and config.INVITE_CODE:
        return secrets.compare_digest((invite_code or "").strip(), config.INVITE_CODE)
    return False


def assert_may_register(invite_code: str = "") -> None:
    if not may_register(invite_code):
        raise HTTPException(status_code=403, detail="registration is closed")


def _alert_admins(conn, user: User, now: str) -> None:
    alerts = [
        Alert(
            dedup_key=f"signup:{user.id}:{admin.id}", created_at=now, ticker="",
            type="signup", severity="high", title="New sign-up waiting for approval",
            message=f"{user.email} signed up and can't see anything until you approve "
                    "or reject the account on the Server page.",
            user_id=admin.id,
        )
        for admin in db.get_users(conn) if admin.is_admin
    ]
    db.upsert_alerts(conn, alerts)
    notify.push_stored(conn, alerts)


def create_account(conn, email: str, password_hash: str) -> User:
    """Create an account after `may_register` said yes.

    approval mode: STOCKS_ADMIN_EMAIL is the approved admin, everyone else is
    pending. Other modes: every account is approved and the first one is admin.
    The admin claims the rows of a pre-multi-user database.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if config.REGISTRATION_MODE == APPROVAL:
        admin = email.lower() == config.ADMIN_EMAIL
        approved = admin
    else:
        admin = db.count_users(conn) == 0
        approved = True
    user = db.create_user(conn, email, password_hash, now, is_admin=admin, approved=approved)
    if admin:
        db.claim_legacy_rows(conn, user.id)
    if not approved:
        _alert_admins(conn, user, now)
    return user
