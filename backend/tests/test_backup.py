import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app import backup


def _seed(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS backup_probe (v TEXT)")
    conn.execute("INSERT INTO backup_probe VALUES ('kept')")
    conn.commit()


def test_backup_is_a_verified_readable_copy(conn, tmp_path):
    _seed(conn)
    path = backup.run(conn, tmp_path / "b", keep=3)
    assert path.name.startswith("stocks-") and path.suffix == ".db"
    copy = sqlite3.connect(path)
    try:
        assert copy.execute("SELECT v FROM backup_probe").fetchone() == ("kept",)
    finally:
        copy.close()
    assert not list((tmp_path / "b").glob("*.part"))


def test_only_the_newest_copies_are_kept(conn, tmp_path):
    _seed(conn)
    start = datetime(2026, 11, 1, 3, 47, tzinfo=UTC)
    made = [backup.run(conn, tmp_path / "b", keep=2, now=start + timedelta(days=i)) for i in range(4)]
    assert sorted((tmp_path / "b").glob("stocks-*.db")) == made[-2:]


def test_a_copy_failing_integrity_check_is_not_kept(conn, tmp_path, monkeypatch):
    _seed(conn)
    real_connect = sqlite3.connect

    class Corrupt(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.startswith("PRAGMA integrity_check"):
                return real_connect(":memory:").execute("SELECT 'row 3 missing from index'")
            return super().execute(sql, *args)

    monkeypatch.setattr(backup.sqlite3, "connect", lambda p: real_connect(p, factory=Corrupt))
    with pytest.raises(RuntimeError, match="integrity_check"):
        backup.run(conn, tmp_path / "b")
    assert list((tmp_path / "b").iterdir()) == []


def test_latest_describes_the_newest_copy(conn, tmp_path):
    target = tmp_path / "b"
    assert backup.latest(target) is None
    _seed(conn)
    backup.run(conn, target, now=datetime(2026, 11, 1, tzinfo=UTC))
    newest = backup.run(conn, target, now=datetime(2026, 11, 2, tzinfo=UTC))
    info = backup.latest(target)
    assert info["path"] == str(newest) and info["count"] == 2 and info["size_bytes"] > 0
