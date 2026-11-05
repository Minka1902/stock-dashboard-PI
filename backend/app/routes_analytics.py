"""Portfolio performance & risk analytics (STC-36): one endpoint, one account.

`account` is "manual", "paper" or "bot:<id>" — a bot account only when the
bot belongs to the caller (another account's bot answers 404, like a missing
one, so ids don't leak).
"""
from fastapi import APIRouter, Depends, HTTPException

from app import analytics, auth, db
from app.ledger import BOT_ACCOUNT


def build_router(conn) -> APIRouter:
    router = APIRouter()

    def own_account(account: str, user_id: int) -> str:
        if account in ("manual", "paper"):
            return account
        if BOT_ACCOUNT.fullmatch(account or ""):
            bot = db.get_bot(conn, int(account.split(":")[1]))
            if bot is not None and bot.user_id == user_id:
                return account
            raise HTTPException(status_code=404, detail="no such bot")
        raise HTTPException(status_code=400, detail="account must be 'manual', 'paper' or 'bot:<id>'")

    @router.get("/api/portfolio/analytics")
    def portfolio_analytics(account: str = "manual", user=Depends(auth.get_current_user)):
        return analytics.report(conn, user.id, own_account(account, user.id))

    return router
