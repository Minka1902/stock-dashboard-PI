"""In-process push channel behind GET /api/stream (STC-73).

Scheduler threads `publish`; each open server-sent-events stream holds an
asyncio queue on the server's event loop and is handed events through
`call_soon_threadsafe`. An event with a user_id reaches only that user's
streams. This lives in the process, which is fine on the single uvicorn
worker the app requires (CLAUDE.md) and would have to move out with it.

A stream sends a comment every PING_SECONDS so proxies keep it open and a
dead client is noticed, and ends after STREAM_MAX_SECONDS so no connection
outlives a restart by long; the browser reconnects.
"""
import asyncio
import json
import threading
import time

from app import config

PING_SECONDS = 20
_QUEUE_SIZE = 100

_lock = threading.Lock()
_subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue, int]] = set()


def _offer(q: asyncio.Queue, item) -> None:
    if q.full():
        q.get_nowait()
    q.put_nowait(item)


def publish(kind: str, data: dict | None = None, user_id: int | None = None) -> int:
    """Send to every stream (or only `user_id`'s). Returns how many got it."""
    item = (kind, data or {})
    with _lock:
        targets = [(loop, q) for loop, q, uid in _subscribers if user_id is None or uid == user_id]
    for loop, q in targets:
        try:
            loop.call_soon_threadsafe(_offer, q, item)
        except RuntimeError:   # that loop has closed
            pass
    return len(targets)


def subscriber_count() -> int:
    with _lock:
        return len(_subscribers)


def frame(kind: str, data: dict) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def stream(user_id: int, is_disconnected, max_seconds: float | None = None):
    """The SSE body for one connection."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_SIZE)
    sub = (loop, q, user_id)
    with _lock:
        _subscribers.add(sub)
    deadline = time.monotonic() + (max_seconds or config.STREAM_MAX_SECONDS)
    try:
        yield "retry: 3000\n\n" + frame("hello", {"ping_seconds": PING_SECONDS})
        while (left := deadline - time.monotonic()) > 0:
            try:
                kind, data = await asyncio.wait_for(q.get(), timeout=min(PING_SECONDS, left))
                yield frame(kind, data)
            except asyncio.TimeoutError:
                if time.monotonic() >= deadline or await is_disconnected():
                    return
                yield ": ping\n\n"
    finally:
        with _lock:
            _subscribers.discard(sub)
