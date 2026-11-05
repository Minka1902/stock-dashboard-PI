"""Ingestion orchestration: run a source's fetch, store results, stamp status."""
import logging
import sqlite3
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from app import db

logger = logging.getLogger(__name__)

# Sources currently inside fetch(), name -> monotonic start time. Read by the
# Server page to show what the worker is doing right now.
_RUNNING: dict[str, float] = {}
_RUNNING_LOCK = threading.Lock()


def running_sources() -> dict[str, float]:
    """Snapshot of in-flight sources -> seconds elapsed so far."""
    now = time.monotonic()
    with _RUNNING_LOCK:
        return {name: round(now - started, 3) for name, started in _RUNNING.items()}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FetchResult(list):
    """A list of records with an optional status note or warning.

    Sources with layered fallbacks return this so the status UI can show which
    tier produced the data (e.g. "ok (fallback: sentiment.xls)") — data is
    still real, never fabricated; the note just records its provenance.

    `warning` marks *degraded provenance*: the records are real and get stored,
    but the source is stamped as an error so the UI flags it (e.g. an X feed
    pulled from an unofficial mirror rather than the official API).
    """

    def __init__(self, records: list, note: str = "", warning: str = ""):
        super().__init__(records)
        self.note = note
        self.warning = warning


class SourceDeferred(Exception):
    """Raised by a fetch that cannot run *right now* but is not broken.

    The canonical case is a rate limit (GDELT's post-429 cooldown). Recording
    it as an error would page someone for a source behaving exactly as the
    upstream asked; recording nothing would hide it. It becomes a `deferred`
    run with the reason and the moment the source wants to be tried again.
    """

    def __init__(self, reason: str, retry_after_seconds: float | None = None):
        super().__init__(reason)
        self.reason = reason
        self.retry_after_seconds = retry_after_seconds


@dataclass
class RunResult:
    """What one run_source call did. `next_attempt_at` is set for deferrals
    (the scheduler may also annotate errors via db.set_source_next_attempt)."""

    outcome: str               # ok | error | deferred
    run_id: int | None = None
    record_count: int = 0
    next_attempt_at: str | None = None


def run_source(
    conn: sqlite3.Connection,
    source_name: str,
    fetch: Callable[[], list],
    store: Callable[[sqlite3.Connection, list], None],
) -> RunResult:
    """Run one source: fetch records, persist them via `store`, stamp status.

    There is deliberately no throttle here. The per-source schedule
    (app/schedules.py, one APScheduler job per source) is the only thing that
    decides *when* a source runs, so a call always fetches and there is no
    silent `skipped` outcome.

    Never raises: any failure is recorded as the source's status (with a short
    `status` string and a full `error_detail` traceback, on the status row and
    on this run's row) so the UI can show that the source tried and failed. A
    `FetchResult.warning` on an otherwise-successful fetch is stamped as an
    error status *while still storing the real records* — the "degraded
    provenance" case. A `SourceDeferred` becomes a `deferred` run.

    Every outcome appends a `source_runs` row.
    """
    started_at = _now_iso()
    started = time.perf_counter()

    with _RUNNING_LOCK:
        _RUNNING[source_name] = time.monotonic()
    try:
        records = fetch()
        store(conn, records)
        duration_ms = int((time.perf_counter() - started) * 1000)
        warning = getattr(records, "warning", "")
        if warning:
            # Degraded provenance: real data stored, but flagged as an error so
            # the UI surfaces the caveat. Keep the real record count.
            # It is *not* a success for the cadence — we want to keep retrying
            # until the source is back on its official tier.
            status = f"error: {warning}"
            db.update_source_status(
                conn, source_name, _now_iso(), status, len(records),
                error_detail=None, success=False, duration_ms=duration_ms)
            run_id = db.record_source_run(
                conn, source_name, started_at, _now_iso(), "error",
                duration_ms, len(records), status)
            return RunResult("error", run_id, len(records))
        note = getattr(records, "note", "")
        status = f"ok ({note})" if note else "ok"
        db.update_source_status(
            conn, source_name, _now_iso(), status, len(records),
            error_detail=None, success=True, duration_ms=duration_ms)
        run_id = db.record_source_run(
            conn, source_name, started_at, _now_iso(), "ok",
            duration_ms, len(records), note or None)
        return RunResult("ok", run_id, len(records))
    except SourceDeferred as exc:
        duration_ms = int((time.perf_counter() - started) * 1000)
        next_at = None
        if exc.retry_after_seconds is not None:
            next_at = (datetime.now(timezone.utc) + timedelta(
                seconds=max(1.0, float(exc.retry_after_seconds)))).isoformat(timespec="seconds")
        reason = str(exc.reason)[:300]
        logger.info("source %s deferred: %s (next attempt %s)", source_name, reason, next_at)
        db.mark_source_deferred(
            conn, source_name, _now_iso(), f"deferred: {reason}", next_at, duration_ms)
        run_id = db.record_source_run(
            conn, source_name, started_at, _now_iso(), "deferred", duration_ms, 0,
            reason, next_attempt_at=next_at)
        return RunResult("deferred", run_id, 0, next_at)
    except Exception as exc:  # noqa: BLE001 - we want to capture any failure
        duration_ms = int((time.perf_counter() - started) * 1000)
        logger.warning("source %s failed", source_name, exc_info=exc)
        # The status string is a UI feature, but raw exception text can leak
        # internals — keep it short and typed. The full traceback goes into
        # error_detail (status row and this run's row) for the Server page.
        brief = f"error: {type(exc).__name__}: {str(exc)[:120]}"
        detail = f"{type(exc).__name__}: {exc}\n\n" + traceback.format_exc()[-4000:]
        db.update_source_status(
            conn, source_name, _now_iso(), brief, 0,
            error_detail=detail, success=False, duration_ms=duration_ms)
        run_id = db.record_source_run(
            conn, source_name, started_at, _now_iso(), "error", duration_ms, 0, brief,
            error_detail=detail)
        return RunResult("error", run_id, 0)
    finally:
        with _RUNNING_LOCK:
            _RUNNING.pop(source_name, None)
