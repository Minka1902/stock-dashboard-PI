"""Path resolution must not depend on the cwd.

The Windows service starts in C:\\Windows\\system32. A cwd-relative DB default
would silently create a second, empty database there, and the app would come up
reporting healthy with none of your data in it — the worst kind of failure,
because nothing errors. These guard against that regressing.
"""
import importlib
import os
import sqlite3
from pathlib import Path

from app import config, db


def _reload_config(monkeypatch, **env):
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    return importlib.reload(config)


def test_db_path_default_is_absolute(monkeypatch):
    cfg = _reload_config(monkeypatch, STOCKS_DB_PATH=None)
    try:
        assert os.path.isabs(cfg.DB_PATH)
    finally:
        importlib.reload(config)


def test_db_path_default_is_backend_stocks_db(monkeypatch):
    cfg = _reload_config(monkeypatch, STOCKS_DB_PATH=None)
    try:
        expected = Path(config.__file__).resolve().parents[1] / "stocks.db"
        assert Path(cfg.DB_PATH) == expected
    finally:
        importlib.reload(config)


def test_db_path_env_override_wins(monkeypatch):
    cfg = _reload_config(monkeypatch, STOCKS_DB_PATH=r"C:\ProgramData\X\stocks.db")
    try:
        assert cfg.DB_PATH == r"C:\ProgramData\X\stocks.db"
    finally:
        importlib.reload(config)


def test_log_dir_default_is_absolute(monkeypatch):
    cfg = _reload_config(monkeypatch, STOCKS_LOG_DIR=None)
    try:
        assert cfg.LOG_DIR.is_absolute()
    finally:
        importlib.reload(config)


def test_connect_creates_missing_parent_dir(tmp_path):
    """The installer makes this directory, but connect() runs at import time,
    before logging exists — a missing dir would surface only as an opaque
    "unable to open database file" in the service's stderr log."""
    target = tmp_path / "ProgramData" / "SignalDashboard" / "db" / "stocks.db"
    assert not target.parent.exists()

    conn = db.connect(str(target))
    try:
        assert target.parent.is_dir()
        assert target.is_file()
    finally:
        conn.close()


def test_connect_still_handles_in_memory():
    conn = db.connect(":memory:")
    try:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()


def test_connect_handles_a_bare_filename(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    conn = db.connect("bare.db")
    try:
        assert (tmp_path / "bare.db").is_file()
    finally:
        conn.close()


def test_schema_survives_a_fresh_service_style_path(tmp_path):
    """End to end: the layout the service actually uses."""
    target = tmp_path / "SignalDashboard" / "db" / "stocks.db"
    conn = db.connect(str(target))
    try:
        db.init_schema(conn)
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "users" in names and "earnings" in names
    finally:
        conn.close()


def test_sqlite_would_have_created_a_stray_db_without_the_fix(tmp_path, monkeypatch):
    """Documents the failure mode: a relative path follows the cwd."""
    monkeypatch.chdir(tmp_path)
    stray = sqlite3.connect("stocks.db")
    stray.close()
    assert (tmp_path / "stocks.db").is_file()
    # ...which is exactly why config.DB_PATH must not be relative.
    assert os.path.isabs(config.DB_PATH)
