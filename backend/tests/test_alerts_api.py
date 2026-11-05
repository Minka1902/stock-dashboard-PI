"""Per-ticker alerts and the explanations attached to them."""
from app import alerts as alerts_module
from app import db
from app.models import Alert


def _alert(key, ticker, type_="boom_cross", severity="high", created="2026-08-01T10:00:00+00:00"):
    return Alert(
        dedup_key=key, created_at=created, ticker=ticker, type=type_,
        severity=severity, title=f"{ticker} {type_}", message="something happened",
    )


def test_every_alert_type_has_an_explanation():
    """An alert with no explanation is exactly the gap this closes, so adding a
    new type without documenting it should fail here rather than ship blank."""
    assert set(alerts_module.ALERT_MEANING) == set(alerts_module._SEVERITY)
    for type_, meaning in alerts_module.ALERT_MEANING.items():
        assert set(meaning) == {"what", "why", "look_at"}, type_
        assert all(v.strip() for v in meaning.values()), type_


def test_get_alerts_for_filters_by_ticker(conn):
    db.upsert_alerts(conn, [
        _alert("a|NVDA|1", "NVDA"),
        _alert("b|AAPL|1", "AAPL"),
        _alert("c|NVDA|2", "NVDA", type_="golden_cross"),
    ])
    rows = db.get_alerts_for(conn, user_id=1, ticker="NVDA")
    assert {r.dedup_key for r in rows} == {"a|NVDA|1", "c|NVDA|2"}
    assert all(r.ticker == "NVDA" for r in rows)


def test_get_alerts_for_is_ordered_by_time_not_insertion(conn):
    """The pane labels this list "newest first", so it has to sort on
    created_at. Insertion order tracks time for live detections but is not the
    same thing, and a backfill or reordered write would silently break it."""
    db.upsert_alerts(conn, [
        _alert("old|NVDA", "NVDA", created="2026-07-01T09:00:00+00:00"),
        _alert("new|NVDA", "NVDA", created="2026-08-01T09:00:00+00:00"),
        _alert("mid|NVDA", "NVDA", created="2026-07-15T09:00:00+00:00"),
    ])
    rows = db.get_alerts_for(conn, user_id=1, ticker="NVDA")
    assert [r.dedup_key for r in rows] == ["new|NVDA", "mid|NVDA", "old|NVDA"]


def test_get_alerts_for_reflects_per_user_read_state(conn):
    db.upsert_alerts(conn, [_alert("a|NVDA|1", "NVDA"), _alert("b|NVDA|2", "NVDA")])
    db.mark_alerts_read(conn, user_id=1, keys=["a|NVDA|1"], read_at="2026-08-01T11:00:00+00:00")

    for_user1 = {r.dedup_key: r.read for r in db.get_alerts_for(conn, 1, "NVDA")}
    assert for_user1 == {"a|NVDA|1": True, "b|NVDA|2": False}
    # Alerts are global but read-state is not: a second user sees both unread.
    for_user2 = {r.dedup_key: r.read for r in db.get_alerts_for(conn, 2, "NVDA")}
    assert for_user2 == {"a|NVDA|1": False, "b|NVDA|2": False}


def test_get_alerts_for_unknown_ticker_is_empty(conn):
    db.upsert_alerts(conn, [_alert("a|NVDA|1", "NVDA")])
    assert db.get_alerts_for(conn, 1, "ZZZZ") == []


def test_get_alerts_for_respects_limit(conn):
    db.upsert_alerts(conn, [_alert(f"k{i}|NVDA", "NVDA") for i in range(30)])
    assert len(db.get_alerts_for(conn, 1, "NVDA", limit=5)) == 5


def test_alerts_ticker_index_exists(conn):
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()}
    assert "idx_alerts_ticker" in names
