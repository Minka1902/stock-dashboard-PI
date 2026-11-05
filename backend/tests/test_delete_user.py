"""Account deletion: every per-user row goes, nobody else's does.

Only three of the per-user tables declare ON DELETE CASCADE, so deleting the
`users` row alone would leave orphans behind under an id SQLite is free to hand
to the next account. `db.delete_user` has to sweep all of them explicitly.
"""
from app import db, ledger
from app.models import (
    Bot,
    BotEvent,
    EquitySnapshot,
    NotifyProfile,
    OAuthIdentity,
    SuggestionHistoryEntry,
    WatchItem,
)
from tests.conftest import buy

# Tables that carry a user_id and must be empty for a deleted account.
PER_USER_TABLES = (
    "watchlist",
    "watchlists",
    "portfolio",
    "transactions",
    "cash_balances",
    "equity_snapshots",
    "bots",
    "bot_events",
    "notify_profile",
    "alert_reads",
    "suggestion_history",
    "drawings",
    "drawing_drafts",
    "fx_watch",
    "sessions",
    "recovery_codes",
    "oauth_identities",
)


def _populate(conn, user_id: int, ticker: str) -> None:
    """Write one row into each per-user table for `user_id`."""
    db.add_watch(conn, user_id, WatchItem(ticker=ticker, note="n", added_at="t"))
    db.create_watchlist(conn, user_id, "Extra", "t")
    buy(conn, user_id, ticker, 1.0, 2.0)
    ledger.record(conn, user_id, ticker="USD", side="DEPOSIT", qty=5, currency_code="USD")
    db.upsert_equity_snapshots(conn, [EquitySnapshot(
        user_id=user_id, account="manual", date="2026-01-01", base_currency="USD",
        value_base=7, cash_base=5, invested_base=2, flow_base=0, computed_at="t")])
    bot = db.create_bot(conn, Bot(user_id=user_id, name="b", strategy="trend", currency="USD",
                                  allocated=1, created_at="t"))
    db.add_bot_event(conn, BotEvent(bot_id=bot.id, user_id=user_id, at="t", kind="info", reason="r"))
    db.upsert_notify_profile(conn, user_id, NotifyProfile(
        email="a@b.c", phone="", email_enabled=True, sms_enabled=False,
        account_size=None, risk_pct=1.0, updated_at="t"))
    db.mark_alerts_read(conn, user_id, keys=[f"k-{user_id}"], read_at="t")
    db.record_suggestion_history(conn, [SuggestionHistoryEntry(
        user_id=user_id, ticker=ticker, for_date="2026-01-01", kind="buy",
        action="watch", price=1.0, created_at="t")])
    db.save_drawings(conn, user_id, ticker, [{"type": "line"}], "t")
    db.create_drawing_draft(conn, user_id, ticker, "d", "", "1d", [], "t")
    db.set_fx_watch(conn, user_id, ["USDILS=X"])
    conn.execute(
        "INSERT INTO sessions "
        "(token_hash, user_id, state, created_at, expires_at, last_seen_at) "
        "VALUES (?, ?, 'active', 't', 't', 't')",
        (f"hash-{user_id}", user_id),
    )
    db.replace_recovery_codes(conn, user_id, [f"rc-{user_id}"])
    db.create_oauth_identity(conn, OAuthIdentity(
        provider="github", provider_user_id=f"gh-{user_id}", user_id=user_id,
        email="a@b.c", created_at="t"))
    conn.commit()


def _row_counts(conn, user_id: int) -> dict[str, int]:
    return {
        t: conn.execute(
            f"SELECT COUNT(*) FROM {t} WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
        for t in PER_USER_TABLES
    }


def _make_user(conn, email: str):
    return db.create_user(conn, email, "hash", "t")


def test_delete_user_removes_every_per_user_row(conn):
    doomed = _make_user(conn, "doomed@example.com")
    keeper = _make_user(conn, "keeper@example.com")
    _populate(conn, doomed.id, "AAPL")
    _populate(conn, keeper.id, "MSFT")

    # Guard: the fixture actually wrote something everywhere, so a later
    # all-zero assertion means "deleted", not "never existed".
    before = _row_counts(conn, doomed.id)
    assert all(n > 0 for n in before.values()), before

    assert db.delete_user(conn, doomed.id) is True

    assert db.get_user(conn, doomed.id) is None
    assert _row_counts(conn, doomed.id) == dict.fromkeys(PER_USER_TABLES, 0)


def test_delete_user_leaves_other_accounts_untouched(conn):
    doomed = _make_user(conn, "doomed@example.com")
    keeper = _make_user(conn, "keeper@example.com")
    _populate(conn, doomed.id, "AAPL")
    _populate(conn, keeper.id, "MSFT")

    expected = _row_counts(conn, keeper.id)
    db.delete_user(conn, doomed.id)

    assert db.get_user(conn, keeper.id) is not None
    assert _row_counts(conn, keeper.id) == expected
    assert [w.ticker for w in db.get_watchlist(conn, keeper.id)] == ["MSFT"]


def test_delete_user_keeps_shared_market_data(conn):
    """Market tables are shared and ticker-keyed — deletion must not touch them."""
    user = _make_user(conn, "doomed@example.com")
    _populate(conn, user.id, "AAPL")
    conn.execute(
        "INSERT INTO alerts "
        "(dedup_key, created_at, ticker, type, severity, title, message) "
        "VALUES ('k1', 't', 'AAPL', 'boom', 'high', 'ti', 'm')")
    conn.commit()

    db.delete_user(conn, user.id)

    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 1


def test_delete_user_returns_false_for_unknown_id(conn):
    assert db.delete_user(conn, 4242) is False
