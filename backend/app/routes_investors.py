"""Famous investors' 13F holdings: the overview and who holds a ticker."""
from fastapi import APIRouter, Depends

from app import auth
from app.sources import holdings13f
from app.validation import clean_ticker


def build_router(conn) -> APIRouter:
    router = APIRouter(prefix="/api/13f")

    @router.get("")
    def overview(user=Depends(auth.get_current_user)):
        return holdings13f.overview(conn)

    @router.get("/{ticker}")
    def holders(ticker: str, user=Depends(auth.get_current_user)):
        return {"ticker": clean_ticker(ticker), "holders": holdings13f.holders_of(conn, clean_ticker(ticker))}

    return router
