"""The Time Machine's API: replay a trade plan from the stock's own days."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import auth, quotes, timemachine
from app.routes_rules import history_bars
from app.validation import clean_ticker


class PlanBody(BaseModel):
    ticker: str
    stop: float
    target: float | None = None
    entry: float | None = None          # default: the live price, else the last close
    horizon_days: int = Field(20, ge=1, le=120)
    replays: int = Field(10_000, ge=1_000, le=20_000)
    seed: int = 19


def build_router(conn) -> APIRouter:
    router = APIRouter(prefix="/api/timemachine")

    @router.post("")
    def replay(body: PlanBody, user=Depends(auth.get_current_user)):
        t = clean_ticker(body.ticker)
        bars, data = history_bars(conn, t)
        bars = [b for b in bars if b.date < date.today().isoformat()]
        entry, entry_source = body.entry, "your entry"
        if entry is None:
            q = next(iter(quotes.get_quotes([t])), None)
            entry, entry_source = (q.price, "live price") if q and q.price else \
                (bars[-1].close, f"the {bars[-1].date} close") if bars else (None, None)
        if entry is None:
            raise HTTPException(status_code=422, detail={"message": f"No price or history for {t}",
                                                          "errors": {"ticker": "no data"}})
        try:
            out = timemachine.run(bars, entry, body.stop, body.target, body.horizon_days, body.replays, body.seed)
        except ValueError as exc:
            field, _, msg = str(exc).partition("|")
            raise HTTPException(status_code=422, detail={"message": msg, "errors": {field: msg}}) from None
        return {"ticker": t, "data": data, "entry_source": entry_source, **out}

    return router
