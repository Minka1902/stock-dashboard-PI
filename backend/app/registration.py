"""Who may create an account.

Being reachable and being open for signup are different decisions. Once the
dashboard is served on a URL anyone can load, account creation has to be gated
separately — an account here reads and writes one person's portfolio.

There are exactly two ways an account comes into existence and BOTH route
through here: the password form (``routes_auth.register``) and the auto-create
inside ``routes_oauth._resolve_user``. A third creation path that forgets to
call this is the regression to watch for.
"""
import secrets

from fastapi import HTTPException

from app import config

OPEN, INVITE, CLOSED = "open", "invite", "closed"


class RegistrationClosed(Exception):
    """Raised on the OAuth path, where the answer is a redirect, not a 403."""


def may_register(invite_code: str = "") -> bool:
    """Read config at call time, never bind at import.

    routes_oauth.py reads its credentials the same way, and it is what lets a
    test monkeypatch app.config without reloading four modules.
    """
    mode = config.REGISTRATION_MODE
    if mode == OPEN:
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
