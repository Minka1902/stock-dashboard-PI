"""Nightly online backup of the SQLite database (the db_backup job).

SQLite's online backup API copies a consistent snapshot while the app keeps
writing. The copy is kept only after `PRAGMA integrity_check` passes on it: a
bad copy is deleted and the job raises, which the Server page shows as an
error. The newest BACKUP_KEEP copies are kept.
"""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app import config

PREFIX = "stocks-"
SUFFIX = ".db"


def _files(directory: Path) -> list[Path]:
    # The UTC timestamp in the name sorts chronologically.
    return sorted(directory.glob(f"{PREFIX}*{SUFFIX}"))


def run(conn: sqlite3.Connection, directory: Path | None = None, keep: int | None = None,
        now: datetime | None = None) -> Path:
    directory = Path(directory or config.BACKUP_DIR)
    keep = max(1, keep or config.BACKUP_KEEP)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    target = directory / f"{PREFIX}{stamp}{SUFFIX}"
    part = target.with_suffix(".part")
    part.unlink(missing_ok=True)
    dest = sqlite3.connect(part)
    try:
        conn.backup(dest)
        verdict = dest.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        dest.close()
    if verdict != "ok":
        part.unlink(missing_ok=True)
        raise RuntimeError(f"backup copy failed integrity_check: {verdict}")
    part.replace(target)
    for old in _files(directory)[:-keep]:
        old.unlink()
    return target


def latest(directory: Path | None = None) -> dict | None:
    files = _files(Path(directory or config.BACKUP_DIR))
    if not files:
        return None
    stat = files[-1].stat()
    return {
        "path": str(files[-1]),
        "size_bytes": stat.st_size,
        "at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
        "count": len(files),
    }
