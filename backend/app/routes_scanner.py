"""The scanner's API (STC-69) and the server-sent events stream (STC-73)."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import auth, config, db, events, scanner
from app.models import ScannerPrefs


class PrefsBody(BaseModel):
    alerts_enabled: bool
    per_hour: int = Field(ge=1, le=20)


def build_router(conn) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/scanner")
    def get_scanner(user=Depends(auth.get_current_user)):
        return scanner.view(conn, user.id)

    @router.put("/scanner/prefs")
    def put_prefs(body: PrefsBody, user=Depends(auth.get_current_user)):
        return db.upsert_scanner_prefs(conn, ScannerPrefs(
            user_id=user.id, alerts_enabled=body.alerts_enabled, per_hour=body.per_hour,
            updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))).model_dump()

    @router.get("/stream")
    async def stream(request: Request, user=Depends(auth.get_current_user)):
        return StreamingResponse(
            events.stream(user.id, request.is_disconnected, config.STREAM_MAX_SECONDS),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return router
