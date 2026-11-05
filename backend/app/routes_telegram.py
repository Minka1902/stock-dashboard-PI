"""Linking a Telegram chat (STC-79): status, a one-time code, on/off, unlink."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app import audit, auth, db, telegram


class EnabledBody(BaseModel):
    enabled: bool


def build_router(conn) -> APIRouter:
    router = APIRouter(prefix="/api/telegram")

    def view(user_id: int) -> dict:
        link = db.get_telegram_link(conn, user_id=user_id)
        return {"configured": telegram.configured(),
                "bot_username": telegram.bot_username() if telegram.configured() else None,
                "linked": link is not None,
                "username": link["username"] if link else None,
                "linked_at": link["linked_at"] if link else None,
                "enabled": link["enabled"] if link else False}

    @router.get("")
    def status(user=Depends(auth.get_current_user)):
        return view(user.id)

    @router.post("/link")
    def link(user=Depends(auth.get_current_user)):
        if not telegram.configured():
            raise HTTPException(status_code=409, detail="Telegram isn't set up on this server "
                                                        "(STOCKS_TELEGRAM_BOT_TOKEN is unset)")
        code, expires = telegram.issue_code(conn, user.id)
        name = telegram.bot_username()
        return {"code": code, "expires_at": expires, "bot_username": name,
                "deep_link": f"https://t.me/{name}?start={code}" if name else None}

    @router.put("")
    def set_enabled(body: EnabledBody, user=Depends(auth.get_current_user)):
        if not db.set_telegram_enabled(conn, user.id, body.enabled):
            raise HTTPException(status_code=409, detail="no Telegram chat is linked")
        return view(user.id)

    @router.delete("")
    def unlink(user=Depends(auth.get_current_user)):
        if db.delete_telegram_link(conn, user_id=user.id):
            audit.record(conn, user.id, "telegram_unlinked")
        return view(user.id)

    return router
