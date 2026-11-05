"""Append-only audit trail (STC-52).

Each user's rows form a hash chain: a row's hash covers the previous row's
hash plus its own user, time, kind and detail, so editing or deleting any
row breaks every hash after it — `verify` finds the first break. One chain
per user means deleting an account drops its whole chain and leaves everyone
else's intact.

Recorded: every order attempt and its outcome (paper and bots), guardrail
refusals, kill-switch halts and releases, risk-setting and exit-rule changes,
bot create / pause / resume / stop, paper resets.
"""
import csv
import hashlib
import io
import json
from datetime import datetime, timezone

from app import db


def digest(prev: str, user_id: int, at: str, kind: str, detail: str) -> str:
    raw = json.dumps([prev, user_id, at, kind, detail], separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def record(conn, user_id: int, kind: str, **detail) -> None:
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    db.append_audit(conn, user_id, at, kind, json.dumps(detail, sort_keys=True, default=str), digest)


def verify(conn, user_id: int) -> dict:
    """{ok, n, broken_at (row id of the first row that doesn't match), reason}."""
    prev = ""
    rows = db.get_audit(conn, user_id, newest_first=False)
    for e in rows:
        if e.prev_hash != prev:
            return {"ok": False, "n": len(rows), "broken_at": e.id, "reason": "links to a different previous row"}
        if digest(prev, e.user_id, e.at, e.kind, e.detail) != e.hash:
            return {"ok": False, "n": len(rows), "broken_at": e.id, "reason": "its content was changed"}
        prev = e.hash
    return {"ok": True, "n": len(rows), "broken_at": None, "reason": None}


def to_csv(conn, user_id: int) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "at", "kind", "detail", "prev_hash", "hash"])
    for e in db.get_audit(conn, user_id, newest_first=False):
        w.writerow([e.id, e.at, e.kind, e.detail, e.prev_hash, e.hash])
    return out.getvalue()
