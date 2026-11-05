"""Chart workspace extras: drawing drafts and the extended-hours print.

Kept out of main.py as its own router (same factory shape as routes_auth) so
the chart feature's routes live together.

- Drawing drafts are named, per-user snapshots of a ticker's drawings — the
  live set stays in `drawings` (one row per user+ticker, see main.py), a draft
  is something the user saved on purpose with a title and description.
  Every read/write is owner-checked: another account's draft id answers 404,
  exactly like a missing one, so ids don't leak across accounts.
- `/api/chart/{ticker}/extended` is the latest pre-market / after-hours print
  for the daily+ chart's "Pre" / "After" price lines. Real 1m extended-hours
  bars only; null when there is no current extended print.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app import auth, chart_data, db
from app.validation import clean_ticker

logger = logging.getLogger(__name__)

# Same bound as the live drawings set (main.py::_MAX_SHAPES).
MAX_DRAFT_SHAPES = 200
# A user can keep plenty of drafts per ticker; this only stops runaway growth.
MAX_DRAFTS_PER_TICKER = 100
TITLE_MAX = 120
DESCRIPTION_MAX = 2000
TIMEFRAME_MAX = 8


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _strip_title(v):
    if v is None:
        return v
    v = str(v).strip()
    if not v:
        raise ValueError("title is required")
    return v


class DraftCreate(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    description: str = Field(default="", max_length=DESCRIPTION_MAX)
    timeframe: str = Field(default="", max_length=TIMEFRAME_MAX)
    shapes: list[dict] = Field(default_factory=list, max_length=MAX_DRAFT_SHAPES)

    _title = field_validator("title")(_strip_title)


class DraftUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)
    description: str | None = Field(default=None, max_length=DESCRIPTION_MAX)
    timeframe: str | None = Field(default=None, max_length=TIMEFRAME_MAX)
    shapes: list[dict] | None = Field(default=None, max_length=MAX_DRAFT_SHAPES)

    _title = field_validator("title")(_strip_title)


def build_router(conn) -> APIRouter:
    router = APIRouter()

    @router.get("/api/chart/{ticker}/extended")
    def extended_hours(ticker: str, user=Depends(auth.get_current_user)):
        t = clean_ticker(ticker)
        try:
            return chart_data.get_extended(t)
        except Exception:
            logger.warning("extended-hours fetch failed for %s", t, exc_info=True)
            raise HTTPException(status_code=502, detail="extended-hours data unavailable")

    @router.get("/api/drawings/{ticker}/drafts")
    def list_drafts(ticker: str, user=Depends(auth.get_current_user)):
        return {"drafts": db.list_drawing_drafts(conn, user.id, clean_ticker(ticker))}

    @router.post("/api/drawings/{ticker}/drafts", status_code=201)
    def create_draft(ticker: str, body: DraftCreate, user=Depends(auth.get_current_user)):
        t = clean_ticker(ticker)
        if len(db.list_drawing_drafts(conn, user.id, t)) >= MAX_DRAFTS_PER_TICKER:
            raise HTTPException(
                status_code=400,
                detail=f"at most {MAX_DRAFTS_PER_TICKER} drafts per ticker — delete one first",
            )
        return db.create_drawing_draft(
            conn, user.id, t, body.title, body.description.strip(),
            body.timeframe, body.shapes, _now(),
        )

    @router.put("/api/drawings/drafts/{draft_id}")
    def update_draft(draft_id: int, body: DraftUpdate, user=Depends(auth.get_current_user)):
        updated = db.update_drawing_draft(
            conn, user.id, draft_id, _now(),
            title=body.title,
            description=body.description.strip() if body.description is not None else None,
            timeframe=body.timeframe,
            shapes=body.shapes,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="draft not found")
        return updated

    @router.delete("/api/drawings/drafts/{draft_id}")
    def delete_draft(draft_id: int, user=Depends(auth.get_current_user)):
        if not db.delete_drawing_draft(conn, user.id, draft_id):
            raise HTTPException(status_code=404, detail="draft not found")
        return {"deleted": draft_id}

    return router
