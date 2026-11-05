"""In-app update routes: /api/update/status (any user) and /api/update/apply (admin).

Kept out of main.py so the update feature is one self-contained unit: the git
logic is in app/updater.py, the work itself in windows/update.ps1.
"""
import logging
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app import auth, updater
from app.security import rate_limit
from app.version import __version__

logger = logging.getLogger(__name__)

# Serializes the precondition check + spawn so a double click can't start two
# updaters between the "is one running?" read and the status-file write.
_apply_lock = threading.Lock()


def _require_admin(user=Depends(auth.get_current_user)):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return user


def _payload(check: updater.UpdateCheck | None, user) -> dict:
    return {
        "current": {
            "version": __version__,
            # What the running process was started from; differs from
            # check.current_commit when the tree moved under a live server.
            "commit": updater.STARTUP_COMMIT,
        },
        "check": check.model_dump() if check else None,
        "apply": updater.read_apply_status(),
        "viewer_is_admin": bool(user.is_admin),
    }


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/update")

    @router.get("/status")
    def update_status(request: Request, refresh: bool = False,
                      user=Depends(auth.get_current_user)):
        """Update availability for everyone; apply progress when one is running.

        `refresh=true` forces a fresh `git fetch` (rate-limited — it is a
        network round trip to GitHub, and it holds a worker thread).
        """
        if refresh:
            rate_limit("update_check", 6, 60)(request)
            if updater.is_apply_running():
                check = updater.cached()
            else:
                check = updater.check(force=True)
        else:
            check = updater.cached() or (
                None if updater.is_apply_running() else updater.check())
        return _payload(check, user)

    @router.post(
        "/apply",
        status_code=202,
        dependencies=[Depends(rate_limit("update_apply", 3, 300))],
    )
    def update_apply(request: Request, user=Depends(_require_admin)):
        with _apply_lock:
            if updater.is_apply_running():
                raise HTTPException(status_code=409, detail="an update is already running")
            # Re-check against GitHub now; the cached result may be an hour old.
            check = updater.check(force=True)
            if not check.can_apply:
                raise HTTPException(status_code=409,
                                    detail=check.blocked_reason or "update not possible")
            mode = updater.detect_mode()
            server = request.scope.get("server") or ("127.0.0.1", 8000)
            port = int(server[1] or 8000)
            status = updater.initial_status(check.current_commit, check.remote_commit, mode)
            updater.write_status(status)
            try:
                updater.spawn_update(check.current_commit, mode, port)
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                logger.exception("could not start the updater")
                status.update(state="failed", message=f"could not start the updater: {exc}")
                updater.write_status(status)
                raise HTTPException(status_code=500,
                                    detail=f"could not start the updater: {exc}") from exc
            logger.warning("update started by %s: %s -> %s (%s mode)",
                           user.email, check.current_commit, check.remote_commit, mode)
        return JSONResponse(status_code=202, content=_payload(check, user))

    return router
