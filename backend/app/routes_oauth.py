"""Social login (GitHub, Google, Facebook) — OAuth 2.0 authorization code flow.

Deliberately httpx-only (no authlib): every network call goes through
``httpx.Client`` like the rest of the app, which keeps the dependency surface
small and makes the flow trivially testable with a monkeypatched client.

Social login replaces only the *password* step — mandatory TOTP 2FA is
preserved. After the provider verifies the user we resolve a local account and
start a pre-2FA session (``pending_totp`` if they already enrolled, else
``totp_setup``); the existing AuthGate status routing then drives the TOTP
challenge or enrollment. Each provider no-ops (its button is hidden) when its
client id/secret env vars are unset, matching the app's "safe when unconfigured"
convention.
"""
import logging
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app import auth, config, db, registration
from app.models import OAuthIdentity

logger = logging.getLogger(__name__)

STATE_COOKIE = "oauth_state"

# Static per-provider endpoints + scopes; credentials come from config (env).
PROVIDERS = {
    "github": {
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "scope": "read:user user:email",
    },
    "google": {
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "scope": "openid email profile",
    },
    "facebook": {
        "authorize": "https://www.facebook.com/v18.0/dialog/oauth",
        "token": "https://graph.facebook.com/v18.0/oauth/access_token",
        "scope": "email public_profile",
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _creds(provider: str) -> tuple[str, str]:
    return {
        "github": (config.OAUTH_GITHUB_CLIENT_ID, config.OAUTH_GITHUB_CLIENT_SECRET),
        "google": (config.OAUTH_GOOGLE_CLIENT_ID, config.OAUTH_GOOGLE_CLIENT_SECRET),
        "facebook": (config.OAUTH_FACEBOOK_CLIENT_ID, config.OAUTH_FACEBOOK_CLIENT_SECRET),
    }.get(provider, ("", ""))


def is_configured(provider: str) -> bool:
    cid, secret = _creds(provider)
    return bool(cid and secret)


def _redirect_uri(provider: str) -> str:
    return f"{config.OAUTH_REDIRECT_BASE}/api/auth/oauth/{provider}/callback"


def _frontend_origin() -> str:
    return config.CORS_ORIGINS[0] if config.CORS_ORIGINS else "http://localhost:5173"


# ---- token exchange + verified-identity fetch (per provider) ----

def _exchange_code(client: httpx.Client, provider: str, code: str) -> str:
    cid, secret = _creds(provider)
    resp = client.post(
        PROVIDERS[provider]["token"],
        data={
            "client_id": cid,
            "client_secret": secret,
            "code": code,
            "redirect_uri": _redirect_uri(provider),
            "grant_type": "authorization_code",
        },
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise ValueError("no access_token in provider response")
    return token


def _fetch_identity(client: httpx.Client, provider: str, token: str) -> tuple[str, str]:
    """Return (provider_user_id, verified_email). Raises if no verified email."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if provider == "github":
        u = client.get("https://api.github.com/user", headers=headers)
        u.raise_for_status()
        uid = str(u.json().get("id") or "")
        emails = client.get("https://api.github.com/user/emails", headers=headers)
        emails.raise_for_status()
        email = ""
        for e in emails.json():
            if e.get("primary") and e.get("verified"):
                email = (e.get("email") or "").lower()
                break
        return uid, email
    if provider == "google":
        r = client.get("https://openidconnect.googleapis.com/v1/userinfo", headers=headers)
        r.raise_for_status()
        data = r.json()
        if not data.get("email_verified"):
            return str(data.get("sub") or ""), ""
        return str(data.get("sub") or ""), (data.get("email") or "").lower()
    if provider == "facebook":
        r = client.get(
            "https://graph.facebook.com/me",
            params={"fields": "id,email", "access_token": token},
        )
        r.raise_for_status()
        data = r.json()
        return str(data.get("id") or ""), (data.get("email") or "").lower()
    raise ValueError(f"unknown provider {provider}")


def _resolve_user(conn, provider: str, provider_user_id: str, email: str):
    """Existing identity → user; else link by verified email; else create.

    A created account gets a random unusable password hash (it signs in via
    OAuth only); the first-ever account still becomes admin.
    """
    ident = db.get_oauth_identity(conn, provider, provider_user_id)
    if ident is not None:
        return db.get_user(conn, ident.user_id)

    user = db.get_user_by_email(conn, email) if email else None
    if user is None:
        if not email:
            raise ValueError("provider did not return a verified email")
        if not registration.may_register():
            # Social login LINKS to an account; it does not create one unless
            # registration is open. Without this a stranger's Google account
            # becomes an account here just because the callback is reachable —
            # the exact hole the /register gate closes on the other path. There
            # is nowhere to type an invite code in an OAuth round trip, so
            # invite/closed both reduce OAuth to link-only.
            raise registration.RegistrationClosed()
        first = db.count_users(conn) == 0
        unusable = auth.hash_password(secrets.token_urlsafe(32))
        user = db.create_user(conn, email, unusable, _now_iso(), is_admin=first)
        if first:
            db.claim_legacy_rows(conn, user.id)

    db.create_oauth_identity(conn, OAuthIdentity(
        provider=provider, provider_user_id=provider_user_id,
        user_id=user.id, email=email, created_at=_now_iso(),
    ))
    return user


def build_router(conn) -> APIRouter:
    router = APIRouter(prefix="/api/auth/oauth")

    @router.get("/providers")
    def providers():
        # Only surface providers with credentials configured; the frontend hides
        # the rest. The app runs fine with none configured.
        return {"providers": [p for p in PROVIDERS if is_configured(p)]}

    @router.get("/{provider}/start")
    def start(provider: str):
        if provider not in PROVIDERS or not is_configured(provider):
            raise HTTPException(status_code=404, detail="provider not configured")
        cid, _ = _creds(provider)
        state = secrets.token_urlsafe(24)
        params = {
            "client_id": cid,
            "redirect_uri": _redirect_uri(provider),
            "scope": PROVIDERS[provider]["scope"],
            "response_type": "code",
            "state": state,
        }
        url = f"{PROVIDERS[provider]['authorize']}?{urlencode(params)}"
        resp = RedirectResponse(url, status_code=302)
        # Short-lived signed-by-secrecy state cookie for CSRF (double-submit).
        resp.set_cookie(
            STATE_COOKIE, state, max_age=600, httponly=True,
            samesite="lax", secure=config.COOKIE_SECURE, path="/",
        )
        return resp

    @router.get("/{provider}/callback")
    def callback(provider: str, request: Request, response: Response,
                 code: str = "", state: str = ""):
        if provider not in PROVIDERS or not is_configured(provider):
            raise HTTPException(status_code=404, detail="provider not configured")
        cookie_state = request.cookies.get(STATE_COOKIE)
        if not code or not state or not cookie_state or not secrets.compare_digest(state, cookie_state):
            return _fail_redirect("state")

        try:
            with httpx.Client(timeout=config.OAUTH_TIMEOUT_SECONDS, follow_redirects=True) as client:
                token = _exchange_code(client, provider, code)
                provider_user_id, email = _fetch_identity(client, provider, token)
            if not provider_user_id:
                return _fail_redirect("identity")
            user = _resolve_user(conn, provider, provider_user_id, email)
        except registration.RegistrationClosed:
            # Caught before the broad clause below, which would otherwise
            # report the misleading reason "exchange".
            logger.info("oauth signup refused for %s: registration closed", provider)
            return _fail_redirect("registration_closed")
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            logger.warning("oauth callback failed for %s: %s", provider, exc)
            return _fail_redirect("exchange")

        # TOTP preserved: enrolled users get a challenge, new ones enrollment.
        session_state = auth.STATE_PENDING if user.totp_enabled else auth.STATE_SETUP
        token = auth.start_session(conn, user.id, session_state)
        resp = RedirectResponse(_frontend_origin(), status_code=302)
        auth.set_session_cookie(resp, token, max_age=None)
        resp.delete_cookie(STATE_COOKIE, path="/")
        return resp

    return router


def _fail_redirect(reason: str) -> RedirectResponse:
    resp = RedirectResponse(f"{_frontend_origin()}/?oauth_error={reason}", status_code=302)
    resp.delete_cookie(STATE_COOKIE, path="/")
    return resp
