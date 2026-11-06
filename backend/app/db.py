"""All SQLite access lives here."""
import functools
from contextlib import contextmanager
import inspect
import json
import re
import html
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.models import (
    AaiiSentiment,
    Alert,
    AnalystSignal,
    AuditEntry,
    AppSettings,
    AuthSession,
    BoomScore,
    Bot,
    BotEvent,
    CashBalance,
    CompanyHolder,
    CongressFiling,
    CongressTrade,
    Filing13F,
    Holding13F,
    ContractRecord,
    EarningsEvent,
    EconEvent,
    EquitySnapshot,
    FearGreedSnapshot,
    Fundamentals,
    Holding,
    OAuthIdentity,
    Order,
    PositionRule,
    RegimeSnapshot,
    RiskSettings,
    RuleSignal,
    SavedRule,
    ScanHit,
    ScannerPrefs,
    SignalMute,
    SignalPerformance,
    InsiderTrade,
    MarginDebtPoint,
    NewsArticle,
    NotifyProfile,
    OHLCBar,
    OHLCSeries,
    PutCallPoint,
    StockAnalysis,
    Seasonality,
    ShortInterest,
    SuggestionHistoryEntry,
    SocialSentiment,
    SourceRun,
    SourceSchedule,
    SourceStatus,
    JobRun,
    SuggestionLogEntry,
    TechnicalSignal,
    Transaction,
    User,
    VixPoint,
    WatchItem,
    WatchlistMeta,
    XPost,
    YieldPoint,
    UniverseMember,
)


class _LockingConnection(sqlite3.Connection):
    """Connection subclass that carries a re-entrant lock.

    A raw ``sqlite3.Connection`` has no ``__dict__`` so we can't attach the
    lock to it; a subclass instance can. The lock lives on the connection
    object itself, so every db access serializes on the *same* lock no matter
    how this module is imported. See the ``_synchronized`` wrapper below.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._app_lock = threading.RLock()


def connect(db_path: str) -> sqlite3.Connection:
    # SQLite creates the file but not its directory. The Pi service points
    # this at /var/lib/stocks-dashboard/db/, and this runs at import time
    # (main.py connects before logging exists), so a missing directory would
    # surface only as "unable to open database file" in the service's stderr
    # log. ":memory:" and bare filenames have no parent worth creating.
    parent = Path(db_path).parent
    if str(parent) not in ("", ".") and not db_path.startswith(":"):
        parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, factory=_LockingConnection, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL lets readers proceed while a write is in flight; busy_timeout keeps
    # a briefly-locked DB from surfacing as an exception under load.
    conn.execute("PRAGMA journal_mode=WAL")
    # NORMAL is crash-safe in WAL mode (a power cut can lose the last commits,
    # never corrupt the file) and syncs far less than FULL: less SD-card wear
    # and less write latency on the Pi.
    conn.execute("PRAGMA synchronous=NORMAL")
    # 15s, not 5s: the request and refresh connections share the file's write
    # lock, and a long ingestion write during a poll-heavy page was enough to
    # surface "database is locked" to the UI.
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _try_add_column(conn: sqlite3.Connection, table: str, col: str, col_def: str) -> None:
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_def}")
    except Exception:
        pass  # column already exists


def _migrate_users_onboarded(conn: sqlite3.Connection) -> None:
    """Add users.onboarded once, backfilling every pre-existing account to 1.

    Existing accounts are returning users and must never be re-shown the guided
    app tour; only brand-new registrations start at 0 (and get the tour). Runs
    exactly once — subsequent startups see the column and skip the backfill."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "onboarded" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN onboarded INTEGER NOT NULL DEFAULT 0")
        conn.execute("UPDATE users SET onboarded = 1")


def _migrate_users_approved(conn: sqlite3.Connection) -> None:
    """Add users.approved_at once, backfilling every pre-existing account as
    approved at its creation time: the approval queue applies to new sign-ups,
    never to the people already using the instance. NULL = pending."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "approved_at" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN approved_at TEXT")
        conn.execute("UPDATE users SET approved_at = created_at")


def _migrate_watchlists(conn: sqlite3.Connection) -> None:
    """Move the flat per-user watchlist to named lists (Task 13). Runs once:
    if the watchlist table predates `watchlist_id`, rebuild it and fold each
    user's existing rows into an auto-created default "My Watchlist"."""
    from datetime import datetime, timezone

    cols = {r[1] for r in conn.execute("PRAGMA table_info(watchlist)").fetchall()}
    if not cols or "watchlist_id" in cols:
        return  # fresh DB (already new shape) or already migrated
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        """CREATE TABLE watchlist_v2 (
            user_id      INTEGER NOT NULL DEFAULT 0,
            watchlist_id INTEGER NOT NULL,
            ticker       TEXT NOT NULL,
            note         TEXT NOT NULL,
            added_at     TEXT NOT NULL,
            PRIMARY KEY (watchlist_id, ticker)
        )"""
    )
    users = [r[0] for r in conn.execute("SELECT DISTINCT user_id FROM watchlist").fetchall()]
    for uid in users:
        cur = conn.execute(
            "INSERT INTO watchlists (user_id, name, created_at, sort_order) VALUES (?, ?, ?, 0)",
            (uid, "My Watchlist", now),
        )
        conn.execute(
            "INSERT INTO watchlist_v2 (user_id, watchlist_id, ticker, note, added_at) "
            "SELECT user_id, ?, ticker, note, added_at FROM watchlist WHERE user_id = ?",
            (cur.lastrowid, uid),
        )
    conn.execute("DROP TABLE watchlist")
    conn.execute("ALTER TABLE watchlist_v2 RENAME TO watchlist")
    conn.commit()


def _migrate_per_user_tables(conn: sqlite3.Connection) -> None:
    """One-time rebuild of pre-auth single-user tables to per-user shape.

    SQLite can't alter a primary key, so tables created before multi-user
    support are rebuilt with rows parked under user_id=0 ("unclaimed legacy").
    The first account to register claims them (see claim_legacy_rows).
    Idempotent: skipped once the user_id column exists.
    """
    def has_col(table: str, col: str) -> bool:
        return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})"))

    if not has_col("watchlist", "user_id"):
        conn.executescript(
            """
            CREATE TABLE watchlist_v2 (
                user_id  INTEGER NOT NULL DEFAULT 0,
                ticker   TEXT NOT NULL,
                note     TEXT NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY (user_id, ticker)
            );
            INSERT INTO watchlist_v2 (user_id, ticker, note, added_at)
                SELECT 0, ticker, note, added_at FROM watchlist;
            DROP TABLE watchlist;
            ALTER TABLE watchlist_v2 RENAME TO watchlist;
            """
        )

    if not has_col("portfolio", "user_id"):
        conn.executescript(
            """
            CREATE TABLE portfolio_v2 (
                user_id  INTEGER NOT NULL DEFAULT 0,
                ticker   TEXT NOT NULL,
                shares   REAL NOT NULL,
                avg_cost REAL NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY (user_id, ticker)
            );
            INSERT INTO portfolio_v2 (user_id, ticker, shares, avg_cost, added_at)
                SELECT 0, ticker, shares, avg_cost, added_at FROM portfolio;
            DROP TABLE portfolio;
            ALTER TABLE portfolio_v2 RENAME TO portfolio;
            """
        )

    if not has_col("notify_profile", "user_id"):
        conn.executescript(
            """
            CREATE TABLE notify_profile_v2 (
                user_id       INTEGER PRIMARY KEY,
                email         TEXT,
                phone         TEXT,
                email_enabled INTEGER NOT NULL DEFAULT 0,
                sms_enabled   INTEGER NOT NULL DEFAULT 0,
                account_size  REAL,
                risk_pct      REAL NOT NULL DEFAULT 1.0,
                updated_at    TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO notify_profile_v2
                (user_id, email, phone, email_enabled, sms_enabled,
                 account_size, risk_pct, updated_at)
                SELECT 0, email, phone, email_enabled, sms_enabled,
                       account_size, COALESCE(risk_pct, 1.0), updated_at
                FROM notify_profile;
            DROP TABLE notify_profile;
            ALTER TABLE notify_profile_v2 RENAME TO notify_profile;
            """
        )

    conn.commit()


def _rebuild_per_run_component_history(conn: sqlite3.Connection) -> None:
    """boom_component_history briefly stored one row per scoring run (no
    `day`). Per-signal outcomes are measured on daily closes, so it is now one
    row per (ticker, day, component); the old shape is dropped and rebuilt."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(boom_component_history)")}
    if cols and "day" not in cols:
        conn.execute("DROP TABLE boom_component_history")


def init_schema(conn: sqlite3.Connection) -> None:
    _rebuild_per_run_component_history(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS contracts (
            external_id     TEXT PRIMARY KEY,
            award_id        TEXT NOT NULL,
            recipient_name  TEXT NOT NULL,
            amount          REAL NOT NULL,
            awarding_agency TEXT NOT NULL,
            start_date      TEXT NOT NULL,
            source          TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS news (
            url           TEXT PRIMARY KEY,
            title         TEXT NOT NULL,
            domain        TEXT NOT NULL,
            seendate      TEXT NOT NULL,
            sourcecountry TEXT NOT NULL,
            image         TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS insider_trades (
            accession        TEXT PRIMARY KEY,
            ticker           TEXT NOT NULL,
            company          TEXT NOT NULL,
            owner            TEXT NOT NULL,
            role             TEXT NOT NULL,
            transaction_date TEXT NOT NULL,
            transaction_type TEXT NOT NULL,
            shares           REAL NOT NULL,
            value            REAL NOT NULL,
            filing_url       TEXT NOT NULL,
            filed_at         TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS watchlists (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            name       TEXT NOT NULL,
            created_at TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS watchlist (
            user_id      INTEGER NOT NULL DEFAULT 0,
            watchlist_id INTEGER NOT NULL,
            ticker       TEXT NOT NULL,
            note         TEXT NOT NULL,
            added_at     TEXT NOT NULL,
            PRIMARY KEY (watchlist_id, ticker)
        );
        CREATE TABLE IF NOT EXISTS source_status (
            source            TEXT PRIMARY KEY,
            last_refreshed_at TEXT,
            status            TEXT NOT NULL,
            record_count      INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS yield_curve (
            date   TEXT PRIMARY KEY,
            yr2    REAL,
            yr10   REAL,
            yr30   REAL,
            spread REAL
        );
        CREATE TABLE IF NOT EXISTS econ_events (
            event_id          TEXT PRIMARY KEY,
            date              TEXT NOT NULL,
            time              TEXT NOT NULL,
            country           TEXT NOT NULL,
            event             TEXT NOT NULL,
            importance        TEXT NOT NULL,
            importance_source TEXT NOT NULL,
            actual            TEXT,
            forecast          TEXT,
            previous          TEXT,
            source            TEXT NOT NULL,
            fetched_at        TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS technical_signals (
            ticker       TEXT PRIMARY KEY,
            fetched_at   TEXT NOT NULL,
            price        REAL,
            change_pct   REAL,
            ma50         REAL,
            ma200        REAL,
            golden_cross INTEGER,
            rsi14        REAL,
            high_52w     REAL,
            low_52w      REAL,
            prices_json  TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS fear_greed (
            captured_at TEXT PRIMARY KEY,
            score       REAL NOT NULL,
            rating      TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS vix_daily (
            date  TEXT PRIMARY KEY,
            close REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS aaii_sentiment (
            week_ending TEXT PRIMARY KEY,
            bullish     REAL NOT NULL,
            neutral     REAL NOT NULL,
            bearish     REAL NOT NULL,
            fetched_at  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS put_call (
            date  TEXT PRIMARY KEY,
            ratio REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS margin_debt (
            month          TEXT PRIMARY KEY,
            debit_balances REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS congress_trades (
            trade_hash        TEXT PRIMARY KEY,
            representative    TEXT NOT NULL,
            party             TEXT NOT NULL,
            state             TEXT NOT NULL,
            ticker            TEXT NOT NULL,
            asset_description TEXT NOT NULL,
            transaction_date  TEXT NOT NULL,
            transaction_type  TEXT NOT NULL,
            amount_range      TEXT NOT NULL,
            filed_at          TEXT NOT NULL,
            chamber           TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS congress_filings (
            doc_id     TEXT PRIMARY KEY,
            chamber    TEXT NOT NULL,
            member     TEXT NOT NULL,
            district   TEXT NOT NULL,
            filed_at   TEXT NOT NULL,
            url        TEXT NOT NULL,
            status     TEXT NOT NULL,
            rows       INTEGER NOT NULL DEFAULT 0,
            checked_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS filings_13f (
            cik          TEXT PRIMARY KEY,
            filer        TEXT NOT NULL,
            person       TEXT NOT NULL,
            period       TEXT NOT NULL,
            filed        TEXT NOT NULL,
            accession    TEXT NOT NULL,
            prior_period TEXT,
            positions    INTEGER NOT NULL,
            total_value  REAL NOT NULL,
            checked_at   TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS holdings_13f (
            cik           TEXT NOT NULL,
            cusip         TEXT NOT NULL,
            put_call      TEXT NOT NULL DEFAULT '',
            issuer        TEXT NOT NULL,
            title         TEXT NOT NULL,
            ticker        TEXT,
            ticker_source TEXT NOT NULL,
            value         REAL NOT NULL,
            shares        REAL NOT NULL,
            prev_shares   REAL NOT NULL,
            prev_value    REAL NOT NULL,
            pct           REAL NOT NULL,
            change_pct    REAL,
            status        TEXT NOT NULL,
            PRIMARY KEY (cik, cusip, put_call)
        );
        CREATE INDEX IF NOT EXISTS idx_holdings_13f_ticker ON holdings_13f(ticker);
        CREATE TABLE IF NOT EXISTS short_interest (
            ticker             TEXT PRIMARY KEY,
            fetched_at         TEXT NOT NULL,
            shares_short       INTEGER,
            short_pct_float    REAL,
            days_to_cover      REAL,
            prior_month_shares INTEGER,
            squeeze_flag       INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS social_sentiment (
            ticker        TEXT PRIMARY KEY,
            fetched_at    TEXT NOT NULL,
            mentions      INTEGER,
            upvotes       INTEGER,
            rank          INTEGER,
            rank_24h_ago  INTEGER,
            rank_change   INTEGER
        );
        CREATE TABLE IF NOT EXISTS analyst_signals (
            ticker            TEXT PRIMARY KEY,
            fetched_at        TEXT NOT NULL,
            next_earnings     TEXT,
            rec_strong_buy    INTEGER,
            rec_buy           INTEGER,
            rec_hold          INTEGER,
            rec_sell          INTEGER,
            recent_upgrades   INTEGER NOT NULL DEFAULT 0,
            recent_downgrades INTEGER NOT NULL DEFAULT 0,
            latest_action     TEXT,
            latest_firm       TEXT,
            latest_to_grade   TEXT
        );
        CREATE TABLE IF NOT EXISTS boom_scores (
            ticker                    TEXT PRIMARY KEY,
            computed_at               TEXT NOT NULL,
            score                     INTEGER NOT NULL,
            components                TEXT NOT NULL,
            golden_cross              INTEGER NOT NULL DEFAULT 0,
            rsi_recovery              INTEGER NOT NULL DEFAULT 0,
            insider_cluster_buy       INTEGER NOT NULL DEFAULT 0,
            congress_buy              INTEGER NOT NULL DEFAULT 0,
            short_squeeze             INTEGER NOT NULL DEFAULT 0,
            wsb_rising                INTEGER NOT NULL DEFAULT 0,
            analyst_upgrade           INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS fundamentals (
            ticker         TEXT PRIMARY KEY,
            fetched_at     TEXT NOT NULL,
            sector         TEXT,
            industry       TEXT,
            pe_ratio       REAL,
            forward_pe     REAL,
            peg_ratio      REAL,
            pb_ratio       REAL,
            revenue_growth REAL,
            profit_margin  REAL,
            market_cap     REAL
        );
        -- One row per suggested ticker per trading day, for watchlisted and
        -- held names only. Deliberately lean: outcomes ("+7d it went up 12%")
        -- are derived at read time from stored bars, never stored, so they
        -- can't go stale.
        CREATE TABLE IF NOT EXISTS suggestion_history (
            user_id    INTEGER NOT NULL,
            ticker     TEXT NOT NULL,
            for_date   TEXT NOT NULL,
            kind       TEXT NOT NULL,   -- 'holding' | 'watchlist'
            action     TEXT NOT NULL,
            price      REAL,            -- reference price when suggested
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, ticker, for_date)
        );
        -- User-drawn chart annotations, one row per (user, ticker). The shapes
        -- themselves are opaque JSON — the server stores and returns them, the
        -- client owns their meaning.
        CREATE TABLE IF NOT EXISTS drawings (
            user_id    INTEGER NOT NULL,
            ticker     TEXT NOT NULL,
            shapes_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, ticker)
        );
        -- Named snapshots of a ticker's drawings ("drafts"), many per
        -- (user, ticker). Like `drawings`, the shapes are opaque client JSON.
        CREATE TABLE IF NOT EXISTS drawing_drafts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL,
            ticker      TEXT NOT NULL,
            title       TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            timeframe   TEXT NOT NULL DEFAULT '',
            shapes_json TEXT NOT NULL DEFAULT '[]',
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_drawing_drafts_owner
            ON drawing_drafts (user_id, ticker);
        CREATE TABLE IF NOT EXISTS company_holders (
            ticker      TEXT NOT NULL,
            kind        TEXT NOT NULL DEFAULT 'institution',
            holder      TEXT NOT NULL,
            pct_held    REAL,
            shares      REAL,
            value       REAL,
            reported_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (ticker, kind, holder)
        );
        CREATE TABLE IF NOT EXISTS boom_score_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker      TEXT NOT NULL,
            score       INTEGER NOT NULL,
            computed_at TEXT NOT NULL
        );
        -- Which components fired in each scoring run, at what weight (STC-20):
        -- what per-signal performance and auto-mute are measured from.
        -- One row per (ticker, day, component): the score is recomputed every
        -- few minutes, and per-signal outcomes are measured on daily closes.
        CREATE TABLE IF NOT EXISTS boom_component_history (
            ticker      TEXT NOT NULL,
            day         TEXT NOT NULL,
            component   TEXT NOT NULL,
            weight      REAL NOT NULL,
            computed_at TEXT NOT NULL,
            PRIMARY KEY (ticker, day, component)
        );
        CREATE INDEX IF NOT EXISTS idx_boom_components_day ON boom_component_history(day);
        CREATE TABLE IF NOT EXISTS regime_history (
            date        TEXT PRIMARY KEY,
            label       TEXT NOT NULL,
            score       INTEGER NOT NULL,
            inputs      TEXT NOT NULL,
            computed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS signal_performance (
            component    TEXT NOT NULL,
            horizon_days INTEGER NOT NULL,
            window_days  INTEGER NOT NULL,
            n            INTEGER NOT NULL,
            hits         INTEGER NOT NULL,
            hit_rate     REAL,
            mean_net     REAL,
            computed_at  TEXT NOT NULL,
            PRIMARY KEY (component, horizon_days)
        );
        CREATE TABLE IF NOT EXISTS signal_mutes (
            component  TEXT PRIMARY KEY,
            muted      INTEGER NOT NULL,
            since      TEXT,
            hit_rate   REAL,
            n          INTEGER NOT NULL,
            reason     TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS signal_mute_overrides (
            user_id   INTEGER NOT NULL,
            component TEXT NOT NULL,
            mode      TEXT NOT NULL CHECK (mode IN ('on', 'off')),
            PRIMARY KEY (user_id, component)
        );
        CREATE TABLE IF NOT EXISTS seasonality (
            ticker        TEXT PRIMARY KEY,
            computed_at   TEXT NOT NULL,
            as_of         TEXT NOT NULL,
            history_years INTEGER NOT NULL DEFAULT 0,
            windows_json  TEXT NOT NULL DEFAULT '[]',
            anchors_json  TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS x_posts (
            account    TEXT NOT NULL,
            post_id    TEXT NOT NULL,
            text       TEXT NOT NULL,
            url        TEXT NOT NULL DEFAULT '',
            posted_at  TEXT NOT NULL DEFAULT '',
            tickers    TEXT NOT NULL DEFAULT '',
            fetched_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (account, post_id)
        );
        -- Materialized positions, one row per (user, account, ticker). The
        -- transactions ledger below is the source of truth; every write goes
        -- through record_transaction, which updates both in one transaction.
        CREATE TABLE IF NOT EXISTS portfolio (
            user_id  INTEGER NOT NULL DEFAULT 0,
            account  TEXT NOT NULL DEFAULT 'manual',
            ticker   TEXT NOT NULL,
            shares   REAL NOT NULL,
            avg_cost REAL NOT NULL,
            added_at TEXT NOT NULL,
            category TEXT,
            currency TEXT NOT NULL DEFAULT 'USD',
            PRIMARY KEY (user_id, account, ticker)
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            account      TEXT NOT NULL DEFAULT 'manual',
            ticker       TEXT NOT NULL,
            side         TEXT NOT NULL,
            qty          REAL NOT NULL,
            price        REAL NOT NULL,
            fees         REAL NOT NULL DEFAULT 0,
            currency     TEXT NOT NULL,
            executed_at  TEXT NOT NULL,
            source       TEXT NOT NULL,
            order_id     INTEGER,
            cash_delta   REAL NOT NULL DEFAULT 0,
            realized_pnl REAL,
            flow         REAL NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_tx_user
            ON transactions(user_id, account, ticker, executed_at);
        CREATE TABLE IF NOT EXISTS cash_balances (
            user_id  INTEGER NOT NULL,
            account  TEXT NOT NULL DEFAULT 'manual',
            currency TEXT NOT NULL,
            balance  REAL NOT NULL,
            PRIMARY KEY (user_id, account, currency)
        );
        CREATE TABLE IF NOT EXISTS bots (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            name         TEXT NOT NULL,
            strategy     TEXT NOT NULL,
            params_json  TEXT NOT NULL DEFAULT '{}',
            tickers_json TEXT NOT NULL DEFAULT '[]',
            watchlist_id INTEGER,
            currency     TEXT NOT NULL,
            allocated    REAL NOT NULL,
            status       TEXT NOT NULL DEFAULT 'active',
            state_json   TEXT NOT NULL DEFAULT '{}',
            returned     REAL,
            created_at   TEXT NOT NULL,
            stopped_at   TEXT
        );
        CREATE TABLE IF NOT EXISTS bot_events (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id  INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            at      TEXT NOT NULL,
            kind    TEXT NOT NULL,
            ticker  TEXT,
            qty     REAL,
            price   REAL,
            reason  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_bot_events_bot ON bot_events(bot_id, id DESC);
        CREATE TABLE IF NOT EXISTS equity_snapshots (
            user_id       INTEGER NOT NULL,
            account       TEXT NOT NULL,
            date          TEXT NOT NULL,
            base_currency TEXT NOT NULL,
            value_base    REAL NOT NULL,
            cash_base     REAL NOT NULL,
            invested_base REAL NOT NULL,
            flow_base     REAL NOT NULL DEFAULT 0,
            computed_at   TEXT NOT NULL,
            PRIMARY KEY (user_id, account, date)
        );
        -- Exit rules per holding (STC-9). trail_high only ratchets up;
        -- fired_json lists the kinds already alerted since updated_at.
        CREATE TABLE IF NOT EXISTS position_rules (
            user_id      INTEGER NOT NULL,
            account      TEXT NOT NULL DEFAULT 'manual',
            ticker       TEXT NOT NULL,
            stop_price   REAL,
            stop_pct     REAL,
            take_profit  REAL,
            trailing_pct REAL,
            trail_high   REAL,
            fired_json   TEXT NOT NULL DEFAULT '[]',
            active       INTEGER NOT NULL DEFAULT 1,
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            PRIMARY KEY (user_id, account, ticker),
            CHECK (stop_price IS NULL OR stop_pct IS NULL)
        );
        -- Orders (STC-41): paper tickets rest here until a quote crosses them;
        -- bot fills are written already filled, in the same transaction.
        CREATE TABLE IF NOT EXISTS orders (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            account         TEXT NOT NULL,
            ticker          TEXT NOT NULL,
            side            TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
            qty             REAL NOT NULL,
            type            TEXT NOT NULL CHECK (type IN ('market', 'limit', 'stop')),
            limit_price     REAL,
            stop_price      REAL,
            tif             TEXT NOT NULL DEFAULT 'day',
            status          TEXT NOT NULL,
            broker_order_id TEXT,
            currency        TEXT NOT NULL,
            created_at      TEXT NOT NULL,
            submitted_at    TEXT,
            filled_at       TEXT,
            fill_price      REAL,
            fees            REAL NOT NULL DEFAULT 0,
            suggestion_id   INTEGER,
            reference_price REAL,
            slippage_bps    REAL,
            source          TEXT NOT NULL DEFAULT 'ticket',
            reason          TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_orders_open ON orders(status) WHERE status = 'submitted';
        -- Saved trading rules (STC-67) and the distinct variants tested per
        -- rule (STC-65), plus what each active rule fired on.
        CREATE TABLE IF NOT EXISTS saved_rules (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            name            TEXT NOT NULL,
            spec            TEXT NOT NULL,
            model_version   TEXT NOT NULL,
            active          INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_saved_rules_name ON saved_rules(user_id, name);
        CREATE TABLE IF NOT EXISTS rule_variants (
            user_id   INTEGER NOT NULL,
            rule_key  TEXT NOT NULL,
            spec_hash TEXT NOT NULL,
            tested_at TEXT NOT NULL,
            PRIMARY KEY (user_id, rule_key, spec_hash)
        );
        CREATE TABLE IF NOT EXISTS rule_signals (
            user_id       INTEGER NOT NULL,
            rule_id       INTEGER NOT NULL,
            ticker        TEXT NOT NULL,
            day           TEXT NOT NULL,
            kind          TEXT NOT NULL,
            model_version TEXT NOT NULL,
            computed_at   TEXT NOT NULL,
            PRIMARY KEY (user_id, rule_id, ticker, day, kind)
        );
        -- Market-wide scanner (STC-69): fetched index constituents, today's
        -- scan matches (shared market data), and per-user alert preferences
        -- and the throttle's log (STC-75).
        CREATE TABLE IF NOT EXISTS universe_members (
            universe     TEXT NOT NULL,
            ticker       TEXT NOT NULL,
            name         TEXT NOT NULL DEFAULT '',
            refreshed_at TEXT NOT NULL,
            PRIMARY KEY (universe, ticker)
        );
        CREATE TABLE IF NOT EXISTS scan_hits (
            scan          TEXT NOT NULL,
            ticker        TEXT NOT NULL,
            day           TEXT NOT NULL,
            price         REAL NOT NULL,
            change_pct    REAL,
            metric        REAL,
            first_seen_at TEXT NOT NULL,
            last_seen_at  TEXT NOT NULL,
            PRIMARY KEY (scan, ticker, day)
        );
        CREATE INDEX IF NOT EXISTS idx_scan_hits_day ON scan_hits(day, last_seen_at);
        CREATE TABLE IF NOT EXISTS scanner_prefs (
            user_id        INTEGER PRIMARY KEY,
            alerts_enabled INTEGER NOT NULL DEFAULT 0,
            per_hour       INTEGER NOT NULL DEFAULT 3,
            updated_at     TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS scanner_alert_log (
            user_id     INTEGER NOT NULL,
            ticker      TEXT NOT NULL,
            day         TEXT NOT NULL,
            scan        TEXT NOT NULL,
            state       TEXT NOT NULL,      -- sent | pending | digested
            logged_at   TEXT NOT NULL,
            digested_at TEXT,
            PRIMARY KEY (user_id, ticker, day)
        );
        -- Rebalance targets (STC-85): a % of the account per ticker.
        CREATE TABLE IF NOT EXISTS target_weights (
            user_id INTEGER NOT NULL,
            account TEXT NOT NULL,
            ticker  TEXT NOT NULL,
            weight  REAL NOT NULL,
            PRIMARY KEY (user_id, account, ticker)
        );
        -- Telegram (STC-79): one linked private chat per user, the pending
        -- one-time link code (hashed), and the getUpdates offset.
        CREATE TABLE IF NOT EXISTS telegram_links (
            user_id   INTEGER PRIMARY KEY,
            chat_id   INTEGER NOT NULL UNIQUE,
            username  TEXT NOT NULL DEFAULT '',
            enabled   INTEGER NOT NULL DEFAULT 1,
            linked_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS telegram_codes (
            user_id    INTEGER PRIMARY KEY,
            code_hash  TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS telegram_state (
            id            INTEGER PRIMARY KEY CHECK (id = 1),
            update_offset INTEGER NOT NULL DEFAULT 0
        );
        -- Append-only audit trail (STC-52), one hash chain per user so deleting
        -- an account drops its whole chain without breaking anyone else's.
        CREATE TABLE IF NOT EXISTS audit_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER NOT NULL,
            at        TEXT NOT NULL,
            kind      TEXT NOT NULL,
            detail    TEXT NOT NULL,
            prev_hash TEXT NOT NULL,
            hash      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id, id);
        CREATE TABLE IF NOT EXISTS risk_settings (
            user_id                  INTEGER PRIMARY KEY,
            max_drawdown_pct         REAL,
            drawdown_tripped_at      TEXT,
            drawdown_pct             REAL,
            drawdown_acknowledged_at TEXT,
            updated_at               TEXT NOT NULL DEFAULT ''
        );
        -- Per-user FX pairs shown in the ticker carousel ("USDILS=X", ...).
        -- Seeded lazily from config.FX_PAIRS on first read (get_fx_watch).
        CREATE TABLE IF NOT EXISTS fx_watch (
            user_id  INTEGER NOT NULL,
            pair     TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, pair)
        );
        CREATE TABLE IF NOT EXISTS notify_profile (
            user_id       INTEGER PRIMARY KEY,
            email         TEXT,
            phone         TEXT,
            email_enabled INTEGER NOT NULL DEFAULT 0,
            sms_enabled   INTEGER NOT NULL DEFAULT 0,
            account_size  REAL,
            risk_pct      REAL NOT NULL DEFAULT 1.0,
            updated_at    TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS app_settings (
            id                     INTEGER PRIMARY KEY CHECK (id = 1),
            analysis_time          TEXT NOT NULL DEFAULT '15:30',
            analysis_tz            TEXT NOT NULL DEFAULT 'Asia/Jerusalem',
            quotes_refresh_seconds INTEGER NOT NULL DEFAULT 30,
            updated_at             TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS suggestion_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            for_date   TEXT NOT NULL,
            channel    TEXT NOT NULL,
            status     TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            dedup_key  TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            ticker     TEXT NOT NULL,
            type       TEXT NOT NULL,
            severity   TEXT NOT NULL,
            title      TEXT NOT NULL,
            message    TEXT NOT NULL,
            read       INTEGER NOT NULL DEFAULT 0,
            pushed     INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS alert_state (
            ticker              TEXT PRIMARY KEY,
            score               INTEGER,
            golden_cross        INTEGER,
            insider_cluster_buy INTEGER,
            updated_at          TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ohlc_series (
            ticker     TEXT NOT NULL,
            interval   TEXT NOT NULL,
            bars_json  TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (ticker, interval)
        );
        CREATE TABLE IF NOT EXISTS stock_analysis (
            ticker       TEXT PRIMARY KEY,
            computed_at  TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            totp_secret   TEXT,
            totp_enabled  INTEGER NOT NULL DEFAULT 0,
            is_admin      INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL,
            onboarded     INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash   TEXT PRIMARY KEY,
            user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            state        TEXT NOT NULL,
            created_at   TEXT NOT NULL,
            expires_at   TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS recovery_codes (
            user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            code_hash TEXT NOT NULL,
            used_at   TEXT,
            PRIMARY KEY (user_id, code_hash)
        );
        CREATE TABLE IF NOT EXISTS alert_reads (
            user_id   INTEGER NOT NULL,
            dedup_key TEXT NOT NULL,
            read_at   TEXT NOT NULL,
            PRIMARY KEY (user_id, dedup_key)
        );
        -- Per-ticker alert lookup for the analysis page.
        CREATE INDEX IF NOT EXISTS idx_alerts_ticker ON alerts(ticker, id DESC);
        CREATE TABLE IF NOT EXISTS earnings_events (
            ticker           TEXT NOT NULL,
            event_date       TEXT NOT NULL,   -- YYYY-MM-DD
            is_estimate      INTEGER NOT NULL DEFAULT 1,
            timing           TEXT NOT NULL DEFAULT '',
            eps_estimate     REAL,
            eps_actual       REAL,
            surprise_pct     REAL,
            revenue_estimate REAL,
            quarter          TEXT NOT NULL DEFAULT '',
            source           TEXT NOT NULL DEFAULT 'yahoo',
            fetched_at       TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (ticker, event_date)
        );
        CREATE INDEX IF NOT EXISTS idx_earnings_date ON earnings_events(event_date);
        CREATE TABLE IF NOT EXISTS oauth_identities (
            provider         TEXT NOT NULL,
            provider_user_id TEXT NOT NULL,
            user_id          INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            email            TEXT NOT NULL DEFAULT '',
            created_at       TEXT NOT NULL,
            PRIMARY KEY (provider, provider_user_id)
        );
        CREATE TABLE IF NOT EXISTS source_runs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            source       TEXT NOT NULL,
            started_at   TEXT NOT NULL,
            finished_at  TEXT NOT NULL,
            outcome      TEXT NOT NULL,   -- ok | error | deferred (legacy: skipped)
            duration_ms  INTEGER NOT NULL,
            record_count INTEGER NOT NULL DEFAULT 0,
            detail       TEXT
        );
        CREATE TABLE IF NOT EXISTS job_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id      TEXT NOT NULL,
            event       TEXT NOT NULL,    -- executed | error | coalesced (legacy: missed | max_instances)
            at          TEXT NOT NULL,
            duration_ms INTEGER,
            detail      TEXT
        );
        CREATE TABLE IF NOT EXISTS earnings (
            ticker           TEXT NOT NULL,
            event_date       TEXT NOT NULL,   -- YYYY-MM-DD
            is_estimate      INTEGER NOT NULL DEFAULT 1,
            timing           TEXT NOT NULL DEFAULT '',  -- bmo | amc | intraday | ''
            eps_estimate     REAL,
            eps_actual       REAL,            -- NULL until the company reports
            surprise_pct     REAL,
            revenue_estimate REAL,
            quarter          TEXT NOT NULL DEFAULT '',
            source           TEXT NOT NULL DEFAULT 'yahoo',
            fetched_at       TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (ticker, event_date)
        );
        -- One row per source (plus 'derived' for boom_score -> alerts): when it
        -- runs. Seeded from the SOURCES registry cadences; edited on the
        -- Server page. times/days are comma lists ("06:00,18:00", "mon,fri").
        CREATE TABLE IF NOT EXISTS source_schedules (
            source           TEXT PRIMARY KEY,
            mode             TEXT NOT NULL DEFAULT 'interval',  -- interval | times
            interval_seconds INTEGER,
            times            TEXT NOT NULL DEFAULT '',
            days             TEXT NOT NULL DEFAULT 'mon,tue,wed,thu,fri,sat,sun',
            tz               TEXT NOT NULL DEFAULT 'UTC',
            enabled          INTEGER NOT NULL DEFAULT 1,
            retry_seconds    INTEGER,
            updated_at       TEXT
        );
        -- The calendar is read by date range far more often than by ticker.
        CREATE INDEX IF NOT EXISTS idx_earnings_date ON earnings(event_date);
        CREATE INDEX IF NOT EXISTS idx_source_runs_src_id ON source_runs(source, id DESC);
        CREATE INDEX IF NOT EXISTS idx_job_runs_id        ON job_runs(id DESC);
        CREATE INDEX IF NOT EXISTS idx_boom_hist_ticker_time
            ON boom_score_history(ticker, computed_at);
        """
    )
    conn.commit()

    # Older DBs may predate these columns; the per-user rebuild below copies them.
    _try_add_column(conn, "notify_profile", "account_size", "REAL")
    _try_add_column(conn, "notify_profile", "risk_pct", "REAL NOT NULL DEFAULT 1.0")
    _migrate_per_user_tables(conn)
    _migrate_users_onboarded(conn)
    _migrate_users_approved(conn)
    _migrate_watchlists(conn)
    _try_add_column(conn, "news", "ticker", "TEXT NOT NULL DEFAULT ''")

    # Company profile fields, parsed from the same Yahoo quoteSummary call that
    # already backed sector/industry (Task 11).
    for col, col_def in [
        ("name",            "TEXT"),
        ("website",         "TEXT"),
        ("country",         "TEXT"),
        ("city",            "TEXT"),
        ("employees",       "INTEGER"),
        ("summary",         "TEXT"),
        ("officers_json",   "TEXT NOT NULL DEFAULT ''"),
        ("insider_pct",     "REAL"),
        ("institution_pct", "REAL"),
        ("financial_currency", "TEXT"),
    ]:
        _try_add_column(conn, "fundamentals", col, col_def)

    # Migrate existing tables: add new columns (safe on pre-existing databases).
    for col, col_def in [
        ("macd",           "REAL"),
        ("macd_signal",    "REAL"),
        ("macd_crossover", "INTEGER"),
        ("rel_volume",     "REAL"),
        ("volume_json",    "TEXT NOT NULL DEFAULT '[]'"),
    ]:
        _try_add_column(conn, "technical_signals", col, col_def)

    for col, col_def in [
        ("near_52w_high",             "INTEGER NOT NULL DEFAULT 0"),
        ("macd_crossover",            "INTEGER NOT NULL DEFAULT 0"),
        ("volume_confirmed",          "INTEGER NOT NULL DEFAULT 0"),
        ("fear_greed_contrarian",     "INTEGER NOT NULL DEFAULT 0"),
        ("yield_uninversion",         "INTEGER NOT NULL DEFAULT 0"),
        ("contracts_catalyst",        "INTEGER NOT NULL DEFAULT 0"),
        ("seasonal_tailwind",         "INTEGER NOT NULL DEFAULT 0"),
        ("death_cross",               "INTEGER NOT NULL DEFAULT 0"),
        ("insider_cluster_sell",      "INTEGER NOT NULL DEFAULT 0"),
        ("overbought_rsi",            "INTEGER NOT NULL DEFAULT 0"),
        ("congress_sale",             "INTEGER NOT NULL DEFAULT 0"),
        ("analyst_downgrade_cluster", "INTEGER NOT NULL DEFAULT 0"),
        ("extreme_greed",             "INTEGER NOT NULL DEFAULT 0"),
        ("earnings_soon",             "INTEGER NOT NULL DEFAULT 0"),
        ("mixed_signals",             "INTEGER NOT NULL DEFAULT 0"),
        ("vix_spike_contrarian",      "INTEGER NOT NULL DEFAULT 0"),
        ("aaii_bearish_extreme",      "INTEGER NOT NULL DEFAULT 0"),
        ("put_call_fear",             "INTEGER NOT NULL DEFAULT 0"),
        ("aaii_bullish_euphoria",     "INTEGER NOT NULL DEFAULT 0"),
        ("margin_debt_deleveraging",  "INTEGER NOT NULL DEFAULT 0"),
        ("margin_debt_euphoria",      "INTEGER NOT NULL DEFAULT 0"),
    ]:
        _try_add_column(conn, "boom_scores", col, col_def)

    _try_add_column(conn, "contracts", "ticker", "TEXT")
    _try_add_column(conn, "seasonality", "anchors_json", "TEXT NOT NULL DEFAULT '[]'")
    _try_add_column(conn, "source_status", "error_detail", "TEXT")
    # Distinct from last_refreshed_at (= last attempt). See SourceStatus in models.py.
    _try_add_column(conn, "source_status", "last_success_at", "TEXT")
    _try_add_column(conn, "source_status", "last_duration_ms", "INTEGER")
    # When the scheduler tries again after an error/deferral (NULL when healthy).
    _try_add_column(conn, "source_status", "next_attempt_at", "TEXT")
    # Per-run traceback (source_status only keeps the latest) and next attempt.
    _try_add_column(conn, "source_runs", "error_detail", "TEXT")
    _try_add_column(conn, "source_runs", "next_attempt_at", "TEXT")
    _try_add_column(conn, "portfolio", "category", "TEXT")  # NULL = auto-classified
    # Native trading currency of each position. Every row before multi-currency
    # support was a US listing priced in dollars, so the default is USD.
    _try_add_column(conn, "portfolio", "currency", "TEXT NOT NULL DEFAULT 'USD'")
    _migrate_portfolio_accounts(conn)
    _try_add_column(conn, "notify_profile", "base_currency", "TEXT NOT NULL DEFAULT 'USD'")
    # Set once a user's FX watch list has been seeded, so removing every pair
    # sticks instead of re-seeding the defaults on the next read.
    _try_add_column(conn, "users", "fx_watch_seeded", "INTEGER NOT NULL DEFAULT 0")
    # Boom Score: components that can't apply to a listing (TASE has no SEC
    # Form 4, congress or federal-contract data) and how the score was rescaled.
    _try_add_column(conn, "boom_scores", "not_applicable", "TEXT NOT NULL DEFAULT '[]'")
    _try_add_column(conn, "boom_scores", "score_note", "TEXT NOT NULL DEFAULT ''")
    _try_add_column(conn, "app_settings", "x_accounts", "TEXT")  # comma list; NULL = env default
    # TA transition snapshot on alert_state (Phase 3 — warn before falls/breakouts).
    for col, col_def in [
        ("ta_recommendation",  "TEXT"),
        ("ta_breakout_status", "TEXT"),
        ("ta_ma_state",        "TEXT"),
        ("ta_conviction",      "INTEGER"),
    ]:
        _try_add_column(conn, "alert_state", col, col_def)
    _try_add_column(conn, "alerts", "user_id", "INTEGER")
    # Model version of each call and score (STC-19); rows from before are 'legacy'.
    _try_add_column(conn, "suggestion_history", "model_version", "TEXT NOT NULL DEFAULT 'legacy'")
    _try_add_column(conn, "boom_score_history", "model_version", "TEXT NOT NULL DEFAULT 'legacy'")
    # Auto-mute (STC-29): what each score left out, and the set in force per call.
    _try_add_column(conn, "boom_scores", "muted", "TEXT NOT NULL DEFAULT '[]'")
    _try_add_column(conn, "suggestion_history", "muted", "TEXT NOT NULL DEFAULT '[]'")
    _try_add_column(conn, "risk_settings", "regime_gate", "TEXT NOT NULL DEFAULT 'downgrade'")
    _try_add_column(conn, "yield_curve", "yr3m", "REAL")
    # Order guardrails and the global kill switch (STC-50, STC-53).
    for col, col_def in [
        ("max_order_notional", "REAL NOT NULL DEFAULT 25000"),
        ("max_orders_per_day", "INTEGER NOT NULL DEFAULT 50"),
        ("max_notional_per_day", "REAL NOT NULL DEFAULT 100000"),
        ("max_position_pct", "REAL NOT NULL DEFAULT 50"),
        ("allow_outside_hours", "INTEGER NOT NULL DEFAULT 0"),
        ("trading_halted_at", "TEXT"),
        ("trading_halt_reason", "TEXT"),
        ("max_loss_per_trade", "REAL"),
        ("max_daily_loss", "REAL"),
        ("min_reward_risk", "REAL"),
        ("require_stop", "INTEGER NOT NULL DEFAULT 0"),
        ("no_entry_after_open_min", "INTEGER"),
        ("no_entry_before_close_min", "INTEGER"),
    ]:
        _try_add_column(conn, "risk_settings", col, col_def)
    _try_add_column(conn, "congress_trades", "doc_id", "TEXT")
    _try_add_column(conn, "congress_trades", "source_url", "TEXT")
    _try_add_column(conn, "orders", "plan_stop", "REAL")
    _try_add_column(conn, "orders", "plan_target", "REAL")
    _try_add_column(conn, "orders", "filled_qty", "REAL NOT NULL DEFAULT 0")
    _migrate_ila_price_rows(conn)
    _migrate_econ_entities(conn)
    _seed_ledger(conn)
    conn.commit()


def _migrate_portfolio_accounts(conn: sqlite3.Connection) -> None:
    """One-time rebuild of `portfolio` with an `account` column in its key.

    Paper (and later live) positions sit beside the manually tracked ones, so
    the primary key becomes (user_id, account, ticker). Existing rows are the
    manual account. Skipped once the column exists.
    """
    if _has_column(conn, "portfolio", "account"):
        return
    conn.executescript(
        """
        CREATE TABLE portfolio_v3 (
            user_id  INTEGER NOT NULL DEFAULT 0,
            account  TEXT NOT NULL DEFAULT 'manual',
            ticker   TEXT NOT NULL,
            shares   REAL NOT NULL,
            avg_cost REAL NOT NULL,
            added_at TEXT NOT NULL,
            category TEXT,
            currency TEXT NOT NULL DEFAULT 'USD',
            PRIMARY KEY (user_id, account, ticker)
        );
        INSERT INTO portfolio_v3
            (user_id, account, ticker, shares, avg_cost, added_at, category, currency)
            SELECT user_id, 'manual', ticker, shares, avg_cost, added_at, category, currency
            FROM portfolio;
        DROP TABLE portfolio;
        ALTER TABLE portfolio_v3 RENAME TO portfolio;
        """
    )


def _seed_ledger(conn: sqlite3.Connection) -> None:
    """One-time: give every position that predates the ledger an opening BUY
    at its avg_cost / added_at, so replaying the ledger reproduces `portfolio`.
    The opening rows move no cash (the money was spent before cash existed)."""
    name = "ledger_seed_2026_10"
    if conn.execute("SELECT 1 FROM data_migrations WHERE name = ?", (name,)).fetchone():
        return
    conn.execute(
        """
        INSERT INTO transactions
            (user_id, account, ticker, side, qty, price, fees, currency,
             executed_at, source, cash_delta, flow)
        SELECT p.user_id, p.account, p.ticker, 'BUY', p.shares, p.avg_cost, 0, p.currency,
               p.added_at, 'migration', 0, p.shares * p.avg_cost
        FROM portfolio p
        WHERE NOT EXISTS (
            SELECT 1 FROM transactions t
            WHERE t.user_id = p.user_id AND t.account = p.account AND t.ticker = p.ticker)
        ORDER BY p.user_id, p.added_at, p.ticker
        """
    )
    conn.execute(
        "INSERT INTO data_migrations (name, applied_at) VALUES (?, ?)",
        (name, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )


# Shared market-data tables that hold prices per ticker.
_PRICE_TABLES = ("ohlc_series", "stock_analysis", "technical_signals", "seasonality")


def _migrate_ila_price_rows(conn: sqlite3.Connection) -> None:
    """One-time: drop TASE (".TA") price rows stored before ILA normalization.

    Yahoo quotes Tel Aviv listings in agorot; since app/currency.py every
    stored price is in shekels. Rows written earlier would mix units with
    fresh ones, so they are deleted once (the scheduler re-fetches them) and
    legacy TASE holdings — which the new portfolio.currency column defaults to
    USD — are marked ILS. Recorded in `data_migrations` so it never repeats.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS data_migrations ("
        "name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    name = "ila_to_ils_2026_09"
    if conn.execute("SELECT 1 FROM data_migrations WHERE name = ?", (name,)).fetchone():
        return
    for table in _PRICE_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE UPPER(ticker) LIKE '%.TA'")
    conn.execute("UPDATE portfolio SET currency = 'ILS' WHERE UPPER(ticker) LIKE '%.TA'")
    conn.execute(
        "INSERT INTO data_migrations (name, applied_at) VALUES (?, ?)",
        (name, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )


def _migrate_econ_entities(conn: sqlite3.Connection) -> None:
    """One-time: decode HTML entities stored in econ_events before the parser
    decoded them. Nasdaq sends "&nbsp;" for a value that isn't out yet; past
    events are never re-fetched, so their rows are fixed in place (same
    empty → NULL rule as econ_calendar._clean_value)."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS data_migrations ("
        "name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    name = "econ_html_entities_2026_09"
    if conn.execute("SELECT 1 FROM data_migrations WHERE name = ?", (name,)).fetchone():
        return
    rows = conn.execute(
        "SELECT event_id, event, actual, forecast, previous FROM econ_events "
        "WHERE event LIKE '%&%;%' OR actual LIKE '%&%;%' "
        "OR forecast LIKE '%&%;%' OR previous LIKE '%&%;%'"
    ).fetchall()

    def clean(v):
        if v is None:
            return None
        s = html.unescape(v).replace(" ", " ").strip()
        return None if s in ("", "-", "—", "N/A", "n/a") else s

    for r in rows:
        conn.execute(
            "UPDATE econ_events SET event = ?, actual = ?, forecast = ?, previous = ? "
            "WHERE event_id = ?",
            (html.unescape(r[1]).strip() or r[1], clean(r[2]), clean(r[3]), clean(r[4]), r[0]),
        )
    conn.execute(
        "INSERT INTO data_migrations (name, applied_at) VALUES (?, ?)",
        (name, datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    conn.commit()


# ---------- contracts ----------
def upsert_contracts(conn: sqlite3.Connection, records: list[ContractRecord]) -> None:
    from app.data.contractors import match_ticker  # lazy import to avoid circular deps

    rows = []
    for r in records:
        d = r.model_dump()
        d["ticker"] = match_ticker(r.recipient_name)
        rows.append(d)

    conn.executemany(
        """
        INSERT INTO contracts
            (external_id, award_id, recipient_name, amount, awarding_agency,
             start_date, source, ticker)
        VALUES (:external_id, :award_id, :recipient_name, :amount, :awarding_agency,
                :start_date, :source, :ticker)
        ON CONFLICT(external_id) DO UPDATE SET
            award_id=excluded.award_id,
            recipient_name=excluded.recipient_name,
            amount=excluded.amount,
            awarding_agency=excluded.awarding_agency,
            start_date=excluded.start_date,
            source=excluded.source,
            ticker=excluded.ticker
        """,
        rows,
    )
    conn.commit()


def get_contracts(conn: sqlite3.Connection, limit: int = 100) -> list[ContractRecord]:
    cur = conn.execute(
        "SELECT * FROM contracts ORDER BY amount DESC LIMIT ?", (limit,)
    )
    return [ContractRecord(**dict(row)) for row in cur.fetchall()]


# ---------- news ----------
def upsert_news(conn: sqlite3.Connection, records: list[NewsArticle]) -> None:
    conn.executemany(
        """
        INSERT INTO news (url, title, domain, seendate, sourcecountry, image, ticker)
        VALUES (:url, :title, :domain, :seendate, :sourcecountry, :image, :ticker)
        ON CONFLICT(url) DO UPDATE SET
            title=excluded.title,
            domain=excluded.domain,
            seendate=excluded.seendate,
            sourcecountry=excluded.sourcecountry,
            image=excluded.image,
            -- a ticker tag, once earned, is never wiped by an untagged macro hit
            ticker=CASE WHEN excluded.ticker != '' THEN excluded.ticker ELSE news.ticker END
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()


def get_news(conn: sqlite3.Connection, limit: int = 120) -> list[NewsArticle]:
    cur = conn.execute(
        "SELECT * FROM news ORDER BY seendate DESC LIMIT ?", (limit,)
    )
    return [NewsArticle(**dict(row)) for row in cur.fetchall()]


def get_company_names(conn: sqlite3.Connection, tickers: list[str]) -> dict[str, str]:
    """ticker -> latest known company name (from insider filings, where seen)."""
    if not tickers:
        return {}
    marks = ",".join("?" * len(tickers))
    cur = conn.execute(
        f"""
        SELECT ticker, company FROM insider_trades
        WHERE ticker IN ({marks}) AND company != ''
        GROUP BY ticker HAVING MAX(filed_at)
        """,
        [t.upper() for t in tickers],
    )
    return {row["ticker"]: row["company"] for row in cur.fetchall()}


def get_all_company_names(conn: sqlite3.Connection) -> dict[str, str]:
    """ticker -> company name for every ticker we have one for.

    Prefers the fundamentals profile name (properly cased, e.g. "NVIDIA
    Corporation") and falls back to the SEC filing issuer name, which is
    all-caps but exists for tickers Yahoo hasn't been asked about. Tickers with
    neither are simply absent — callers fall back to the symbol.
    """
    names: dict[str, str] = {}
    for row in conn.execute(
        "SELECT ticker, company FROM insider_trades WHERE company != '' "
        "GROUP BY ticker HAVING MAX(filed_at)"
    ):
        names[row["ticker"]] = row["company"]
    for row in conn.execute(
        "SELECT ticker, name FROM fundamentals WHERE name IS NOT NULL AND name != ''"
    ):
        names[row["ticker"]] = row["name"]
    return names


# ---------- insider trades ----------
def upsert_trades(conn: sqlite3.Connection, records: list[InsiderTrade]) -> None:
    conn.executemany(
        """
        INSERT INTO insider_trades
            (accession, ticker, company, owner, role, transaction_date,
             transaction_type, shares, value, filing_url, filed_at)
        VALUES
            (:accession, :ticker, :company, :owner, :role, :transaction_date,
             :transaction_type, :shares, :value, :filing_url, :filed_at)
        ON CONFLICT(accession) DO UPDATE SET
            ticker=excluded.ticker,
            company=excluded.company,
            owner=excluded.owner,
            role=excluded.role,
            transaction_date=excluded.transaction_date,
            transaction_type=excluded.transaction_type,
            shares=excluded.shares,
            value=excluded.value,
            filing_url=excluded.filing_url,
            filed_at=excluded.filed_at
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()


def get_trades(conn: sqlite3.Connection, limit: int = 60) -> list[InsiderTrade]:
    cur = conn.execute(
        "SELECT * FROM insider_trades ORDER BY filed_at DESC, value DESC LIMIT ?",
        (limit,),
    )
    return [InsiderTrade(**dict(row)) for row in cur.fetchall()]


def get_trades_for(conn: sqlite3.Connection, ticker: str, limit: int = 25) -> list[InsiderTrade]:
    """This ticker's Form 4 filings, newest first. get_trades() is global and
    capped, so filtering it client-side would silently miss older filings."""
    cur = conn.execute(
        "SELECT * FROM insider_trades WHERE ticker = ? "
        "ORDER BY filed_at DESC, value DESC LIMIT ?",
        (ticker, limit),
    )
    return [InsiderTrade(**dict(row)) for row in cur.fetchall()]


# ---------- watchlist (user managed, per-user) ----------
# ---- named watchlists (Task 13) ----
def _ensure_default_watchlist(conn: sqlite3.Connection, user_id: int) -> int:
    """Return the user's first list id, creating a default "My Watchlist" if
    they have none yet. Every watchlist operation resolves through here."""
    row = conn.execute(
        "SELECT id FROM watchlists WHERE user_id = ? ORDER BY sort_order, id LIMIT 1",
        (user_id,),
    ).fetchone()
    if row:
        return row[0]
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO watchlists (user_id, name, created_at, sort_order) VALUES (?, 'My Watchlist', ?, 0)",
        (user_id, now),
    )
    conn.commit()
    return cur.lastrowid


def get_watchlists(conn: sqlite3.Connection, user_id: int) -> list[WatchlistMeta]:
    _ensure_default_watchlist(conn, user_id)
    cur = conn.execute(
        "SELECT id, name, created_at, sort_order FROM watchlists WHERE user_id = ? "
        "ORDER BY sort_order, id",
        (user_id,),
    )
    return [WatchlistMeta(**dict(row)) for row in cur.fetchall()]


def create_watchlist(conn: sqlite3.Connection, user_id: int, name: str, created_at: str) -> WatchlistMeta:
    clean = (name or "").strip()[:60] or "Watchlist"
    order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM watchlists WHERE user_id = ?", (user_id,),
    ).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO watchlists (user_id, name, created_at, sort_order) VALUES (?, ?, ?, ?)",
        (user_id, clean, created_at, order),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id, name, created_at, sort_order FROM watchlists WHERE id = ?", (cur.lastrowid,),
    ).fetchone()
    return WatchlistMeta(**dict(row))


def rename_watchlist(conn: sqlite3.Connection, user_id: int, list_id: int, name: str) -> bool:
    clean = (name or "").strip()[:60]
    if not clean:
        return False
    cur = conn.execute(
        "UPDATE watchlists SET name = ? WHERE id = ? AND user_id = ?", (clean, list_id, user_id))
    conn.commit()
    return cur.rowcount > 0


def delete_watchlist(conn: sqlite3.Connection, user_id: int, list_id: int) -> bool:
    """Delete a list and its tickers. Refuses to remove a user's only list."""
    count = conn.execute("SELECT COUNT(*) FROM watchlists WHERE user_id = ?", (user_id,)).fetchone()[0]
    if count <= 1:
        return False
    owns = conn.execute(
        "SELECT 1 FROM watchlists WHERE id = ? AND user_id = ?", (list_id, user_id)).fetchone()
    if not owns:
        return False
    conn.execute("DELETE FROM watchlist WHERE watchlist_id = ?", (list_id,))
    conn.execute("DELETE FROM watchlists WHERE id = ? AND user_id = ?", (list_id, user_id))
    conn.commit()
    return True


def add_watch(conn: sqlite3.Connection, user_id: int, item: WatchItem, list_id: int | None = None) -> None:
    lid = list_id if list_id is not None else _ensure_default_watchlist(conn, user_id)
    conn.execute(
        """
        INSERT INTO watchlist (user_id, watchlist_id, ticker, note, added_at)
        VALUES (:user_id, :wid, :ticker, :note, :added_at)
        ON CONFLICT(watchlist_id, ticker) DO UPDATE SET note=excluded.note
        """,
        {"user_id": user_id, "wid": lid, **item.model_dump()},
    )
    conn.commit()


def remove_watch(conn: sqlite3.Connection, user_id: int, ticker: str, list_id: int | None = None) -> None:
    if list_id is not None:
        conn.execute(
            "DELETE FROM watchlist WHERE watchlist_id = ? AND ticker = ?", (list_id, ticker))
    else:
        # Legacy callers (no list): drop the ticker from all of the user's lists.
        conn.execute(
            "DELETE FROM watchlist WHERE user_id = ? AND ticker = ?", (user_id, ticker))
    conn.commit()


def update_watch(
    conn: sqlite3.Connection,
    user_id: int,
    ticker: str,
    from_list_id: int,
    to_list_id: int | None = None,
    note: str | None = None,
) -> bool:
    """Move a ticker to another list and/or replace its note.

    Returns False when either list isn't the caller's or the ticker isn't on
    the source list. `note=None` keeps the existing note, so a pure move never
    loses it — the reason a client-side copy-then-delete is not good enough.
    Moving onto a list that already holds the ticker keeps the destination row
    and drops the source one, because (watchlist_id, ticker) is unique.
    """
    def owns(list_id: int) -> bool:
        return conn.execute(
            "SELECT 1 FROM watchlists WHERE id = ? AND user_id = ?", (list_id, user_id),
        ).fetchone() is not None

    if not owns(from_list_id):
        return False
    row = conn.execute(
        "SELECT note FROM watchlist WHERE watchlist_id = ? AND ticker = ?",
        (from_list_id, ticker),
    ).fetchone()
    if row is None:
        return False

    new_note = row[0] if note is None else note.strip()
    dest = from_list_id if to_list_id is None else to_list_id

    if dest == from_list_id:
        conn.execute(
            "UPDATE watchlist SET note = ? WHERE watchlist_id = ? AND ticker = ?",
            (new_note, from_list_id, ticker),
        )
    else:
        if not owns(dest):
            return False
        clash = conn.execute(
            "SELECT 1 FROM watchlist WHERE watchlist_id = ? AND ticker = ?", (dest, ticker),
        ).fetchone()
        if clash:
            conn.execute(
                "UPDATE watchlist SET note = ? WHERE watchlist_id = ? AND ticker = ?",
                (new_note, dest, ticker),
            )
            conn.execute(
                "DELETE FROM watchlist WHERE watchlist_id = ? AND ticker = ?",
                (from_list_id, ticker),
            )
        else:
            conn.execute(
                "UPDATE watchlist SET watchlist_id = ?, note = ? "
                "WHERE watchlist_id = ? AND ticker = ?",
                (dest, new_note, from_list_id, ticker),
            )
    conn.commit()
    return True


def get_watchlist(conn: sqlite3.Connection, user_id: int, list_id: int | None = None) -> list[WatchItem]:
    lid = list_id if list_id is not None else _ensure_default_watchlist(conn, user_id)
    cur = conn.execute(
        "SELECT ticker, note, added_at FROM watchlist WHERE watchlist_id = ? "
        "ORDER BY added_at DESC",
        (lid,),
    )
    return [WatchItem(**dict(row)) for row in cur.fetchall()]


def get_watchlist_map(conn: sqlite3.Connection, user_id: int) -> dict[str, list[str]]:
    """ticker -> names of this user's lists holding it (a ticker may be on several)."""
    cur = conn.execute(
        "SELECT w.ticker, l.name FROM watchlist w "
        "JOIN watchlists l ON l.id = w.watchlist_id "
        "WHERE l.user_id = ? ORDER BY l.sort_order, l.id",
        (user_id,),
    )
    out: dict[str, list[str]] = {}
    for row in cur.fetchall():
        out.setdefault(row["ticker"], []).append(row["name"])
    return out


def get_watched_tickers_for_user(conn: sqlite3.Connection, user_id: int) -> list[str]:
    """Distinct tickers across all of this user's watchlists (for their quotes)."""
    cur = conn.execute(
        "SELECT DISTINCT ticker FROM watchlist WHERE user_id = ? ORDER BY ticker", (user_id,))
    return [row[0] for row in cur.fetchall()]


def get_all_watched_tickers(conn: sqlite3.Connection) -> list[str]:
    """Every ticker on any user's watchlist — the ingestion pipeline's universe."""
    cur = conn.execute("SELECT DISTINCT ticker FROM watchlist ORDER BY ticker")
    return [row[0] for row in cur.fetchall()]


# ---------- source status ----------
def update_source_status(
    conn: sqlite3.Connection,
    source: str,
    last_refreshed_at: str | None,
    status: str,
    record_count: int,
    error_detail: str | None = None,
    success: bool = False,
    duration_ms: int | None = None,
) -> None:
    # error_detail is explicitly written (NULL on success) so a stale traceback
    # from a prior failure is cleared once the source recovers.
    #
    # last_success_at only advances when success=True, and it does so inside the
    # UPSERT (a CASE on excluded) rather than via a read-modify-write, so a
    # concurrent failure can never clobber a good timestamp.
    conn.execute(
        """
        INSERT INTO source_status
            (source, last_refreshed_at, status, record_count, error_detail,
             last_success_at, last_duration_ms, next_attempt_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(source) DO UPDATE SET
            last_refreshed_at=excluded.last_refreshed_at,
            status=excluded.status,
            record_count=excluded.record_count,
            error_detail=excluded.error_detail,
            last_duration_ms=excluded.last_duration_ms,
            next_attempt_at=NULL,
            last_success_at=CASE
                WHEN excluded.last_success_at IS NOT NULL THEN excluded.last_success_at
                ELSE source_status.last_success_at
            END
        """,
        (
            source, last_refreshed_at, status, record_count, error_detail,
            last_refreshed_at if success else None, duration_ms,
        ),
    )
    conn.commit()


def get_source_statuses(conn: sqlite3.Connection) -> list[SourceStatus]:
    cur = conn.execute("SELECT * FROM source_status ORDER BY source")
    return [SourceStatus(**dict(row)) for row in cur.fetchall()]


def mark_source_deferred(
    conn: sqlite3.Connection,
    source: str,
    at: str,
    status: str,
    next_attempt_at: str | None,
    duration_ms: int | None = None,
) -> None:
    """Stamp a deferral: the source asked to be retried later.

    Unlike a failure it says nothing bad about the data already stored, so
    record_count and last_success_at are left alone; only the attempt clock,
    the status line and the next attempt move.
    """
    conn.execute(
        """
        INSERT INTO source_status
            (source, last_refreshed_at, status, record_count, error_detail,
             last_success_at, last_duration_ms, next_attempt_at)
        VALUES (?, ?, ?, 0, NULL, NULL, ?, ?)
        ON CONFLICT(source) DO UPDATE SET
            last_refreshed_at=excluded.last_refreshed_at,
            status=excluded.status,
            error_detail=NULL,
            last_duration_ms=excluded.last_duration_ms,
            next_attempt_at=excluded.next_attempt_at
        """,
        (source, at, status, duration_ms, next_attempt_at),
    )
    conn.commit()


def set_source_next_attempt(
    conn: sqlite3.Connection, source: str, run_id: int | None, at: str | None
) -> None:
    """Record when the scheduler will try `source` again, on both the run row
    and the status row, so the page can say "retrying at 14:30" instead of
    leaving a failed source looking abandoned."""
    if run_id is not None:
        conn.execute("UPDATE source_runs SET next_attempt_at = ? WHERE id = ?", (at, run_id))
    conn.execute("UPDATE source_status SET next_attempt_at = ? WHERE source = ?", (at, source))
    conn.commit()


# ---------- run history (Server page / diagnostics) ----------
def record_source_run(
    conn: sqlite3.Connection,
    source: str,
    started_at: str,
    finished_at: str,
    outcome: str,
    duration_ms: int,
    record_count: int = 0,
    detail: str | None = None,
    error_detail: str | None = None,
    next_attempt_at: str | None = None,
) -> int:
    """Append one source execution and return its row id.

    `detail` is the one-line summary; `error_detail` the full traceback of this
    particular run (source_status only keeps the most recent one).
    """
    cur = conn.execute(
        """
        INSERT INTO source_runs
            (source, started_at, finished_at, outcome, duration_ms, record_count,
             detail, error_detail, next_attempt_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (source, started_at, finished_at, outcome, duration_ms, record_count,
         detail, error_detail, next_attempt_at),
    )
    conn.commit()
    return cur.lastrowid


def get_source_runs(
    conn: sqlite3.Connection, source: str | None = None, limit: int = 100
) -> list[SourceRun]:
    if source:
        cur = conn.execute(
            "SELECT * FROM source_runs WHERE source = ? ORDER BY id DESC LIMIT ?",
            (source, limit),
        )
    else:
        cur = conn.execute("SELECT * FROM source_runs ORDER BY id DESC LIMIT ?", (limit,))
    return [SourceRun(**dict(row)) for row in cur.fetchall()]


def get_source_run_stats(conn: sqlite3.Connection) -> dict[str, dict]:
    """Per-source run counters and durations, keyed by source name.

    Durations only count runs that actually fetched (ok/error); a deferral and
    a legacy skip return immediately and would drag the averages down.
    """
    cur = conn.execute(
        """
        SELECT source,
               COUNT(*)                                              AS runs_total,
               SUM(CASE WHEN outcome = 'ok'       THEN 1 ELSE 0 END) AS runs_ok,
               SUM(CASE WHEN outcome = 'error'    THEN 1 ELSE 0 END) AS runs_error,
               SUM(CASE WHEN outcome = 'deferred' THEN 1 ELSE 0 END) AS runs_deferred,
               AVG(CASE WHEN outcome IN ('ok', 'error') THEN duration_ms END) AS avg_duration_ms,
               MAX(CASE WHEN outcome IN ('ok', 'error') THEN duration_ms END) AS max_duration_ms
        FROM source_runs
        GROUP BY source
        """
    )
    return {row["source"]: dict(row) for row in cur.fetchall()}


def record_job_run(
    conn: sqlite3.Connection,
    job_id: str,
    event: str,
    at: str,
    duration_ms: int | None = None,
    detail: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO job_runs (job_id, event, at, duration_ms, detail) VALUES (?, ?, ?, ?, ?)",
        (job_id, event, at, duration_ms, detail),
    )
    conn.commit()


def get_job_runs(
    conn: sqlite3.Connection, limit: int = 100, job_id: str | None = None
) -> list[JobRun]:
    if job_id:
        cur = conn.execute(
            "SELECT * FROM job_runs WHERE job_id = ? ORDER BY id DESC LIMIT ?", (job_id, limit))
    else:
        cur = conn.execute("SELECT * FROM job_runs ORDER BY id DESC LIMIT ?", (limit,))
    return [JobRun(**dict(row)) for row in cur.fetchall()]


# ---------- per-source schedules ----------
def _schedule_from_row(row) -> SourceSchedule:
    d = dict(row)
    return SourceSchedule(
        source=d["source"],
        mode=d["mode"],
        interval_seconds=d["interval_seconds"],
        times=[t for t in (d["times"] or "").split(",") if t],
        days=[x for x in (d["days"] or "").split(",") if x],
        tz=d["tz"],
        enabled=bool(d["enabled"]),
        retry_seconds=d["retry_seconds"],
        updated_at=d["updated_at"],
    )


def _schedule_params(s: SourceSchedule) -> tuple:
    return (s.source, s.mode, s.interval_seconds, ",".join(s.times), ",".join(s.days),
            s.tz, 1 if s.enabled else 0, s.retry_seconds, s.updated_at)


def seed_source_schedules(conn: sqlite3.Connection, schedules: list[SourceSchedule]) -> None:
    """Insert default rows for sources that have none, and refresh rows that
    are still an untouched default (updated_at NULL — only an admin save via
    the Server page stamps it). An admin's edit is never overwritten and
    survives every restart; a stale seed follows the registry's new default
    (margin_debt moved from every 14 days to a Monday 06:00 slot)."""
    conn.executemany(
        """
        INSERT INTO source_schedules
            (source, mode, interval_seconds, times, days, tz, enabled, retry_seconds, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source) DO UPDATE SET
            mode=excluded.mode, interval_seconds=excluded.interval_seconds,
            times=excluded.times, days=excluded.days, tz=excluded.tz,
            enabled=excluded.enabled, retry_seconds=excluded.retry_seconds
        WHERE source_schedules.updated_at IS NULL AND excluded.updated_at IS NULL
        """,
        [_schedule_params(s) for s in schedules],
    )
    conn.commit()


def upsert_source_schedule(conn: sqlite3.Connection, s: SourceSchedule) -> None:
    conn.execute(
        """
        INSERT INTO source_schedules
            (source, mode, interval_seconds, times, days, tz, enabled, retry_seconds, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source) DO UPDATE SET
            mode=excluded.mode, interval_seconds=excluded.interval_seconds,
            times=excluded.times, days=excluded.days, tz=excluded.tz,
            enabled=excluded.enabled, retry_seconds=excluded.retry_seconds,
            updated_at=excluded.updated_at
        """,
        _schedule_params(s),
    )
    conn.commit()


def get_source_schedules(conn: sqlite3.Connection) -> dict[str, SourceSchedule]:
    cur = conn.execute("SELECT * FROM source_schedules ORDER BY source")
    return {row["source"]: _schedule_from_row(row) for row in cur.fetchall()}


def get_source_schedule(conn: sqlite3.Connection, source: str) -> SourceSchedule | None:
    row = conn.execute("SELECT * FROM source_schedules WHERE source = ?", (source,)).fetchone()
    return _schedule_from_row(row) if row else None


def prune_history(
    conn: sqlite3.Connection,
    source_run_days: int,
    job_run_days: int,
    boom_history_days: int,
) -> dict[str, int]:
    """Trim the append-only history tables. Returns rows deleted per table."""
    deleted = {}
    for table, column, days in (
        ("source_runs", "started_at", source_run_days),
        ("job_runs", "at", job_run_days),
        ("boom_score_history", "computed_at", boom_history_days),
        ("boom_component_history", "day", boom_history_days),
    ):
        cur = conn.execute(
            f"DELETE FROM {table} WHERE {column} < datetime('now', ?)",  # noqa: S608 - table/col are literals above
            (f"-{int(days)} days",),
        )
        deleted[table] = cur.rowcount
    conn.commit()
    return deleted


# ---------- yield curve ----------
def upsert_yield_curve(conn: sqlite3.Connection, records: list[YieldPoint]) -> None:
    conn.executemany(
        """
        INSERT INTO yield_curve (date, yr3m, yr2, yr10, yr30, spread)
        VALUES (:date, :yr3m, :yr2, :yr10, :yr30, :spread)
        ON CONFLICT(date) DO UPDATE SET
            yr3m=excluded.yr3m, yr2=excluded.yr2, yr10=excluded.yr10,
            yr30=excluded.yr30, spread=excluded.spread
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()


def get_latest_bill_rate(conn: sqlite3.Connection) -> tuple[float, str] | None:
    """(3-month Treasury yield in %, its date) — the risk-free rate."""
    row = conn.execute(
        "SELECT yr3m, date FROM yield_curve WHERE yr3m IS NOT NULL ORDER BY date DESC LIMIT 1").fetchone()
    return (row[0], row[1]) if row else None


def get_yield_curve(conn: sqlite3.Connection, days: int = 90) -> list[YieldPoint]:
    cur = conn.execute(
        "SELECT * FROM yield_curve WHERE date >= date('now', ?) ORDER BY date ASC",
        (f"-{days} days",),
    )
    return [YieldPoint(**dict(row)) for row in cur.fetchall()]


# ---------- economic calendar ----------
def upsert_econ_events(conn: sqlite3.Connection, records: list[EconEvent]) -> None:
    conn.executemany(
        """
        INSERT INTO econ_events
            (event_id, date, time, country, event, importance, importance_source,
             actual, forecast, previous, source, fetched_at)
        VALUES
            (:event_id, :date, :time, :country, :event, :importance, :importance_source,
             :actual, :forecast, :previous, :source, :fetched_at)
        ON CONFLICT(event_id) DO UPDATE SET
            time=excluded.time, importance=excluded.importance,
            importance_source=excluded.importance_source,
            actual=excluded.actual, forecast=excluded.forecast,
            previous=excluded.previous, source=excluded.source,
            fetched_at=excluded.fetched_at
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()


def get_econ_events(
    conn: sqlite3.Connection,
    days_ahead: int = 7,
    days_back: int = 1,
    importance: str | None = None,
) -> list[EconEvent]:
    sql = (
        "SELECT * FROM econ_events "
        "WHERE date >= date('now', ?) AND date <= date('now', ?)"
    )
    params: list = [f"-{days_back} days", f"+{days_ahead} days"]
    if importance:
        sql += " AND importance = ?"
        params.append(importance)
    sql += " ORDER BY date ASC, CASE WHEN time = '' THEN '99:99' ELSE time END ASC"
    cur = conn.execute(sql, params)
    return [EconEvent(**dict(row)) for row in cur.fetchall()]


# ---------- technical signals ----------
def upsert_technical_signals(conn: sqlite3.Connection, records: list[TechnicalSignal]) -> None:
    conn.executemany(
        """
        INSERT INTO technical_signals
            (ticker, fetched_at, price, change_pct, ma50, ma200,
             golden_cross, rsi14, high_52w, low_52w, prices_json,
             macd, macd_signal, macd_crossover, rel_volume, volume_json)
        VALUES
            (:ticker, :fetched_at, :price, :change_pct, :ma50, :ma200,
             :golden_cross, :rsi14, :high_52w, :low_52w, :prices_json,
             :macd, :macd_signal, :macd_crossover, :rel_volume, :volume_json)
        ON CONFLICT(ticker) DO UPDATE SET
            fetched_at=excluded.fetched_at, price=excluded.price,
            change_pct=excluded.change_pct, ma50=excluded.ma50,
            ma200=excluded.ma200, golden_cross=excluded.golden_cross,
            rsi14=excluded.rsi14, high_52w=excluded.high_52w,
            low_52w=excluded.low_52w, prices_json=excluded.prices_json,
            macd=excluded.macd, macd_signal=excluded.macd_signal,
            macd_crossover=excluded.macd_crossover, rel_volume=excluded.rel_volume,
            volume_json=excluded.volume_json
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()


def _row_to_technical_signal(d: dict) -> TechnicalSignal:
    gc = d.get("golden_cross")
    d["golden_cross"] = bool(gc) if gc is not None else None
    mc = d.get("macd_crossover")
    d["macd_crossover"] = bool(mc) if mc is not None else None
    d.setdefault("macd", None)
    d.setdefault("macd_signal", None)
    d.setdefault("rel_volume", None)
    d.setdefault("volume_json", "[]")
    return TechnicalSignal(**d)


def get_technical_signals(conn: sqlite3.Connection) -> list[TechnicalSignal]:
    cur = conn.execute("SELECT * FROM technical_signals ORDER BY ticker ASC")
    return [_row_to_technical_signal(dict(row)) for row in cur.fetchall()]


# ---------- fear & greed ----------
def upsert_fear_greed(conn: sqlite3.Connection, records: list[FearGreedSnapshot]) -> None:
    conn.executemany(
        """
        INSERT INTO fear_greed (captured_at, score, rating)
        VALUES (:captured_at, :score, :rating)
        ON CONFLICT(captured_at) DO UPDATE SET
            score=excluded.score, rating=excluded.rating
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()


def get_fear_greed(conn: sqlite3.Connection, days: int = 30) -> list[FearGreedSnapshot]:
    cur = conn.execute(
        "SELECT * FROM fear_greed WHERE captured_at >= datetime('now', ?) ORDER BY captured_at ASC",
        (f"-{days} days",),
    )
    return [FearGreedSnapshot(**dict(row)) for row in cur.fetchall()]


# ---------- VIX ----------
def upsert_vix(conn: sqlite3.Connection, records: list[VixPoint]) -> None:
    conn.executemany(
        """
        INSERT INTO vix_daily (date, close)
        VALUES (:date, :close)
        ON CONFLICT(date) DO UPDATE SET close=excluded.close
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()


def get_vix(conn: sqlite3.Connection, days: int = 180) -> list[VixPoint]:
    cur = conn.execute(
        "SELECT * FROM vix_daily WHERE date >= date('now', ?) ORDER BY date ASC",
        (f"-{days} days",),
    )
    return [VixPoint(**dict(row)) for row in cur.fetchall()]


def get_latest_vix_closes(conn: sqlite3.Connection, n: int = 2) -> list[float]:
    """Most recent N closes, oldest first (for threshold-crossing checks)."""
    cur = conn.execute(
        "SELECT close FROM vix_daily ORDER BY date DESC LIMIT ?", (n,)
    )
    closes = [row[0] for row in cur.fetchall()]
    return list(reversed(closes))


# ---------- AAII sentiment survey ----------
def upsert_aaii(conn: sqlite3.Connection, records: list[AaiiSentiment]) -> None:
    conn.executemany(
        """
        INSERT INTO aaii_sentiment (week_ending, bullish, neutral, bearish, fetched_at)
        VALUES (:week_ending, :bullish, :neutral, :bearish, :fetched_at)
        ON CONFLICT(week_ending) DO UPDATE SET
            bullish=excluded.bullish, neutral=excluded.neutral,
            bearish=excluded.bearish, fetched_at=excluded.fetched_at
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()


def get_aaii(conn: sqlite3.Connection, weeks: int = 52) -> list[AaiiSentiment]:
    cur = conn.execute(
        "SELECT * FROM aaii_sentiment WHERE week_ending >= date('now', ?) ORDER BY week_ending ASC",
        (f"-{weeks * 7} days",),
    )
    return [AaiiSentiment(**dict(row)) for row in cur.fetchall()]


def get_latest_aaii(conn: sqlite3.Connection) -> AaiiSentiment | None:
    cur = conn.execute(
        "SELECT * FROM aaii_sentiment ORDER BY week_ending DESC LIMIT 1"
    )
    row = cur.fetchone()
    return AaiiSentiment(**dict(row)) if row else None


# ---------- put/call ratio ----------
def upsert_put_call(conn: sqlite3.Connection, records: list[PutCallPoint]) -> None:
    conn.executemany(
        """
        INSERT INTO put_call (date, ratio)
        VALUES (:date, :ratio)
        ON CONFLICT(date) DO UPDATE SET ratio=excluded.ratio
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()


def get_put_call(conn: sqlite3.Connection, days: int = 180) -> list[PutCallPoint]:
    cur = conn.execute(
        "SELECT * FROM put_call WHERE date >= date('now', ?) ORDER BY date ASC",
        (f"-{days} days",),
    )
    return [PutCallPoint(**dict(row)) for row in cur.fetchall()]


def get_latest_put_call(conn: sqlite3.Connection) -> PutCallPoint | None:
    cur = conn.execute("SELECT * FROM put_call ORDER BY date DESC LIMIT 1")
    row = cur.fetchone()
    return PutCallPoint(**dict(row)) if row else None


# ---------- margin debt (FINRA, monthly) ----------
def upsert_margin_debt(conn: sqlite3.Connection, records: list[MarginDebtPoint]) -> None:
    conn.executemany(
        """
        INSERT INTO margin_debt (month, debit_balances)
        VALUES (:month, :debit_balances)
        ON CONFLICT(month) DO UPDATE SET debit_balances=excluded.debit_balances
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()


def get_margin_debt(conn: sqlite3.Connection) -> list[MarginDebtPoint]:
    # No date filter: rows are monthly (tiny) and YoY needs a year+ of history.
    cur = conn.execute("SELECT * FROM margin_debt ORDER BY month ASC")
    return [MarginDebtPoint(**dict(row)) for row in cur.fetchall()]


# ---------- congress trades ----------
def upsert_congress_trades(conn: sqlite3.Connection, records: list[CongressTrade]) -> None:
    conn.executemany(
        """
        INSERT INTO congress_trades
            (trade_hash, representative, party, state, ticker,
             asset_description, transaction_date, transaction_type,
             amount_range, filed_at, chamber, doc_id, source_url)
        VALUES
            (:trade_hash, :representative, :party, :state, :ticker,
             :asset_description, :transaction_date, :transaction_type,
             :amount_range, :filed_at, :chamber, :doc_id, :source_url)
        ON CONFLICT(trade_hash) DO UPDATE SET
            filed_at=excluded.filed_at, amount_range=excluded.amount_range,
            source_url=excluded.source_url
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()


def upsert_congress_filings(conn: sqlite3.Connection, filings: list[CongressFiling]) -> None:
    conn.executemany(
        "INSERT INTO congress_filings (doc_id, chamber, member, district, filed_at, url, status, rows, checked_at) "
        "VALUES (:doc_id, :chamber, :member, :district, :filed_at, :url, :status, :rows, :checked_at) "
        "ON CONFLICT(doc_id) DO UPDATE SET status = excluded.status, rows = excluded.rows, "
        "checked_at = excluded.checked_at", [f.model_dump() for f in filings])
    conn.commit()


def get_congress_filing_status(conn: sqlite3.Connection, doc_ids: list[str]) -> dict[str, str]:
    out = {}
    for i in range(0, len(doc_ids), 500):
        chunk = doc_ids[i:i + 500]
        cur = conn.execute(f"SELECT doc_id, status FROM congress_filings WHERE doc_id IN "
                           f"({','.join('?' * len(chunk))})", chunk)
        out.update({r[0]: r[1] for r in cur.fetchall()})
    return out


def replace_holdings_13f(conn: sqlite3.Connection, filings: list[Filing13F], holdings: list[Holding13F]) -> None:
    """Each filing replaces its filer's holdings, all in one transaction."""
    if not filings:
        return
    cols = Filing13F.model_fields
    insert = f"INSERT OR REPLACE INTO filings_13f ({', '.join(cols)}) VALUES ({', '.join(':' + k for k in cols)})"
    with _immediate(conn):
        for f in filings:
            conn.execute("DELETE FROM holdings_13f WHERE cik = ?", (f.cik,))
            conn.execute(insert, f.model_dump())
        conn.executemany(f"INSERT INTO holdings_13f ({', '.join(Holding13F.model_fields)}) "
                         f"VALUES ({', '.join(':' + k for k in Holding13F.model_fields)})",
                         [h.model_dump() for h in holdings])


def get_filings_13f(conn: sqlite3.Connection) -> list[Filing13F]:
    return [Filing13F(**dict(r)) for r in conn.execute("SELECT * FROM filings_13f ORDER BY filed DESC, filer")]


def get_holdings_13f(conn: sqlite3.Connection, ticker: str | None = None) -> list[Holding13F]:
    if ticker is None:
        cur = conn.execute("SELECT * FROM holdings_13f")
    else:
        cur = conn.execute("SELECT * FROM holdings_13f WHERE ticker = ? AND status != 'SOLD OUT' "
                           "ORDER BY pct DESC", (ticker.upper(),))
    return [Holding13F(**dict(r)) for r in cur.fetchall()]


def delete_legacy_house_trades(conn: sqlite3.Connection) -> int:
    """House rows from the old aggregator (no Clerk doc_id)."""
    cur = conn.execute("DELETE FROM congress_trades WHERE chamber = 'house' AND doc_id IS NULL")
    conn.commit()
    return cur.rowcount


def get_congress_trades(conn: sqlite3.Connection, limit: int = 100) -> list[CongressTrade]:
    cur = conn.execute(
        "SELECT * FROM congress_trades ORDER BY transaction_date DESC, filed_at DESC LIMIT ?",
        (limit,),
    )
    return [CongressTrade(**dict(row)) for row in cur.fetchall()]


# ---------- short interest ----------
def upsert_short_interest(conn: sqlite3.Connection, records: list[ShortInterest]) -> None:
    conn.executemany(
        """
        INSERT INTO short_interest
            (ticker, fetched_at, shares_short, short_pct_float, days_to_cover,
             prior_month_shares, squeeze_flag)
        VALUES
            (:ticker, :fetched_at, :shares_short, :short_pct_float, :days_to_cover,
             :prior_month_shares, :squeeze_flag)
        ON CONFLICT(ticker) DO UPDATE SET
            fetched_at=excluded.fetched_at,
            shares_short=excluded.shares_short,
            short_pct_float=excluded.short_pct_float,
            days_to_cover=excluded.days_to_cover,
            prior_month_shares=excluded.prior_month_shares,
            squeeze_flag=excluded.squeeze_flag
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()


def get_short_interest(conn: sqlite3.Connection) -> list[ShortInterest]:
    cur = conn.execute("SELECT * FROM short_interest ORDER BY ticker ASC")
    rows = []
    for row in cur.fetchall():
        d = dict(row)
        d["squeeze_flag"] = bool(d["squeeze_flag"])
        rows.append(ShortInterest(**d))
    return rows


def get_short_interest_for(conn: sqlite3.Connection, ticker: str) -> ShortInterest | None:
    cur = conn.execute("SELECT * FROM short_interest WHERE ticker = ?", (ticker,))
    row = cur.fetchone()
    if row is None:
        return None
    d = dict(row)
    d["squeeze_flag"] = bool(d["squeeze_flag"])
    return ShortInterest(**d)


# ---------- social sentiment ----------
def upsert_social_sentiment(conn: sqlite3.Connection, records: list[SocialSentiment]) -> None:
    conn.executemany(
        """
        INSERT INTO social_sentiment
            (ticker, fetched_at, mentions, upvotes, rank, rank_24h_ago, rank_change)
        VALUES
            (:ticker, :fetched_at, :mentions, :upvotes, :rank, :rank_24h_ago, :rank_change)
        ON CONFLICT(ticker) DO UPDATE SET
            fetched_at=excluded.fetched_at,
            mentions=excluded.mentions,
            upvotes=excluded.upvotes,
            rank=excluded.rank,
            rank_24h_ago=excluded.rank_24h_ago,
            rank_change=excluded.rank_change
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()


def get_social_sentiment(conn: sqlite3.Connection) -> list[SocialSentiment]:
    cur = conn.execute("SELECT * FROM social_sentiment ORDER BY rank ASC NULLS LAST")
    return [SocialSentiment(**dict(row)) for row in cur.fetchall()]


def get_social_for(conn: sqlite3.Connection, ticker: str) -> SocialSentiment | None:
    cur = conn.execute("SELECT * FROM social_sentiment WHERE ticker = ?", (ticker,))
    row = cur.fetchone()
    return SocialSentiment(**dict(row)) if row else None


# ---------- analyst signals ----------
def upsert_analyst_signals(conn: sqlite3.Connection, records: list[AnalystSignal]) -> None:
    conn.executemany(
        """
        INSERT INTO analyst_signals
            (ticker, fetched_at, next_earnings, rec_strong_buy, rec_buy, rec_hold, rec_sell,
             recent_upgrades, recent_downgrades, latest_action, latest_firm, latest_to_grade)
        VALUES
            (:ticker, :fetched_at, :next_earnings, :rec_strong_buy, :rec_buy, :rec_hold, :rec_sell,
             :recent_upgrades, :recent_downgrades, :latest_action, :latest_firm, :latest_to_grade)
        ON CONFLICT(ticker) DO UPDATE SET
            fetched_at=excluded.fetched_at,
            next_earnings=excluded.next_earnings,
            rec_strong_buy=excluded.rec_strong_buy,
            rec_buy=excluded.rec_buy,
            rec_hold=excluded.rec_hold,
            rec_sell=excluded.rec_sell,
            recent_upgrades=excluded.recent_upgrades,
            recent_downgrades=excluded.recent_downgrades,
            latest_action=excluded.latest_action,
            latest_firm=excluded.latest_firm,
            latest_to_grade=excluded.latest_to_grade
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()


def get_analyst_signals(conn: sqlite3.Connection) -> list[AnalystSignal]:
    cur = conn.execute("SELECT * FROM analyst_signals ORDER BY ticker ASC")
    return [AnalystSignal(**dict(row)) for row in cur.fetchall()]


def get_analyst_for(conn: sqlite3.Connection, ticker: str) -> AnalystSignal | None:
    cur = conn.execute("SELECT * FROM analyst_signals WHERE ticker = ?", (ticker,))
    row = cur.fetchone()
    return AnalystSignal(**dict(row)) if row else None


# ---------- earnings calendar ----------
def upsert_earnings(conn: sqlite3.Connection, records: list[EarningsEvent]) -> None:
    """Store calendar rows, keeping any figures the new fetch doesn't carry.

    Yahoo's forward calendar returns a bare date with no actuals, so a plain
    `excluded.*` update would erase the reported EPS/surprise/quarter of a
    company that has already announced. COALESCE keeps the stored value
    whenever the incoming row is NULL, and `is_estimate` only ever ratchets
    towards confirmed (1 -> 0), never back.
    """
    conn.executemany(
        """
        INSERT INTO earnings
            (ticker, event_date, is_estimate, timing, eps_estimate, eps_actual,
             surprise_pct, revenue_estimate, quarter, source, fetched_at)
        VALUES
            (:ticker, :event_date, :is_estimate, :timing, :eps_estimate, :eps_actual,
             :surprise_pct, :revenue_estimate, :quarter, :source, :fetched_at)
        ON CONFLICT(ticker, event_date) DO UPDATE SET
            is_estimate=MIN(earnings.is_estimate, excluded.is_estimate),
            timing=CASE WHEN excluded.timing != '' THEN excluded.timing
                        ELSE earnings.timing END,
            eps_estimate=COALESCE(excluded.eps_estimate, earnings.eps_estimate),
            eps_actual=COALESCE(excluded.eps_actual, earnings.eps_actual),
            surprise_pct=COALESCE(excluded.surprise_pct, earnings.surprise_pct),
            revenue_estimate=COALESCE(excluded.revenue_estimate, earnings.revenue_estimate),
            quarter=CASE WHEN excluded.quarter != '' THEN excluded.quarter
                         ELSE earnings.quarter END,
            source=excluded.source,
            fetched_at=excluded.fetched_at
        """,
        [{**r.model_dump(), "is_estimate": int(r.is_estimate)} for r in records],
    )
    conn.commit()


def get_earnings(
    conn: sqlite3.Connection,
    date_from: str | None = None,
    date_to: str | None = None,
    tickers: list[str] | None = None,
) -> list[EarningsEvent]:
    """Calendar rows, oldest first. Every filter is optional and ANDed."""
    where: list[str] = []
    params: list = []
    if date_from:
        where.append("event_date >= ?")
        params.append(date_from)
    if date_to:
        where.append("event_date <= ?")
        params.append(date_to)
    if tickers is not None:
        if not tickers:
            return []
        where.append(f"ticker IN ({','.join('?' * len(tickers))})")
        params.extend(tickers)
    sql = "SELECT * FROM earnings"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY event_date ASC, ticker ASC"
    cur = conn.execute(sql, params)
    return [_earnings_row(row) for row in cur.fetchall()]


def get_earnings_for(conn: sqlite3.Connection, ticker: str) -> list[EarningsEvent]:
    """One company's rows, oldest first (history then upcoming)."""
    cur = conn.execute(
        "SELECT * FROM earnings WHERE ticker = ? ORDER BY event_date ASC", (ticker,))
    return [_earnings_row(row) for row in cur.fetchall()]


def _earnings_row(row: sqlite3.Row) -> EarningsEvent:
    # SQLite has no bool type; is_estimate comes back as 0/1.
    data = dict(row)
    data["is_estimate"] = bool(data["is_estimate"])
    return EarningsEvent(**data)


# ---------- boom scores ----------
_BOOM_BOOL_COLS = (
    "golden_cross", "rsi_recovery", "insider_cluster_buy", "congress_buy",
    "short_squeeze", "wsb_rising", "analyst_upgrade",
    "near_52w_high", "macd_crossover", "volume_confirmed", "fear_greed_contrarian",
    "yield_uninversion", "contracts_catalyst", "seasonal_tailwind",
    "death_cross", "insider_cluster_sell", "overbought_rsi", "congress_sale",
    "analyst_downgrade_cluster", "extreme_greed", "earnings_soon", "mixed_signals",
    "vix_spike_contrarian", "aaii_bearish_extreme", "put_call_fear", "aaii_bullish_euphoria",
    "margin_debt_deleveraging", "margin_debt_euphoria",
)


def upsert_boom_scores(conn: sqlite3.Connection, records: list[BoomScore]) -> None:
    conn.executemany(
        """
        INSERT INTO boom_scores
            (ticker, computed_at, score, components,
             golden_cross, rsi_recovery, insider_cluster_buy, congress_buy,
             short_squeeze, wsb_rising, analyst_upgrade,
             near_52w_high, macd_crossover, volume_confirmed, fear_greed_contrarian,
             yield_uninversion, contracts_catalyst, seasonal_tailwind,
             death_cross, insider_cluster_sell, overbought_rsi, congress_sale,
             analyst_downgrade_cluster, extreme_greed, earnings_soon, mixed_signals,
             vix_spike_contrarian, aaii_bearish_extreme, put_call_fear, aaii_bullish_euphoria,
             margin_debt_deleveraging, margin_debt_euphoria,
             not_applicable, score_note, muted)
        VALUES
            (:ticker, :computed_at, :score, :components,
             :golden_cross, :rsi_recovery, :insider_cluster_buy, :congress_buy,
             :short_squeeze, :wsb_rising, :analyst_upgrade,
             :near_52w_high, :macd_crossover, :volume_confirmed, :fear_greed_contrarian,
             :yield_uninversion, :contracts_catalyst, :seasonal_tailwind,
             :death_cross, :insider_cluster_sell, :overbought_rsi, :congress_sale,
             :analyst_downgrade_cluster, :extreme_greed, :earnings_soon, :mixed_signals,
             :vix_spike_contrarian, :aaii_bearish_extreme, :put_call_fear, :aaii_bullish_euphoria,
             :margin_debt_deleveraging, :margin_debt_euphoria,
             :not_applicable, :score_note, :muted)
        ON CONFLICT(ticker) DO UPDATE SET
            computed_at=excluded.computed_at, score=excluded.score,
            components=excluded.components,
            golden_cross=excluded.golden_cross, rsi_recovery=excluded.rsi_recovery,
            insider_cluster_buy=excluded.insider_cluster_buy,
            congress_buy=excluded.congress_buy, short_squeeze=excluded.short_squeeze,
            wsb_rising=excluded.wsb_rising, analyst_upgrade=excluded.analyst_upgrade,
            near_52w_high=excluded.near_52w_high, macd_crossover=excluded.macd_crossover,
            volume_confirmed=excluded.volume_confirmed,
            fear_greed_contrarian=excluded.fear_greed_contrarian,
            yield_uninversion=excluded.yield_uninversion,
            contracts_catalyst=excluded.contracts_catalyst,
            seasonal_tailwind=excluded.seasonal_tailwind,
            death_cross=excluded.death_cross,
            insider_cluster_sell=excluded.insider_cluster_sell,
            overbought_rsi=excluded.overbought_rsi, congress_sale=excluded.congress_sale,
            analyst_downgrade_cluster=excluded.analyst_downgrade_cluster,
            extreme_greed=excluded.extreme_greed,
            earnings_soon=excluded.earnings_soon, mixed_signals=excluded.mixed_signals,
            vix_spike_contrarian=excluded.vix_spike_contrarian,
            aaii_bearish_extreme=excluded.aaii_bearish_extreme,
            put_call_fear=excluded.put_call_fear,
            aaii_bullish_euphoria=excluded.aaii_bullish_euphoria,
            margin_debt_deleveraging=excluded.margin_debt_deleveraging,
            margin_debt_euphoria=excluded.margin_debt_euphoria,
            not_applicable=excluded.not_applicable,
            score_note=excluded.score_note,
            muted=excluded.muted
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()


def _row_to_boom_score(d: dict) -> BoomScore:
    for col in _BOOM_BOOL_COLS:
        if col in d:
            d[col] = bool(d[col])
        else:
            d[col] = False
    return BoomScore(**d)


def get_boom_scores(conn: sqlite3.Connection) -> list[BoomScore]:
    cur = conn.execute("SELECT * FROM boom_scores ORDER BY score DESC")
    return [_row_to_boom_score(dict(row)) for row in cur.fetchall()]


# ---------- single-ticker helpers for boom_score computation ----------
def get_boom_score_for(conn: sqlite3.Connection, ticker: str) -> BoomScore | None:
    cur = conn.execute("SELECT * FROM boom_scores WHERE ticker = ?", (ticker,))
    row = cur.fetchone()
    return _row_to_boom_score(dict(row)) if row else None


def get_technical_signal_for(conn: sqlite3.Connection, ticker: str) -> TechnicalSignal | None:
    cur = conn.execute("SELECT * FROM technical_signals WHERE ticker = ?", (ticker,))
    row = cur.fetchone()
    if row is None:
        return None
    return _row_to_technical_signal(dict(row))


def count_insider_buys(conn: sqlite3.Connection, ticker: str, days: int = 30) -> int:
    cur = conn.execute(
        """
        SELECT COUNT(*) FROM insider_trades
        WHERE ticker = ?
          AND transaction_type = 'Buy'
          AND transaction_date >= date('now', ?)
        """,
        (ticker, f"-{days} days"),
    )
    return cur.fetchone()[0]


def has_congress_buy(conn: sqlite3.Connection, ticker: str, days: int = 30) -> bool:
    cur = conn.execute(
        """
        SELECT 1 FROM congress_trades
        WHERE ticker = ?
          AND transaction_type = 'Purchase'
          AND transaction_date >= date('now', ?)
        LIMIT 1
        """,
        (ticker, f"-{days} days"),
    )
    return cur.fetchone() is not None


# ---------- new helpers for enhanced boom score ----------

def count_insider_sells(conn: sqlite3.Connection, ticker: str, days: int = 30) -> int:
    cur = conn.execute(
        """
        SELECT COUNT(*) FROM insider_trades
        WHERE ticker = ?
          AND transaction_type = 'Sell'
          AND transaction_date >= date('now', ?)
        """,
        (ticker, f"-{days} days"),
    )
    return cur.fetchone()[0]


def has_congress_sale(conn: sqlite3.Connection, ticker: str, days: int = 30) -> bool:
    cur = conn.execute(
        """
        SELECT 1 FROM congress_trades
        WHERE ticker = ?
          AND transaction_type = 'Sale'
          AND transaction_date >= date('now', ?)
        LIMIT 1
        """,
        (ticker, f"-{days} days"),
    )
    return cur.fetchone() is not None


def get_congress_buys_for(conn: sqlite3.Connection, ticker: str, days: int = 30) -> list[dict]:
    cur = conn.execute(
        """
        SELECT transaction_date, transaction_type, amount_range
        FROM congress_trades
        WHERE ticker = ?
          AND transaction_type = 'Purchase'
          AND transaction_date >= date('now', ?)
        ORDER BY transaction_date DESC
        """,
        (ticker, f"-{days} days"),
    )
    return [dict(row) for row in cur.fetchall()]


def get_latest_fear_greed_score(conn: sqlite3.Connection) -> float | None:
    cur = conn.execute(
        "SELECT score FROM fear_greed ORDER BY captured_at DESC LIMIT 1"
    )
    row = cur.fetchone()
    return row[0] if row else None


def has_yield_uninversion(conn: sqlite3.Connection, days: int = 30) -> bool:
    """True if yield curve spread went from negative to positive in the last N days."""
    cur_latest = conn.execute(
        "SELECT spread FROM yield_curve ORDER BY date DESC LIMIT 1"
    )
    latest = cur_latest.fetchone()
    if not latest or latest[0] is None or latest[0] <= 0:
        return False
    cur_neg = conn.execute(
        """
        SELECT 1 FROM yield_curve
        WHERE date >= date('now', ?)
          AND spread < 0
        LIMIT 1
        """,
        (f"-{days} days",),
    )
    return cur_neg.fetchone() is not None


def has_major_contract_for(
    conn: sqlite3.Connection, ticker: str, days: int = 30, min_amount: float = 100_000_000
) -> bool:
    cur = conn.execute(
        """
        SELECT 1 FROM contracts
        WHERE ticker = ?
          AND amount >= ?
          AND start_date >= date('now', ?)
        LIMIT 1
        """,
        (ticker, min_amount, f"-{days} days"),
    )
    return cur.fetchone() is not None


# ---------- fundamentals ----------

def upsert_fundamentals(conn: sqlite3.Connection, records: list[Fundamentals]) -> None:
    conn.executemany(
        """
        INSERT INTO fundamentals
            (ticker, fetched_at, sector, industry, pe_ratio, forward_pe,
             peg_ratio, pb_ratio, revenue_growth, profit_margin, market_cap,
             name, website, country, city, employees, summary, officers_json,
             insider_pct, institution_pct, financial_currency)
        VALUES
            (:ticker, :fetched_at, :sector, :industry, :pe_ratio, :forward_pe,
             :peg_ratio, :pb_ratio, :revenue_growth, :profit_margin, :market_cap,
             :name, :website, :country, :city, :employees, :summary, :officers_json,
             :insider_pct, :institution_pct, :financial_currency)
        ON CONFLICT(ticker) DO UPDATE SET
            fetched_at=excluded.fetched_at, sector=excluded.sector,
            industry=excluded.industry, pe_ratio=excluded.pe_ratio,
            forward_pe=excluded.forward_pe, peg_ratio=excluded.peg_ratio,
            pb_ratio=excluded.pb_ratio, revenue_growth=excluded.revenue_growth,
            profit_margin=excluded.profit_margin, market_cap=excluded.market_cap,
            name=excluded.name, website=excluded.website, country=excluded.country,
            city=excluded.city, employees=excluded.employees, summary=excluded.summary,
            officers_json=excluded.officers_json, insider_pct=excluded.insider_pct,
            institution_pct=excluded.institution_pct,
            financial_currency=excluded.financial_currency
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()


def get_fundamentals(conn: sqlite3.Connection) -> list[Fundamentals]:
    cur = conn.execute("SELECT * FROM fundamentals ORDER BY ticker ASC")
    return [Fundamentals(**dict(row)) for row in cur.fetchall()]


def record_suggestion_history(
    conn: sqlite3.Connection, records: list[SuggestionHistoryEntry],
) -> int:
    """Snapshot suggestions for a day. First write for a (user, ticker, date)
    wins, so the recorded price is the one at the time the call was first made
    — re-opening the page later never rewrites history."""
    if not records:
        return 0
    cur = conn.executemany(
        """
        INSERT INTO suggestion_history
            (user_id, ticker, for_date, kind, action, price, created_at, model_version, muted)
        VALUES (:user_id, :ticker, :for_date, :kind, :action, :price, :created_at, :model_version,
                :muted)
        ON CONFLICT(user_id, ticker, for_date) DO NOTHING
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()
    return cur.rowcount


def get_suggestion_history(
    conn: sqlite3.Connection,
    user_id: int,
    ticker: str | None = None,
    months: int = 6,
) -> list[SuggestionHistoryEntry]:
    sql = (
        "SELECT user_id, ticker, for_date, kind, action, price, created_at, model_version, muted "
        "FROM suggestion_history WHERE user_id = ? "
        f"AND for_date >= date('now', '-{int(months)} months')"
    )
    params: list = [user_id]
    if ticker:
        sql += " AND ticker = ?"
        params.append(ticker.upper())
    sql += " ORDER BY for_date DESC, ticker ASC"
    cur = conn.execute(sql, params)
    return [SuggestionHistoryEntry(**dict(row)) for row in cur.fetchall()]


def get_drawings(conn: sqlite3.Connection, user_id: int, ticker: str) -> dict:
    """Stored chart annotations for one ticker. Returns the shapes plus the
    timestamp, so the client can resolve which copy is newer."""
    row = conn.execute(
        "SELECT shapes_json, updated_at FROM drawings WHERE user_id = ? AND ticker = ?",
        (user_id, ticker.upper()),
    ).fetchone()
    if row is None:
        return {"shapes": [], "updated_at": None}
    try:
        shapes = json.loads(row["shapes_json"])
    except (json.JSONDecodeError, TypeError):
        shapes = []
    return {"shapes": shapes, "updated_at": row["updated_at"]}


def save_drawings(
    conn: sqlite3.Connection, user_id: int, ticker: str, shapes: list, updated_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO drawings (user_id, ticker, shapes_json, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, ticker) DO UPDATE SET
            shapes_json = excluded.shapes_json, updated_at = excluded.updated_at
        """,
        (user_id, ticker.upper(), json.dumps(shapes), updated_at),
    )
    conn.commit()


# ---- drawing drafts: named per-user snapshots of a ticker's drawings ----
_DRAFT_COLS = "id, ticker, title, description, timeframe, shapes_json, created_at, updated_at"


def _draft_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["shapes"] = json.loads(d.pop("shapes_json"))
    except (json.JSONDecodeError, TypeError):
        d["shapes"] = []
    return d


def list_drawing_drafts(conn: sqlite3.Connection, user_id: int, ticker: str) -> list[dict]:
    """A user's drafts for one ticker, newest first."""
    cur = conn.execute(
        f"SELECT {_DRAFT_COLS} FROM drawing_drafts WHERE user_id = ? AND ticker = ?"
        " ORDER BY updated_at DESC, id DESC",
        (user_id, ticker.upper()),
    )
    return [_draft_row(r) for r in cur.fetchall()]


def get_drawing_draft(conn: sqlite3.Connection, user_id: int, draft_id: int) -> dict | None:
    """One draft, or None when it doesn't exist *or belongs to someone else* —
    callers answer both with the same 404 so ids don't leak across accounts."""
    row = conn.execute(
        f"SELECT {_DRAFT_COLS} FROM drawing_drafts WHERE id = ? AND user_id = ?",
        (draft_id, user_id),
    ).fetchone()
    return _draft_row(row) if row else None


def create_drawing_draft(
    conn: sqlite3.Connection, user_id: int, ticker: str, title: str,
    description: str, timeframe: str, shapes: list, now: str,
) -> dict:
    cur = conn.execute(
        "INSERT INTO drawing_drafts (user_id, ticker, title, description, timeframe,"
        " shapes_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, ticker.upper(), title, description, timeframe, json.dumps(shapes), now, now),
    )
    conn.commit()
    return get_drawing_draft(conn, user_id, cur.lastrowid)


def update_drawing_draft(
    conn: sqlite3.Connection, user_id: int, draft_id: int, now: str, *,
    title: str | None = None, description: str | None = None,
    timeframe: str | None = None, shapes: list | None = None,
) -> dict | None:
    """Patch the given fields of an owned draft. None if not found/not owned."""
    sets, params = [], []
    for col, val in (("title", title), ("description", description), ("timeframe", timeframe)):
        if val is not None:
            sets.append(f"{col} = ?")
            params.append(val)
    if shapes is not None:
        sets.append("shapes_json = ?")
        params.append(json.dumps(shapes))
    sets.append("updated_at = ?")
    params.append(now)
    cur = conn.execute(
        f"UPDATE drawing_drafts SET {', '.join(sets)} WHERE id = ? AND user_id = ?",
        (*params, draft_id, user_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        return None
    return get_drawing_draft(conn, user_id, draft_id)


def delete_drawing_draft(conn: sqlite3.Connection, user_id: int, draft_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM drawing_drafts WHERE id = ? AND user_id = ?", (draft_id, user_id),
    )
    conn.commit()
    return cur.rowcount > 0


def upsert_company_holders(conn: sqlite3.Connection, records: list[CompanyHolder]) -> None:
    conn.executemany(
        """
        INSERT INTO company_holders
            (ticker, kind, holder, pct_held, shares, value, reported_at)
        VALUES (:ticker, :kind, :holder, :pct_held, :shares, :value, :reported_at)
        ON CONFLICT(ticker, kind, holder) DO UPDATE SET
            pct_held=excluded.pct_held, shares=excluded.shares,
            value=excluded.value, reported_at=excluded.reported_at
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()


def get_company_holders_for(
    conn: sqlite3.Connection, ticker: str, limit: int = 10,
) -> list[CompanyHolder]:
    cur = conn.execute(
        "SELECT ticker, kind, holder, pct_held, shares, value, reported_at "
        "FROM company_holders WHERE ticker = ? "
        "ORDER BY pct_held DESC NULLS LAST, holder ASC LIMIT ?",
        (ticker, limit),
    )
    return [CompanyHolder(**dict(row)) for row in cur.fetchall()]


def get_fundamentals_for(conn: sqlite3.Connection, ticker: str) -> "Fundamentals | None":
    cur = conn.execute("SELECT * FROM fundamentals WHERE ticker = ?", (ticker,))
    row = cur.fetchone()
    return Fundamentals(**dict(row)) if row else None


# ---------- boom score history ----------

def insert_boom_score_history(conn: sqlite3.Connection, scores: list[BoomScore],
                              model_version: str = "legacy") -> None:
    """Append each score, and every component that fired with its weight."""
    conn.executemany(
        "INSERT INTO boom_score_history (ticker, score, computed_at, model_version) VALUES (?, ?, ?, ?)",
        [(s.ticker, s.score, s.computed_at, model_version) for s in scores],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO boom_component_history (ticker, day, component, weight, computed_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [(s.ticker, s.computed_at[:10], k, w, s.computed_at) for s in scores
         for k, w in json.loads(s.components or "{}").items()],
    )
    conn.commit()


def get_boom_component_history(conn: sqlite3.Connection, since: str) -> list[dict]:
    """Every (ticker, day) a component fired since `since` (a date), oldest first."""
    cur = conn.execute(
        "SELECT ticker, day, component, weight, computed_at FROM boom_component_history "
        "WHERE day >= ? ORDER BY day", (since,))
    return [dict(r) for r in cur.fetchall()]


# ---------- market regime & signal mutes ----------

def upsert_regime(conn: sqlite3.Connection, snaps: list[RegimeSnapshot]) -> None:
    conn.executemany(
        "INSERT INTO regime_history (date, label, score, inputs, computed_at) "
        "VALUES (:date, :label, :score, :inputs, :computed_at) "
        "ON CONFLICT(date) DO UPDATE SET label = excluded.label, score = excluded.score, "
        "inputs = excluded.inputs, computed_at = excluded.computed_at",
        [s.model_dump() for s in snaps])
    conn.commit()


def get_latest_regime(conn: sqlite3.Connection) -> RegimeSnapshot | None:
    row = conn.execute("SELECT * FROM regime_history ORDER BY date DESC LIMIT 1").fetchone()
    return RegimeSnapshot(**dict(row)) if row else None


def get_regime_history(conn: sqlite3.Connection, days: int = 90) -> list[RegimeSnapshot]:
    cur = conn.execute("SELECT * FROM regime_history WHERE date >= date('now', ?) ORDER BY date",
                       (f"-{int(days)} days",))
    return [RegimeSnapshot(**dict(r)) for r in cur.fetchall()]


def replace_signal_performance(conn: sqlite3.Connection, rows: list[SignalPerformance]) -> None:
    """The whole table is one rolling computation, so it is replaced, not merged."""
    with _immediate(conn):
        conn.execute("DELETE FROM signal_performance")
        conn.executemany(
            "INSERT INTO signal_performance (component, horizon_days, window_days, n, hits, hit_rate, "
            "mean_net, computed_at) VALUES (:component, :horizon_days, :window_days, :n, :hits, "
            ":hit_rate, :mean_net, :computed_at)", [r.model_dump() for r in rows])


def get_signal_performance(conn: sqlite3.Connection) -> list[SignalPerformance]:
    cur = conn.execute("SELECT * FROM signal_performance ORDER BY component, horizon_days")
    return [SignalPerformance(**dict(r)) for r in cur.fetchall()]


def get_signal_mutes(conn: sqlite3.Connection) -> list[SignalMute]:
    cur = conn.execute("SELECT * FROM signal_mutes ORDER BY component")
    return [SignalMute(**{**dict(r), "muted": bool(r["muted"])}) for r in cur.fetchall()]


def upsert_signal_mutes(conn: sqlite3.Connection, rows: list[SignalMute]) -> None:
    conn.executemany(
        "INSERT INTO signal_mutes (component, muted, since, hit_rate, n, reason, updated_at) "
        "VALUES (:component, :muted, :since, :hit_rate, :n, :reason, :updated_at) "
        "ON CONFLICT(component) DO UPDATE SET muted = excluded.muted, since = excluded.since, "
        "hit_rate = excluded.hit_rate, n = excluded.n, reason = excluded.reason, "
        "updated_at = excluded.updated_at",
        [{**r.model_dump(), "muted": int(r.muted)} for r in rows])
    conn.commit()


def get_mute_overrides(conn: sqlite3.Connection, user_id: int) -> dict[str, str]:
    cur = conn.execute("SELECT component, mode FROM signal_mute_overrides WHERE user_id = ?", (user_id,))
    return {r[0]: r[1] for r in cur.fetchall()}


def set_mute_override(conn: sqlite3.Connection, user_id: int, component: str, mode: str | None) -> None:
    """mode 'on' (always count), 'off' (never count) or None (follow the auto-mute)."""
    if mode is None:
        conn.execute("DELETE FROM signal_mute_overrides WHERE user_id = ? AND component = ?",
                     (user_id, component))
    else:
        conn.execute(
            "INSERT INTO signal_mute_overrides (user_id, component, mode) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, component) DO UPDATE SET mode = excluded.mode",
            (user_id, component, mode))
    conn.commit()


def get_boom_score_history(conn: sqlite3.Connection, ticker: str, days: int = 30) -> list[dict]:
    cur = conn.execute(
        """
        SELECT computed_at, score FROM boom_score_history
        WHERE ticker = ?
          AND computed_at >= datetime('now', ?)
        ORDER BY computed_at ASC
        """,
        (ticker, f"-{days} days"),
    )
    return [{"computed_at": row[0], "score": row[1]} for row in cur.fetchall()]


def get_boom_score_history_all(conn: sqlite3.Connection, since: str) -> list[dict]:
    """Every ticker's score history since a date, for the signal replay.

    Ordered by (ticker, time) so the caller can walk each series and spot a
    real threshold crossing rather than comparing unrelated rows.
    """
    cur = conn.execute(
        """
        SELECT ticker, computed_at, score, model_version FROM boom_score_history
        WHERE computed_at >= ?
        ORDER BY ticker ASC, computed_at ASC
        """,
        (since,),
    )
    return [{"ticker": r[0], "computed_at": r[1], "score": r[2], "model_version": r[3]}
            for r in cur.fetchall()]


# ---------- seasonality ----------

def upsert_seasonality(conn: sqlite3.Connection, records: list[Seasonality]) -> None:
    conn.executemany(
        """
        INSERT INTO seasonality
            (ticker, computed_at, as_of, history_years, windows_json, anchors_json)
        VALUES
            (:ticker, :computed_at, :as_of, :history_years, :windows_json, :anchors_json)
        ON CONFLICT(ticker) DO UPDATE SET
            computed_at=excluded.computed_at, as_of=excluded.as_of,
            history_years=excluded.history_years, windows_json=excluded.windows_json,
            anchors_json=excluded.anchors_json
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()


def get_seasonality(conn: sqlite3.Connection) -> list[Seasonality]:
    cur = conn.execute("SELECT * FROM seasonality ORDER BY ticker ASC")
    return [Seasonality(**dict(row)) for row in cur.fetchall()]


def get_seasonality_for(conn: sqlite3.Connection, ticker: str) -> Seasonality | None:
    cur = conn.execute("SELECT * FROM seasonality WHERE ticker = ?", (ticker,))
    row = cur.fetchone()
    return Seasonality(**dict(row)) if row else None


# ---------- x posts ----------

def upsert_x_posts(conn: sqlite3.Connection, records: list[XPost]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO x_posts
            (account, post_id, text, url, posted_at, tickers, fetched_at)
        VALUES
            (:account, :post_id, :text, :url, :posted_at, :tickers, :fetched_at)
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()


def get_x_posts(conn: sqlite3.Connection, limit: int = 100) -> list[XPost]:
    cur = conn.execute(
        "SELECT * FROM x_posts ORDER BY posted_at DESC, rowid DESC LIMIT ?", (limit,))
    return [XPost(**dict(row)) for row in cur.fetchall()]


def get_x_posts_for(conn: sqlite3.Connection, ticker: str, limit: int = 20) -> list[XPost]:
    """Posts relevant to `ticker`: those tagged with it at ingest (comma-joined
    `tickers` column) plus a word-boundary text match for posts where the symbol
    appears but wasn't tagged. Newest first, de-duplicated by (account, post_id)."""
    t = ticker.upper()
    cur = conn.execute(
        "SELECT * FROM x_posts "
        "WHERE (',' || UPPER(tickers) || ',') LIKE ? "
        "ORDER BY posted_at DESC, rowid DESC LIMIT ?",
        (f"%,{t},%", limit),
    )
    posts = [XPost(**dict(row)) for row in cur.fetchall()]
    seen = {(p.account, p.post_id) for p in posts}

    # Word-boundary scan over a bounded recent window for untagged mentions.
    pat = re.compile(rf"\b{re.escape(t)}\b", re.IGNORECASE)
    recent = conn.execute(
        "SELECT * FROM x_posts ORDER BY posted_at DESC, rowid DESC LIMIT 300")
    for row in recent.fetchall():
        p = XPost(**dict(row))
        if (p.account, p.post_id) in seen:
            continue
        if pat.search(p.text):
            posts.append(p)
            seen.add((p.account, p.post_id))

    posts.sort(key=lambda p: p.posted_at, reverse=True)
    return posts[:limit]


# ---------- portfolio (user managed; materialized from the ledger) ----------

@contextmanager
def _immediate(conn: sqlite3.Connection):
    """One write transaction taken up front (BEGIN IMMEDIATE).

    The request and refresh connections both write. A read-modify-write in
    SQLite's default deferred mode could read a position, lose the race for
    the write lock and fail; taking the lock first serializes the two writers
    on the database itself. Commits on success, rolls back on any error.
    """
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def _get_holding(conn, user_id: int, account: str, ticker: str) -> Holding | None:
    row = conn.execute(
        "SELECT ticker, shares, avg_cost, added_at, currency FROM portfolio "
        "WHERE user_id = ? AND account = ? AND ticker = ?",
        (user_id, account, ticker),
    ).fetchone()
    return Holding(**dict(row)) if row else None


def record_transaction(conn: sqlite3.Connection, tx: Transaction, funded: bool) -> Transaction:
    """Append `tx` to the ledger and apply it to `portfolio` and `cash_balances`
    in one transaction. Raises ledger.LedgerError (nothing written) when the
    rules refuse it: oversell, a currency that doesn't match the position, or
    cash going negative."""
    from app import ledger  # ledger imports db; local import breaks the cycle

    with _immediate(conn):
        return _record_transaction(conn, tx, funded, ledger)


def _record_transaction(conn, tx: Transaction, funded: bool, ledger) -> Transaction:
    """record_transaction's body, for callers already inside _immediate."""
    pos = None
    if tx.side not in ledger.CASH_SIDES:
        pos = _get_holding(conn, tx.user_id, tx.account, tx.ticker)
        if pos and tx.side != "ADJUST" and pos.currency != tx.currency:
            raise ledger.LedgerError(
                f"{tx.ticker} is held in {pos.currency}, not {tx.currency}")
        if pos is None and tx.side == "SELL":
            raise ledger.LedgerError(f"{tx.ticker} is not held in the {tx.account} account")
    eff = ledger.apply(pos, tx, funded)
    if eff.cash_delta:
        row = conn.execute(
            "SELECT balance FROM cash_balances WHERE user_id = ? AND account = ? AND currency = ?",
            (tx.user_id, tx.account, tx.currency),
        ).fetchone()
        after = (row[0] if row else 0.0) + eff.cash_delta
        if after < -ledger.EPS:
            raise ledger.LedgerError(
                f"not enough {tx.currency} cash: {row[0] if row else 0.0:,.2f} available, "
                f"{-eff.cash_delta:,.2f} needed")
        conn.execute(
            "INSERT INTO cash_balances (user_id, account, currency, balance) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, account, currency) DO UPDATE SET balance = excluded.balance",
            # Rounded so float noise never accumulates into "9,156.119999".
            (tx.user_id, tx.account, tx.currency, 0.0 if abs(after) <= ledger.EPS else round(after, 6)),
        )
    tx = tx.model_copy(update={
        "cash_delta": eff.cash_delta, "realized_pnl": eff.realized, "flow": eff.flow})
    d = tx.model_dump(exclude={"id"})
    cur = conn.execute(
        f"INSERT INTO transactions ({', '.join(d)}) VALUES ({', '.join('?' * len(d))})",
        tuple(d.values()),
    )
    tx = tx.model_copy(update={"id": cur.lastrowid})
    if tx.side in ledger.CASH_SIDES:
        return tx
    if eff.position is None:
        conn.execute(
            "DELETE FROM portfolio WHERE user_id = ? AND account = ? AND ticker = ?",
            (tx.user_id, tx.account, tx.ticker))
    else:
        p = eff.position
        conn.execute(
            "INSERT INTO portfolio (user_id, account, ticker, shares, avg_cost, added_at, currency) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, account, ticker) DO UPDATE SET "
            "shares = excluded.shares, avg_cost = excluded.avg_cost, currency = excluded.currency",
            (tx.user_id, tx.account, p.ticker, p.shares, p.avg_cost, p.added_at, p.currency),
        )
    return tx


def get_portfolio(conn: sqlite3.Connection, user_id: int, account: str = "manual") -> list[Holding]:
    cur = conn.execute(
        "SELECT ticker, shares, avg_cost, added_at, currency FROM portfolio "
        "WHERE user_id = ? AND account = ? ORDER BY ticker ASC",
        (user_id, account),
    )
    return [Holding(**dict(row)) for row in cur.fetchall()]


def get_cash(conn: sqlite3.Connection, user_id: int, account: str = "manual") -> list[CashBalance]:
    cur = conn.execute(
        "SELECT currency, balance FROM cash_balances "
        "WHERE user_id = ? AND account = ? ORDER BY currency",
        (user_id, account),
    )
    return [CashBalance(**dict(row)) for row in cur.fetchall()]


def get_target_weights(conn: sqlite3.Connection, user_id: int, account: str) -> dict[str, float]:
    cur = conn.execute("SELECT ticker, weight FROM target_weights WHERE user_id = ? AND account = ? "
                       "ORDER BY weight DESC", (user_id, account))
    return {r[0]: r[1] for r in cur.fetchall()}


def replace_target_weights(conn: sqlite3.Connection, user_id: int, account: str, weights: dict[str, float]) -> None:
    with _immediate(conn):
        conn.execute("DELETE FROM target_weights WHERE user_id = ? AND account = ?", (user_id, account))
        conn.executemany("INSERT INTO target_weights (user_id, account, ticker, weight) VALUES (?, ?, ?, ?)",
                         [(user_id, account, t, w) for t, w in weights.items()])


def get_ledger(conn: sqlite3.Connection, user_id: int, accounts: tuple[str, ...]) -> list[Transaction]:
    """Every ledger row of these accounts, oldest first (the whole history)."""
    marks = ",".join("?" * len(accounts))
    cur = conn.execute(f"SELECT * FROM transactions WHERE user_id = ? AND account IN ({marks}) "
                       f"ORDER BY executed_at, id", (user_id, *accounts))
    return [Transaction(**dict(r)) for r in cur.fetchall()]


def get_transactions(
    conn: sqlite3.Connection, user_id: int, account: str | None = None,
    ticker: str | None = None, limit: int = 500,
) -> list[Transaction]:
    """Ledger rows, newest first."""
    sql = "SELECT * FROM transactions WHERE user_id = ?"
    args: list = [user_id]
    if account:
        sql += " AND account = ?"
        args.append(account)
    if ticker:
        sql += " AND ticker = ?"
        args.append(ticker)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return [Transaction(**dict(r)) for r in conn.execute(sql, args).fetchall()]


def upsert_equity_snapshots(conn: sqlite3.Connection, snaps: list[EquitySnapshot]) -> None:
    """One row per (user, account, date); a later run the same day replaces it."""
    conn.executemany(
        """
        INSERT INTO equity_snapshots
            (user_id, account, date, base_currency, value_base, cash_base,
             invested_base, flow_base, computed_at)
        VALUES (:user_id, :account, :date, :base_currency, :value_base, :cash_base,
                :invested_base, :flow_base, :computed_at)
        ON CONFLICT(user_id, account, date) DO UPDATE SET
            base_currency = excluded.base_currency, value_base = excluded.value_base,
            cash_base = excluded.cash_base, invested_base = excluded.invested_base,
            flow_base = excluded.flow_base, computed_at = excluded.computed_at
        """,
        [s.model_dump() for s in snaps],
    )
    conn.commit()


def get_equity_snapshots(
    conn: sqlite3.Connection, user_id: int, account: str = "manual", since: str | None = None,
) -> list[EquitySnapshot]:
    """Oldest first."""
    cur = conn.execute(
        "SELECT * FROM equity_snapshots WHERE user_id = ? AND account = ? AND date >= ? "
        "ORDER BY date",
        (user_id, account, since or ""),
    )
    return [EquitySnapshot(**dict(r)) for r in cur.fetchall()]


def get_last_snapshot_before(
    conn: sqlite3.Connection, user_id: int, account: str, date: str,
) -> EquitySnapshot | None:
    row = conn.execute(
        "SELECT * FROM equity_snapshots WHERE user_id = ? AND account = ? AND date < ? "
        "ORDER BY date DESC LIMIT 1",
        (user_id, account, date),
    ).fetchone()
    return EquitySnapshot(**dict(row)) if row else None


def get_flows_since(
    conn: sqlite3.Connection, user_id: int, account: str, after: str, until: str,
) -> dict[str, float]:
    """{currency: net external flow} of ledger rows in (after, until]."""
    cur = conn.execute(
        "SELECT currency, SUM(flow) FROM transactions "
        "WHERE user_id = ? AND account = ? AND executed_at > ? AND executed_at <= ? "
        "AND flow != 0 GROUP BY currency",
        (user_id, account, after, until),
    )
    return {r[0]: r[1] for r in cur.fetchall()}


# ---------- orders ----------

OPEN_STATUSES = ("new", "submitted", "partially_filled")


def _order_cols(o: Order) -> dict:
    return o.model_dump(exclude={"id"})


def _insert_order(conn: sqlite3.Connection, o: Order) -> Order:
    d = _order_cols(o)
    cur = conn.execute(f"INSERT INTO orders ({', '.join(d)}) VALUES ({', '.join(':' + k for k in d)})", d)
    return o.model_copy(update={"id": cur.lastrowid})


def insert_order(conn: sqlite3.Connection, o: Order) -> Order:
    out = _insert_order(conn, o)
    conn.commit()
    return out


def get_order(conn: sqlite3.Connection, order_id: int) -> Order | None:
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return Order(**dict(row)) if row else None


def get_orders(conn: sqlite3.Connection, user_id: int | None = None, account: str | None = None,
               statuses: tuple[str, ...] | None = None, limit: int = 200) -> list[Order]:
    """Newest first."""
    sql, args = "SELECT * FROM orders WHERE 1 = 1", []
    if user_id is not None:
        sql += " AND user_id = ?"
        args.append(user_id)
    if account is not None:
        sql += " AND account = ?"
        args.append(account)
    if statuses:
        sql += f" AND status IN ({', '.join('?' * len(statuses))})"
        args.extend(statuses)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return [Order(**dict(r)) for r in conn.execute(sql, args)]


def set_order_status(conn: sqlite3.Connection, order_id: int, status: str, reason: str,
                     from_statuses: tuple[str, ...] = OPEN_STATUSES) -> bool:
    """Move an open order to `status` (cancelled / rejected). False when it was
    no longer in `from_statuses` — e.g. it filled first."""
    cur = conn.execute(
        f"UPDATE orders SET status = ?, reason = ? WHERE id = ? AND status IN "
        f"({', '.join('?' * len(from_statuses))})", (status, reason, order_id, *from_statuses))
    conn.commit()
    return cur.rowcount > 0


def fill_order(conn: sqlite3.Connection, order: Order, tx: Transaction,
               slippage_bps: float | None) -> Order | None:
    """Fill a resting order: the ledger row (position + cash) and the order's
    new status in one transaction, only if it is still open — a cancel that
    got there first wins. Raises ledger.LedgerError (nothing written) when the
    ledger refuses the fill, e.g. not enough cash."""
    from app import ledger

    with _immediate(conn):
        row = conn.execute("SELECT status FROM orders WHERE id = ?", (order.id,)).fetchone()
        if row is None or row[0] not in OPEN_STATUSES:
            return None
        _record_transaction(conn, tx.model_copy(update={"order_id": order.id}), True, ledger)
        conn.execute(
            "UPDATE orders SET status = 'filled', filled_at = ?, fill_price = ?, fees = ?, "
            "slippage_bps = ? WHERE id = ?",
            (tx.executed_at, tx.price, tx.fees, slippage_bps, order.id))
    return get_order(conn, order.id)


def apply_broker_update(conn: sqlite3.Connection, order: Order, tx: Transaction | None, funded: bool,
                        **fields) -> Order:
    """A broker's news about an order: the ledger row for any new fill and
    the order's new fields, in one transaction. Raises ledger.LedgerError
    (nothing written) when the ledger refuses the fill."""
    from app import ledger

    with _immediate(conn):
        if tx is not None:
            _record_transaction(conn, tx.model_copy(update={"order_id": order.id}), funded, ledger)
        if fields:
            conn.execute(f"UPDATE orders SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                         (*fields.values(), order.id))
    return get_order(conn, order.id)


def get_order_by_broker_id(conn: sqlite3.Connection, broker_id: str) -> Order | None:
    row = conn.execute("SELECT * FROM orders WHERE broker_order_id = ?", (broker_id,)).fetchone()
    return Order(**dict(row)) if row else None


def set_broker_order_id(conn: sqlite3.Connection, order_id: int, broker_id: str, status: str, reason: str = "") -> None:
    conn.execute("UPDATE orders SET broker_order_id = ?, status = ?, reason = ? WHERE id = ?",
                 (broker_id, status, reason, order_id))
    conn.commit()


def get_slippage(conn: sqlite3.Connection, user_id: int | None, account: str) -> list[float]:
    """Measured slippage (bps, + = cost) of every filled order in an account."""
    sql = "SELECT slippage_bps FROM orders WHERE account = ? AND status = 'filled' AND slippage_bps IS NOT NULL"
    args: list = [account]
    if user_id is not None:
        sql += " AND user_id = ?"
        args.append(user_id)
    return [r[0] for r in conn.execute(sql, args)]


def get_suggestion_ref(conn: sqlite3.Connection, user_id: int, ticker: str,
                       for_date: str) -> tuple[int, float | None] | None:
    """(rowid, recorded price) of a recorded suggestion — what an order placed
    from it links to and measures its slippage against."""
    row = conn.execute(
        "SELECT rowid, price FROM suggestion_history WHERE user_id = ? AND ticker = ? AND for_date = ?",
        (user_id, ticker, for_date)).fetchone()
    return (row[0], row[1]) if row else None


# ---------- exit rules & risk settings ----------

def _rule_from_row(row) -> PositionRule:
    d = dict(row)
    d["fired"] = json.loads(d.pop("fired_json") or "[]")
    d["active"] = bool(d["active"])
    return PositionRule(**d)


def get_position_rules(conn: sqlite3.Connection, user_id: int | None = None,
                       account: str | None = None, active_only: bool = False) -> list[PositionRule]:
    sql, args = "SELECT * FROM position_rules WHERE 1 = 1", []
    if user_id is not None:
        sql += " AND user_id = ?"
        args.append(user_id)
    if account is not None:
        sql += " AND account = ?"
        args.append(account)
    if active_only:
        sql += " AND active = 1"
    return [_rule_from_row(r) for r in conn.execute(sql + " ORDER BY user_id, account, ticker", args)]


def get_position_rule(conn: sqlite3.Connection, user_id: int, account: str,
                      ticker: str) -> PositionRule | None:
    row = conn.execute(
        "SELECT * FROM position_rules WHERE user_id = ? AND account = ? AND ticker = ?",
        (user_id, account, ticker)).fetchone()
    return _rule_from_row(row) if row else None


def upsert_position_rule(conn: sqlite3.Connection, rule: PositionRule) -> PositionRule:
    """Insert or replace a rule's levels. A save re-arms every kind (fired is
    cleared) and keeps created_at. trail_high is whatever the caller passes:
    kept across edits while a trail stays on, None when it's turned off."""
    conn.execute(
        """
        INSERT INTO position_rules (user_id, account, ticker, stop_price, stop_pct, take_profit,
                                    trailing_pct, trail_high, fired_json, active, created_at,
                                    updated_at)
        VALUES (:user_id, :account, :ticker, :stop_price, :stop_pct, :take_profit, :trailing_pct,
                :trail_high, '[]', :active, :created_at, :updated_at)
        ON CONFLICT(user_id, account, ticker) DO UPDATE SET
            stop_price = excluded.stop_price, stop_pct = excluded.stop_pct,
            take_profit = excluded.take_profit, trailing_pct = excluded.trailing_pct,
            trail_high = excluded.trail_high, fired_json = '[]', active = excluded.active,
            updated_at = excluded.updated_at
        """,
        {**rule.model_dump(exclude={"fired"}), "active": int(rule.active)},
    )
    conn.commit()
    return get_position_rule(conn, rule.user_id, rule.account, rule.ticker)


def delete_position_rule(conn: sqlite3.Connection, user_id: int, account: str, ticker: str) -> bool:
    cur = conn.execute(
        "DELETE FROM position_rules WHERE user_id = ? AND account = ? AND ticker = ?",
        (user_id, account, ticker))
    conn.commit()
    return cur.rowcount > 0


def ratchet_trail_high(conn: sqlite3.Connection, rule: PositionRule, price: float) -> float | None:
    """Raise trail_high to `price` if higher; never lowers it. Only touches the
    version of the rule that was read (an edit in between wins). Returns the
    stored high, or None when the rule changed or went away."""
    cur = conn.execute(
        "UPDATE position_rules SET trail_high = MAX(COALESCE(trail_high, ?), ?) "
        "WHERE user_id = ? AND account = ? AND ticker = ? AND updated_at = ?",
        (price, price, rule.user_id, rule.account, rule.ticker, rule.updated_at))
    conn.commit()
    if cur.rowcount == 0:
        return None
    return conn.execute(
        "SELECT trail_high FROM position_rules WHERE user_id = ? AND account = ? AND ticker = ?",
        (rule.user_id, rule.account, rule.ticker)).fetchone()[0]


def fire_rule(conn: sqlite3.Connection, rule: PositionRule, kind: str, alert: Alert) -> bool:
    """Record that `kind` fired and store its alert, atomically, only if the
    rule is still the version that was evaluated, still active, and hasn't
    already fired that kind. Returns whether the alert was written."""
    with _immediate(conn):
        row = conn.execute(
            "SELECT fired_json FROM position_rules WHERE user_id = ? AND account = ? "
            "AND ticker = ? AND updated_at = ? AND active = 1",
            (rule.user_id, rule.account, rule.ticker, rule.updated_at)).fetchone()
        if row is None:
            return False
        fired = json.loads(row[0] or "[]")
        if kind in fired:
            return False
        conn.execute(
            "UPDATE position_rules SET fired_json = ? WHERE user_id = ? AND account = ? AND ticker = ?",
            (json.dumps([*fired, kind]), rule.user_id, rule.account, rule.ticker))
        cur = conn.execute(
            """
            INSERT INTO alerts (dedup_key, created_at, ticker, type, severity, title, message,
                                read, pushed, user_id)
            VALUES (:dedup_key, :created_at, :ticker, :type, :severity, :title, :message, :read,
                    :pushed, :user_id)
            ON CONFLICT(dedup_key) DO NOTHING
            """,
            alert.model_dump())
    return cur.rowcount > 0


def mark_alert_pushed(conn: sqlite3.Connection, dedup_key: str) -> None:
    conn.execute("UPDATE alerts SET pushed = 1 WHERE dedup_key = ?", (dedup_key,))
    conn.commit()


def _risk_from_row(row) -> RiskSettings:
    d = dict(row)
    d["allow_outside_hours"] = bool(d.get("allow_outside_hours", 0))
    d["require_stop"] = bool(d.get("require_stop", 0))
    return RiskSettings(**d)


def get_risk_settings(conn: sqlite3.Connection, user_id: int) -> RiskSettings:
    row = conn.execute("SELECT * FROM risk_settings WHERE user_id = ?", (user_id,)).fetchone()
    return _risk_from_row(row) if row else RiskSettings(user_id=user_id)


def get_all_risk_settings(conn: sqlite3.Connection) -> list[RiskSettings]:
    return [_risk_from_row(r) for r in conn.execute("SELECT * FROM risk_settings")]


def upsert_risk_settings(conn: sqlite3.Connection, s: RiskSettings) -> RiskSettings:
    cols = list(RiskSettings.model_fields)
    conn.execute(
        f"INSERT INTO risk_settings ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)}) "
        f"ON CONFLICT(user_id) DO UPDATE SET "
        + ", ".join(f"{c} = excluded.{c}" for c in cols if c != "user_id"),
        {**s.model_dump(), "allow_outside_hours": int(s.allow_outside_hours), "require_stop": int(s.require_stop)})
    conn.commit()
    return get_risk_settings(conn, s.user_id)


def orders_today(conn: sqlite3.Connection, user_id: int, since: str) -> list[Order]:
    """Every order placed since `since` (an ISO timestamp) that wasn't refused."""
    cur = conn.execute("SELECT * FROM orders WHERE user_id = ? AND created_at >= ? AND status != 'rejected'",
                       (user_id, since))
    return [Order(**dict(r)) for r in cur.fetchall()]


# ---------- saved rules ----------

_SAVED_RULE_SELECT = ("SELECT s.*, (SELECT COUNT(*) FROM rule_variants v WHERE v.user_id = s.user_id "
                      "AND v.rule_key = s.name) AS variants_tested FROM saved_rules s")


def _saved_rule(row) -> SavedRule:
    return SavedRule(**{**dict(row), "active": bool(row["active"])})


def get_saved_rules(conn: sqlite3.Connection, user_id: int | None = None,
                    active_only: bool = False) -> list[SavedRule]:
    sql, args = _SAVED_RULE_SELECT + " WHERE 1 = 1", []
    if user_id is not None:
        sql += " AND s.user_id = ?"
        args.append(user_id)
    if active_only:
        sql += " AND s.active = 1"
    return [_saved_rule(r) for r in conn.execute(sql + " ORDER BY s.id", args)]


def get_saved_rule(conn: sqlite3.Connection, rule_id: int) -> SavedRule | None:
    row = conn.execute(_SAVED_RULE_SELECT + " WHERE s.id = ?", (rule_id,)).fetchone()
    return _saved_rule(row) if row else None


def save_rule(conn: sqlite3.Connection, r: SavedRule) -> SavedRule:
    d = {**r.model_dump(exclude={"id", "variants_tested"}), "active": int(r.active)}
    if r.id is None:
        cur = conn.execute(f"INSERT INTO saved_rules ({', '.join(d)}) VALUES ({', '.join(':' + k for k in d)})", d)
        rid = cur.lastrowid
    else:
        conn.execute("UPDATE saved_rules SET name = :name, spec = :spec, model_version = :model_version, "
                     "active = :active, updated_at = :updated_at WHERE id = :id AND user_id = :user_id",
                     {**d, "id": r.id})
        rid = r.id
    conn.commit()
    return get_saved_rule(conn, rid)


def delete_saved_rule(conn: sqlite3.Connection, user_id: int, rule_id: int) -> bool:
    cur = conn.execute("DELETE FROM saved_rules WHERE id = ? AND user_id = ?", (rule_id, user_id))
    conn.execute("DELETE FROM rule_signals WHERE rule_id = ? AND user_id = ?", (rule_id, user_id))
    conn.commit()
    return cur.rowcount > 0


def note_variant(conn: sqlite3.Connection, user_id: int, rule_key: str, spec_hash: str, at: str) -> int:
    """Record that this variant was tested; returns how many distinct
    variants of the rule this user has tested."""
    conn.execute("INSERT OR IGNORE INTO rule_variants (user_id, rule_key, spec_hash, tested_at) "
                 "VALUES (?, ?, ?, ?)", (user_id, rule_key, spec_hash, at))
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM rule_variants WHERE user_id = ? AND rule_key = ?",
                        (user_id, rule_key)).fetchone()[0]


# ---------- market-wide scanner ----------

def replace_universe(conn: sqlite3.Connection, members: list[UniverseMember]) -> None:
    """Swap each fetched universe's constituents for the new list in one go."""
    names = {m.universe for m in members}
    with _immediate(conn):
        conn.executemany("DELETE FROM universe_members WHERE universe = ?", [(n,) for n in names])
        conn.executemany("INSERT OR IGNORE INTO universe_members (universe, ticker, name, refreshed_at) "
                         "VALUES (:universe, :ticker, :name, :refreshed_at)", [m.model_dump() for m in members])


def get_universe(conn: sqlite3.Connection, universe: str) -> list[UniverseMember]:
    cur = conn.execute("SELECT * FROM universe_members WHERE universe = ? ORDER BY ticker", (universe,))
    return [UniverseMember(**dict(r)) for r in cur.fetchall()]


def get_universe_refreshed(conn: sqlite3.Connection) -> dict[str, tuple[int, str]]:
    """{universe: (member count, refreshed_at)} for the fetched universes."""
    cur = conn.execute("SELECT universe, COUNT(*), MAX(refreshed_at) FROM universe_members GROUP BY universe")
    return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def get_all_held_tickers(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT ticker FROM portfolio ORDER BY ticker")]


def get_latest_ohlc_fetched(conn: sqlite3.Connection, tickers: list[str],
                            intervals: tuple[str, ...]) -> dict[str, str]:
    """{ticker: newest fetched_at across `intervals`} for the given tickers."""
    if not tickers:
        return {}
    marks, imarks = ",".join("?" * len(tickers)), ",".join("?" * len(intervals))
    cur = conn.execute(f"SELECT ticker, MAX(fetched_at) FROM ohlc_series WHERE ticker IN ({marks}) "
                       f"AND interval IN ({imarks}) GROUP BY ticker", [*tickers, *intervals])
    return {r[0]: r[1] for r in cur.fetchall()}


def prune_ohlc(conn: sqlite3.Connection, interval: str, keep: list[str]) -> int:
    """Drop `interval` series for tickers no longer in `keep`."""
    marks = ",".join("?" * len(keep)) or "''"
    cur = conn.execute(f"DELETE FROM ohlc_series WHERE interval = ? AND ticker NOT IN ({marks})", [interval, *keep])
    conn.commit()
    return cur.rowcount


def upsert_scan_hits(conn: sqlite3.Connection, hits: list[ScanHit]) -> None:
    conn.executemany(
        "INSERT INTO scan_hits (scan, ticker, day, price, change_pct, metric, first_seen_at, last_seen_at) "
        "VALUES (:scan, :ticker, :day, :price, :change_pct, :metric, :first_seen_at, :last_seen_at) "
        "ON CONFLICT(scan, ticker, day) DO UPDATE SET price = excluded.price, change_pct = excluded.change_pct, "
        "metric = excluded.metric, last_seen_at = excluded.last_seen_at",
        [h.model_dump() for h in hits])
    conn.commit()


def get_scan_hits(conn: sqlite3.Connection, day: str | None = None) -> list[ScanHit]:
    """Every match of `day` (default: the latest day with any)."""
    if day is None:
        day = conn.execute("SELECT MAX(day) FROM scan_hits").fetchone()[0]
        if day is None:
            return []
    cur = conn.execute("SELECT * FROM scan_hits WHERE day = ? ORDER BY first_seen_at DESC", (day,))
    return [ScanHit(**dict(r)) for r in cur.fetchall()]


def prune_scan_hits(conn: sqlite3.Connection, before_day: str) -> int:
    cur = conn.execute("DELETE FROM scan_hits WHERE day < ?", (before_day,))
    conn.execute("DELETE FROM scanner_alert_log WHERE day < ?", (before_day,))
    conn.commit()
    return cur.rowcount


def get_scanner_prefs(conn: sqlite3.Connection, user_id: int, default_per_hour: int = 3) -> ScannerPrefs:
    row = conn.execute("SELECT * FROM scanner_prefs WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return ScannerPrefs(user_id=user_id, per_hour=default_per_hour)
    return ScannerPrefs(**{**dict(row), "alerts_enabled": bool(row["alerts_enabled"])})


def upsert_scanner_prefs(conn: sqlite3.Connection, p: ScannerPrefs) -> ScannerPrefs:
    conn.execute("INSERT INTO scanner_prefs (user_id, alerts_enabled, per_hour, updated_at) "
                 "VALUES (:user_id, :alerts_enabled, :per_hour, :updated_at) ON CONFLICT(user_id) DO UPDATE SET "
                 "alerts_enabled = excluded.alerts_enabled, per_hour = excluded.per_hour, "
                 "updated_at = excluded.updated_at", {**p.model_dump(), "alerts_enabled": int(p.alerts_enabled)})
    conn.commit()
    return get_scanner_prefs(conn, p.user_id)


def get_scanner_alert_users(conn: sqlite3.Connection) -> list[ScannerPrefs]:
    cur = conn.execute("SELECT * FROM scanner_prefs WHERE alerts_enabled = 1")
    return [ScannerPrefs(**{**dict(r), "alerts_enabled": True}) for r in cur.fetchall()]


def get_scanner_log(conn: sqlite3.Connection, user_id: int, day: str) -> list[dict]:
    cur = conn.execute("SELECT * FROM scanner_alert_log WHERE user_id = ? AND day = ?", (user_id, day))
    return [dict(r) for r in cur.fetchall()]


def get_scanner_sends_since(conn: sqlite3.Connection, user_id: int, since: str) -> tuple[int, str | None]:
    """(individual alerts sent since `since`, the newest digest time ever)."""
    sent = conn.execute("SELECT COUNT(*) FROM scanner_alert_log WHERE user_id = ? AND state = 'sent' "
                        "AND logged_at >= ?", (user_id, since)).fetchone()[0]
    digest = conn.execute("SELECT MAX(digested_at) FROM scanner_alert_log WHERE user_id = ?",
                          (user_id,)).fetchone()[0]
    return sent, digest


def log_scanner_alerts(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany("INSERT OR IGNORE INTO scanner_alert_log (user_id, ticker, day, scan, state, logged_at) "
                     "VALUES (:user_id, :ticker, :day, :scan, :state, :logged_at)", rows)


def mark_scanner_digested(conn: sqlite3.Connection, user_id: int, tickers: list[str], day: str, at: str) -> None:
    conn.executemany("UPDATE scanner_alert_log SET state = 'digested', digested_at = ? "
                     "WHERE user_id = ? AND ticker = ? AND day = ?", [(at, user_id, t, day) for t in tickers])


# ---------- Telegram ----------

def get_telegram_link(conn: sqlite3.Connection, user_id: int | None = None,
                      chat_id: int | None = None) -> dict | None:
    col, val = ("user_id", user_id) if user_id is not None else ("chat_id", chat_id)
    row = conn.execute(f"SELECT * FROM telegram_links WHERE {col} = ?", (val,)).fetchone()
    return {**dict(row), "enabled": bool(row["enabled"])} if row else None


def set_telegram_code(conn: sqlite3.Connection, user_id: int, code_hash: str, expires_at: str) -> None:
    conn.execute("INSERT INTO telegram_codes (user_id, code_hash, expires_at) VALUES (?, ?, ?) "
                 "ON CONFLICT(user_id) DO UPDATE SET code_hash = excluded.code_hash, expires_at = excluded.expires_at",
                 (user_id, code_hash, expires_at))
    conn.commit()


def redeem_telegram_code(conn: sqlite3.Connection, code_hash: str, now: str, chat_id: int,
                         username: str) -> int | None:
    """Link `chat_id` to the code's owner and spend the code, atomically.
    Returns the user id, or None for an unknown or expired code. A chat
    linked to another account moves to this one."""
    with _immediate(conn):
        row = conn.execute("SELECT user_id FROM telegram_codes WHERE code_hash = ? AND expires_at > ?",
                           (code_hash, now)).fetchone()
        if row is None:
            return None
        uid = row[0]
        conn.execute("DELETE FROM telegram_codes WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM telegram_links WHERE chat_id = ? OR user_id = ?", (chat_id, uid))
        conn.execute("INSERT INTO telegram_links (user_id, chat_id, username, enabled, linked_at) "
                     "VALUES (?, ?, ?, 1, ?)", (uid, chat_id, username, now))
    return uid


def set_telegram_enabled(conn: sqlite3.Connection, user_id: int, enabled: bool) -> bool:
    cur = conn.execute("UPDATE telegram_links SET enabled = ? WHERE user_id = ?", (int(enabled), user_id))
    conn.commit()
    return cur.rowcount > 0


def delete_telegram_link(conn: sqlite3.Connection, user_id: int | None = None, chat_id: int | None = None) -> bool:
    col, val = ("user_id", user_id) if user_id is not None else ("chat_id", chat_id)
    cur = conn.execute(f"DELETE FROM telegram_links WHERE {col} = ?", (val,))
    conn.commit()
    return cur.rowcount > 0


def get_telegram_offset(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT update_offset FROM telegram_state WHERE id = 1").fetchone()
    return row[0] if row else 0


def set_telegram_offset(conn: sqlite3.Connection, offset: int) -> None:
    conn.execute("INSERT INTO telegram_state (id, update_offset) VALUES (1, ?) "
                 "ON CONFLICT(id) DO UPDATE SET update_offset = excluded.update_offset", (offset,))
    conn.commit()


def upsert_rule_signals(conn: sqlite3.Connection, rows: list[RuleSignal]) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO rule_signals (user_id, rule_id, ticker, day, kind, model_version, computed_at) "
        "VALUES (:user_id, :rule_id, :ticker, :day, :kind, :model_version, :computed_at)",
        [r.model_dump() for r in rows])
    conn.commit()


def get_rule_signals(conn: sqlite3.Connection, user_id: int, since: str) -> list[RuleSignal]:
    cur = conn.execute("SELECT * FROM rule_signals WHERE user_id = ? AND day >= ? ORDER BY day DESC, ticker",
                       (user_id, since))
    return [RuleSignal(**dict(r)) for r in cur.fetchall()]


# ---------- audit log ----------

def append_audit(conn: sqlite3.Connection, user_id: int, at: str, kind: str, detail: str,
                 hasher) -> AuditEntry:
    """Append one row to the user's chain. The previous hash is read and the
    row written in one transaction, so two writers can't fork the chain."""
    with _immediate(conn):
        row = conn.execute("SELECT hash FROM audit_log WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                           (user_id,)).fetchone()
        prev = row[0] if row else ""
        digest = hasher(prev, user_id, at, kind, detail)
        cur = conn.execute(
            "INSERT INTO audit_log (user_id, at, kind, detail, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, at, kind, detail, prev, digest))
    return AuditEntry(id=cur.lastrowid, user_id=user_id, at=at, kind=kind, detail=detail,
                      prev_hash=prev, hash=digest)


def get_audit(conn: sqlite3.Connection, user_id: int, limit: int | None = None,
              newest_first: bool = True) -> list[AuditEntry]:
    sql = f"SELECT * FROM audit_log WHERE user_id = ? ORDER BY id {'DESC' if newest_first else 'ASC'}"
    args: list = [user_id]
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    return [AuditEntry(**dict(r)) for r in conn.execute(sql, args)]


def get_audit_user_ids(conn: sqlite3.Connection) -> list[int]:
    return [r[0] for r in conn.execute("SELECT DISTINCT user_id FROM audit_log ORDER BY user_id")]


# ---------- demo trading bots ----------

def _bot_from_row(row) -> Bot:
    d = dict(row)
    d["params"] = json.loads(d.pop("params_json") or "{}")
    d["tickers"] = json.loads(d.pop("tickers_json") or "[]")
    d["state"] = json.loads(d.pop("state_json") or "{}")
    return Bot(**d)


def _transfer(conn, user_id: int, from_acct: str, to_acct: str, ccy: str, amount: float,
              source: str, at: str, ledger) -> None:
    """WITHDRAW from one account, DEPOSIT into another; caller holds _immediate."""
    for acct, side in ((from_acct, "WITHDRAW"), (to_acct, "DEPOSIT")):
        _record_transaction(conn, Transaction(
            user_id=user_id, account=acct, ticker=ccy, side=side, qty=amount, price=1.0,
            currency=ccy, executed_at=at, source=source), True, ledger)


def create_bot(conn: sqlite3.Connection, bot: Bot) -> Bot:
    """Insert the bot and move its allocation out of manual cash, atomically.
    Raises ledger.LedgerError (nothing written) when manual cash is short."""
    from app import ledger

    with _immediate(conn):
        cur = conn.execute(
            "INSERT INTO bots (user_id, name, strategy, params_json, tickers_json, watchlist_id, "
            "currency, allocated, status, state_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', '{}', ?)",
            (bot.user_id, bot.name, bot.strategy, json.dumps(bot.params), json.dumps(bot.tickers),
             bot.watchlist_id, bot.currency, bot.allocated, bot.created_at),
        )
        bot = bot.model_copy(update={"id": cur.lastrowid})
        _transfer(conn, bot.user_id, "manual", bot.account, bot.currency, bot.allocated,
                  "bot_allocate", bot.created_at, ledger)
        return bot


def get_bot(conn: sqlite3.Connection, bot_id: int) -> Bot | None:
    row = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
    return _bot_from_row(row) if row else None


def get_bots(conn: sqlite3.Connection, user_id: int | None = None,
             status: str | None = None) -> list[Bot]:
    sql, args = "SELECT * FROM bots WHERE 1 = 1", []
    if user_id is not None:
        sql += " AND user_id = ?"
        args.append(user_id)
    if status:
        sql += " AND status = ?"
        args.append(status)
    return [_bot_from_row(r) for r in conn.execute(sql + " ORDER BY id", args).fetchall()]


def set_bot_status(conn: sqlite3.Connection, bot_id: int, status: str, from_statuses: tuple) -> bool:
    """Conditional transition; False when the bot wasn't in `from_statuses`."""
    cur = conn.execute(
        f"UPDATE bots SET status = ? WHERE id = ? AND status IN ({','.join('?' * len(from_statuses))})",
        (status, bot_id, *from_statuses))
    conn.commit()
    return cur.rowcount == 1


def bot_trade(conn: sqlite3.Connection, bot: Bot, tx: Transaction, state: dict,
              event: BotEvent, order: Order | None = None) -> Transaction | None:
    """One engine fill: the order (written filled), the ledger row (positions +
    cash), the bot's new state and its event, in one transaction — and only
    while the bot is still active, so a stop that lands mid-run wins. Returns
    None when the bot had stopped."""
    from app import ledger

    with _immediate(conn):
        live = conn.execute("SELECT status FROM bots WHERE id = ?", (bot.id,)).fetchone()
        if live is None or live[0] != "active":
            return None
        if order is not None:
            tx = tx.model_copy(update={"order_id": _insert_order(conn, order).id})
        tx = _record_transaction(conn, tx, True, ledger)
        conn.execute("UPDATE bots SET state_json = ? WHERE id = ?", (json.dumps(state), bot.id))
        _insert_bot_event(conn, event)
        return tx


def set_bot_state(conn: sqlite3.Connection, bot_id: int, state: dict) -> None:
    conn.execute("UPDATE bots SET state_json = ? WHERE id = ? AND status = 'active'",
                 (json.dumps(state), bot_id))
    conn.commit()


def stop_bot(conn: sqlite3.Connection, bot: Bot, fills: list[tuple[str, float, float, str]],
             at: str) -> float | None:
    """Close every position at the given (ticker, qty, price, reason) fills,
    return all of the bot's cash to manual and mark it stopped — atomically.
    Returns the amount returned, or None when it was already stopped."""
    from app import ledger

    with _immediate(conn):
        cur = conn.execute(
            "UPDATE bots SET status = 'stopped', stopped_at = ? WHERE id = ? AND status != 'stopped'",
            (at, bot.id))
        if cur.rowcount != 1:
            return None
        for ticker, qty, price, reason in fills:
            _record_transaction(conn, Transaction(
                user_id=bot.user_id, account=bot.account, ticker=ticker, side="SELL", qty=qty,
                price=price, currency=bot.currency, executed_at=at, source="bot"), True, ledger)
            _insert_bot_event(conn, BotEvent(bot_id=bot.id, user_id=bot.user_id, at=at, kind="sell",
                                             ticker=ticker, qty=qty, price=price, reason=reason))
        row = conn.execute(
            "SELECT balance FROM cash_balances WHERE user_id = ? AND account = ? AND currency = ?",
            (bot.user_id, bot.account, bot.currency)).fetchone()
        cash = row[0] if row else 0.0
        if cash > ledger.EPS:
            _transfer(conn, bot.user_id, bot.account, "manual", bot.currency, cash,
                      "bot_return", at, ledger)
        conn.execute("UPDATE bots SET returned = ? WHERE id = ?", (round(cash, 2), bot.id))
        _insert_bot_event(conn, BotEvent(
            bot_id=bot.id, user_id=bot.user_id, at=at, kind="info",
            reason=f"Stopped: {cash:,.2f} {bot.currency} returned to your Portfolio cash"))
        return cash


def _insert_bot_event(conn, e: BotEvent) -> None:
    d = e.model_dump(exclude={"id"})
    conn.execute(f"INSERT INTO bot_events ({', '.join(d)}) VALUES ({', '.join('?' * len(d))})",
                 tuple(d.values()))


def add_bot_event(conn: sqlite3.Connection, event: BotEvent) -> None:
    _insert_bot_event(conn, event)
    conn.commit()


def get_bot_events(conn: sqlite3.Connection, bot_id: int, limit: int = 50) -> list[BotEvent]:
    cur = conn.execute(
        "SELECT * FROM bot_events WHERE bot_id = ? ORDER BY id DESC LIMIT ?", (bot_id, limit))
    return [BotEvent(**dict(r)) for r in cur.fetchall()]


def get_accounts_with_holdings(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """Every (user_id, account) owning a position or a non-zero cash balance."""
    cur = conn.execute(
        "SELECT user_id, account FROM portfolio WHERE user_id > 0 "
        "UNION SELECT user_id, account FROM cash_balances WHERE user_id > 0 AND balance != 0 "
        "ORDER BY 1, 2")
    return [(r[0], r[1]) for r in cur.fetchall()]


def get_all_portfolio_tickers(conn: sqlite3.Connection) -> list[str]:
    """Every ticker held by any user — the ingestion pipeline's universe."""
    cur = conn.execute("SELECT DISTINCT ticker FROM portfolio ORDER BY ticker")
    return [row[0] for row in cur.fetchall()]


# ---------- background analysis universe (portfolio ∪ watchlist ∪ signals) ----------

def get_signal_candidate_tickers(conn: sqlite3.Connection, limit: int = 20) -> list[str]:
    """Recent, distinct tickers flowing through the other signal sources — congress
    purchases, insider buys, analyst upgrades, social spikes and major federal
    contracts — most-recent first, capped at `limit`. Feeds the opportunities
    universe so the TA engine (and Suggestions) can surface new names, without a
    full-index scan."""
    rows: list[tuple[str, str]] = []
    queries = [
        "SELECT ticker, MAX(transaction_date) FROM congress_trades "
        "WHERE transaction_type='Purchase' AND ticker != '' GROUP BY ticker",
        "SELECT ticker, MAX(transaction_date) FROM insider_trades "
        "WHERE transaction_type='Buy' AND ticker != '' GROUP BY ticker",
        "SELECT ticker, fetched_at FROM analyst_signals "
        "WHERE recent_upgrades > 0 AND ticker != ''",
        "SELECT ticker, fetched_at FROM social_sentiment "
        "WHERE rank_change >= 5 AND ticker != ''",
        "SELECT ticker, MAX(start_date) FROM contracts "
        "WHERE ticker IS NOT NULL AND ticker != '' GROUP BY ticker",
    ]
    for sql in queries:
        for row in conn.execute(sql).fetchall():
            if row[0]:
                rows.append((row[0].upper(), row[1] or ""))
    best: dict[str, str] = {}
    for ticker, when in rows:
        if ticker not in best or when > best[ticker]:
            best[ticker] = when
    ordered = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
    return [t for t, _ in ordered[:limit]]


def get_analysis_universe(conn: sqlite3.Connection, candidate_limit: int = 20) -> list[str]:
    """Portfolio ∪ watchlist ∪ capped signal candidates — the set of tickers the
    background technical-analysis engine covers."""
    universe = set(get_all_portfolio_tickers(conn)) | set(get_all_watched_tickers(conn))
    universe |= set(get_signal_candidate_tickers(conn, candidate_limit))
    universe |= set(get_active_bot_tickers(conn))
    return sorted(universe)


def get_active_bot_tickers(conn: sqlite3.Connection) -> list[str]:
    """Tickers typed into bots that aren't stopped. They need price history
    even when nobody holds or watches them, or the bot can never trade them."""
    rows = conn.execute("SELECT tickers_json FROM bots WHERE status != 'stopped'").fetchall()
    return sorted({t for (raw,) in rows for t in json.loads(raw or "[]")})


def get_ohlc_fetched_at(conn: sqlite3.Connection, interval: str = "daily") -> dict[str, str]:
    """{ticker: fetched_at} for the stored OHLC series — drives stalest-first
    rotation so a large universe spreads its Yahoo fan-out across hourly runs."""
    cur = conn.execute(
        "SELECT ticker, fetched_at FROM ohlc_series WHERE interval = ?", (interval,))
    return {row[0]: row[1] for row in cur.fetchall()}


def prune_analyses(conn: sqlite3.Connection, keep: list[str]) -> int:
    """Drop stored analyses for tickers no longer in the universe. Returns the
    number of rows removed."""
    keep_up = {t.upper() for t in keep}
    existing = [row[0] for row in conn.execute("SELECT ticker FROM stock_analysis").fetchall()]
    drop = [t for t in existing if t.upper() not in keep_up]
    if drop:
        conn.executemany("DELETE FROM stock_analysis WHERE ticker = ?", [(t,) for t in drop])
        conn.commit()
    return len(drop)


def get_holding_categories(
    conn: sqlite3.Connection, user_id: int, account: str = "manual"
) -> dict[str, str]:
    """Manual category overrides for a user: {ticker: category}. Tickers with
    NULL category (auto) are omitted."""
    cur = conn.execute(
        "SELECT ticker, category FROM portfolio "
        "WHERE user_id = ? AND account = ? AND category IS NOT NULL",
        (user_id, account),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def set_holding_category(
    conn: sqlite3.Connection, user_id: int, ticker: str, category: str | None,
    account: str = "manual",
) -> None:
    """Set (or clear, when None) a holding's manual theme override."""
    conn.execute(
        "UPDATE portfolio SET category = ? WHERE user_id = ? AND account = ? AND ticker = ?",
        (category, user_id, account, ticker),
    )
    conn.commit()


def get_fundamentals_map(conn: sqlite3.Connection) -> dict[str, tuple[str | None, str | None]]:
    """{ticker: (sector, industry)} across all stored fundamentals — the input
    to theme classification."""
    cur = conn.execute("SELECT ticker, sector, industry FROM fundamentals")
    return {row[0]: (row[1], row[2]) for row in cur.fetchall()}


# ---------- notify profile (one row per user) ----------

def get_notify_profile(conn: sqlite3.Connection, user_id: int) -> NotifyProfile:
    cur = conn.execute("SELECT * FROM notify_profile WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if row is None:
        return NotifyProfile()
    d = dict(row)
    return NotifyProfile(
        email=d.get("email"),
        phone=d.get("phone"),
        email_enabled=bool(d.get("email_enabled")),
        sms_enabled=bool(d.get("sms_enabled")),
        account_size=d.get("account_size"),
        risk_pct=d.get("risk_pct") if d.get("risk_pct") is not None else 1.0,
        updated_at=d.get("updated_at") or "",
        base_currency=d.get("base_currency") or "USD",
    )


def upsert_notify_profile(conn: sqlite3.Connection, user_id: int,
                          profile: NotifyProfile) -> None:
    conn.execute(
        """
        INSERT INTO notify_profile (user_id, email, phone, email_enabled, sms_enabled, account_size, risk_pct, updated_at, base_currency)
        VALUES (:user_id, :email, :phone, :email_enabled, :sms_enabled, :account_size, :risk_pct, :updated_at, :base_currency)
        ON CONFLICT(user_id) DO UPDATE SET
            email=excluded.email, phone=excluded.phone,
            email_enabled=excluded.email_enabled, sms_enabled=excluded.sms_enabled,
            account_size=excluded.account_size, risk_pct=excluded.risk_pct,
            updated_at=excluded.updated_at, base_currency=excluded.base_currency
        """,
        {
            "user_id": user_id,
            "email": profile.email,
            "phone": profile.phone,
            "email_enabled": int(profile.email_enabled),
            "sms_enabled": int(profile.sms_enabled),
            "account_size": profile.account_size,
            "risk_pct": profile.risk_pct,
            "updated_at": profile.updated_at,
            "base_currency": profile.base_currency or "USD",
        },
    )
    conn.commit()


# ---------- FX watch list (per user) ----------

def get_fx_watch(conn: sqlite3.Connection, user_id: int,
                 seed: list[str] | tuple[str, ...] = ()) -> list[str]:
    """The user's FX pairs in display order.

    First read seeds `seed` (config.FX_PAIRS) and marks the account seeded, so
    a user who later removes every pair gets an empty list, not the defaults
    back. Accounts with no `users` row (tests) simply re-seed each time.
    """
    rows = conn.execute(
        "SELECT pair FROM fx_watch WHERE user_id = ? ORDER BY position, pair",
        (user_id,),
    ).fetchall()
    if rows:
        return [r[0] for r in rows]
    seeded = conn.execute(
        "SELECT fx_watch_seeded FROM users WHERE id = ?", (user_id,)).fetchone()
    if seeded is not None and seeded[0]:
        return []
    pairs = list(dict.fromkeys(seed))
    _write_fx_watch(conn, user_id, pairs)
    return pairs


def _write_fx_watch(conn: sqlite3.Connection, user_id: int, pairs: list[str]) -> None:
    conn.execute("DELETE FROM fx_watch WHERE user_id = ?", (user_id,))
    conn.executemany(
        "INSERT INTO fx_watch (user_id, pair, position) VALUES (?, ?, ?)",
        [(user_id, p, i) for i, p in enumerate(pairs)],
    )
    conn.execute("UPDATE users SET fx_watch_seeded = 1 WHERE id = ?", (user_id,))
    conn.commit()


def set_fx_watch(conn: sqlite3.Connection, user_id: int, pairs: list[str]) -> list[str]:
    """Replace the user's FX pairs (order = display order). Caller validates."""
    pairs = list(dict.fromkeys(pairs))
    _write_fx_watch(conn, user_id, pairs)
    return pairs


# ---------- app settings (single row) ----------

def get_app_settings(conn: sqlite3.Connection) -> AppSettings:
    cur = conn.execute("SELECT * FROM app_settings WHERE id = 1")
    row = cur.fetchone()
    if row is None:
        return AppSettings()
    d = dict(row)
    raw_accounts = d.get("x_accounts")
    x_accounts = [a.strip().lstrip("@") for a in raw_accounts.split(",")] if raw_accounts else []
    x_accounts = [a for a in x_accounts if a]
    return AppSettings(
        analysis_time=d.get("analysis_time") or "15:30",
        analysis_tz=d.get("analysis_tz") or "Asia/Jerusalem",
        quotes_refresh_seconds=d.get("quotes_refresh_seconds") or 30,
        x_accounts=x_accounts,
        updated_at=d.get("updated_at") or "",
    )


def upsert_app_settings(conn: sqlite3.Connection, settings: AppSettings) -> None:
    conn.execute(
        """
        INSERT INTO app_settings (id, analysis_time, analysis_tz, quotes_refresh_seconds, x_accounts, updated_at)
        VALUES (1, :analysis_time, :analysis_tz, :quotes_refresh_seconds, :x_accounts, :updated_at)
        ON CONFLICT(id) DO UPDATE SET
            analysis_time=excluded.analysis_time, analysis_tz=excluded.analysis_tz,
            quotes_refresh_seconds=excluded.quotes_refresh_seconds,
            x_accounts=excluded.x_accounts,
            updated_at=excluded.updated_at
        """,
        {
            "analysis_time": settings.analysis_time,
            "analysis_tz": settings.analysis_tz,
            "quotes_refresh_seconds": settings.quotes_refresh_seconds,
            "x_accounts": ",".join(settings.x_accounts),
            "updated_at": settings.updated_at,
        },
    )
    conn.commit()


# ---------- OHLC series + technical analysis ----------

def upsert_ohlc(conn: sqlite3.Connection, series: list[OHLCSeries]) -> None:
    conn.executemany(
        """
        INSERT INTO ohlc_series (ticker, interval, bars_json, fetched_at)
        VALUES (:ticker, :interval, :bars_json, :fetched_at)
        ON CONFLICT(ticker, interval) DO UPDATE SET
            bars_json=excluded.bars_json, fetched_at=excluded.fetched_at
        """,
        [s.model_dump() for s in series],
    )
    conn.commit()


def get_ohlc(conn: sqlite3.Connection, ticker: str, interval: str = "daily") -> list[OHLCBar]:
    cur = conn.execute(
        "SELECT bars_json FROM ohlc_series WHERE ticker = ? AND interval = ?",
        (ticker.upper(), interval),
    )
    row = cur.fetchone()
    if row is None:
        return []
    try:
        return [OHLCBar(**b) for b in json.loads(row["bars_json"])]
    except (ValueError, TypeError):
        return []


def upsert_analyses(conn: sqlite3.Connection, analyses: list[StockAnalysis]) -> None:
    conn.executemany(
        """
        INSERT INTO stock_analysis (ticker, computed_at, payload_json)
        VALUES (:ticker, :computed_at, :payload_json)
        ON CONFLICT(ticker) DO UPDATE SET
            computed_at=excluded.computed_at, payload_json=excluded.payload_json
        """,
        [{"ticker": a.ticker, "computed_at": a.computed_at, "payload_json": a.model_dump_json()}
         for a in analyses],
    )
    conn.commit()


def get_analysis(conn: sqlite3.Connection, ticker: str) -> StockAnalysis | None:
    cur = conn.execute("SELECT payload_json FROM stock_analysis WHERE ticker = ?", (ticker.upper(),))
    row = cur.fetchone()
    if row is None:
        return None
    try:
        return StockAnalysis(**json.loads(row["payload_json"]))
    except (ValueError, TypeError):
        return None


def get_all_analyses(conn: sqlite3.Connection) -> list[StockAnalysis]:
    cur = conn.execute("SELECT payload_json FROM stock_analysis")
    out: list[StockAnalysis] = []
    for row in cur.fetchall():
        try:
            out.append(StockAnalysis(**json.loads(row["payload_json"])))
        except (ValueError, TypeError):
            continue
    return out


# ---------- suggestion log (append-only) ----------

def insert_suggestion_log(conn: sqlite3.Connection, entries: list[SuggestionLogEntry]) -> None:
    conn.executemany(
        """
        INSERT INTO suggestion_log (created_at, for_date, channel, status)
        VALUES (:created_at, :for_date, :channel, :status)
        """,
        [e.model_dump() for e in entries],
    )
    conn.commit()


def get_recent_suggestions(conn: sqlite3.Connection, limit: int = 20) -> list[SuggestionLogEntry]:
    cur = conn.execute(
        "SELECT created_at, for_date, channel, status FROM suggestion_log ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    return [SuggestionLogEntry(**dict(row)) for row in cur.fetchall()]


# ---------- alerts ----------

def upsert_alerts(conn: sqlite3.Connection, records: list[Alert]) -> None:
    conn.executemany(
        """
        INSERT INTO alerts (dedup_key, created_at, ticker, type, severity, title, message, read,
                            pushed, user_id)
        VALUES (:dedup_key, :created_at, :ticker, :type, :severity, :title, :message, :read,
                :pushed, :user_id)
        ON CONFLICT(dedup_key) DO NOTHING
        """,
        [r.model_dump() for r in records],
    )
    conn.commit()


def _row_to_alert(d: dict) -> Alert:
    d["read"] = bool(d["read"])
    d["pushed"] = bool(d["pushed"])
    return Alert(**d)


def get_alerts(conn: sqlite3.Connection, user_id: int, limit: int = 100) -> list[Alert]:
    """Market alerts (user_id NULL) plus this user's own; read-state is per
    user via alert_reads."""
    cur = conn.execute(
        """
        SELECT a.dedup_key, a.created_at, a.ticker, a.type, a.severity, a.title,
               a.message, (ar.dedup_key IS NOT NULL) AS read, a.pushed, a.user_id
        FROM alerts a
        LEFT JOIN alert_reads ar
            ON ar.dedup_key = a.dedup_key AND ar.user_id = ?
        WHERE a.user_id IS NULL OR a.user_id = ?
        ORDER BY a.id DESC LIMIT ?
        """,
        (user_id, user_id, limit),
    )
    return [_row_to_alert(dict(row)) for row in cur.fetchall()]


def get_alerts_for(
    conn: sqlite3.Connection, user_id: int, ticker: str, limit: int = 20
) -> list[Alert]:
    """This ticker's alerts, newest first. Same read-state join as get_alerts."""
    cur = conn.execute(
        """
        SELECT a.dedup_key, a.created_at, a.ticker, a.type, a.severity, a.title,
               a.message, (ar.dedup_key IS NOT NULL) AS read, a.pushed, a.user_id
        FROM alerts a
        LEFT JOIN alert_reads ar
            ON ar.dedup_key = a.dedup_key AND ar.user_id = ?
        WHERE a.ticker = ? AND (a.user_id IS NULL OR a.user_id = ?)
        -- created_at, not id. Insertion order tracks time for alerts the
        -- detector fires live, but it is not the same thing, and this list is
        -- labelled "newest first" in the UI. id breaks ties deterministically.
        ORDER BY a.created_at DESC, a.id DESC LIMIT ?
        """,
        (user_id, ticker, user_id, limit),
    )
    return [_row_to_alert(dict(row)) for row in cur.fetchall()]


# ---------- earnings ----------
def upsert_earnings(conn: sqlite3.Connection, records: list[EarningsEvent]) -> None:
    conn.executemany(
        """
        INSERT INTO earnings_events
            (ticker, event_date, is_estimate, timing, eps_estimate, eps_actual,
             surprise_pct, revenue_estimate, quarter, source, fetched_at)
        VALUES (:ticker, :event_date, :is_estimate, :timing, :eps_estimate, :eps_actual,
                :surprise_pct, :revenue_estimate, :quarter, :source, :fetched_at)
        ON CONFLICT(ticker, event_date) DO UPDATE SET
            is_estimate=excluded.is_estimate,
            timing=excluded.timing,
            eps_estimate=excluded.eps_estimate,
            -- Never overwrite a reported figure with a NULL from a later fetch
            -- that only carried the forward calendar.
            eps_actual=COALESCE(excluded.eps_actual, earnings_events.eps_actual),
            surprise_pct=COALESCE(excluded.surprise_pct, earnings_events.surprise_pct),
            revenue_estimate=excluded.revenue_estimate,
            quarter=CASE WHEN excluded.quarter != '' THEN excluded.quarter ELSE earnings_events.quarter END,
            source=excluded.source,
            fetched_at=excluded.fetched_at
        """,
        [{**r.model_dump(), "is_estimate": int(r.is_estimate)} for r in records],
    )
    conn.commit()


def _row_to_earnings(row) -> EarningsEvent:
    d = dict(row)
    d["is_estimate"] = bool(d["is_estimate"])
    return EarningsEvent(**d)


def get_earnings(
    conn: sqlite3.Connection,
    date_from: str | None = None,
    date_to: str | None = None,
    tickers: list[str] | None = None,
) -> list[EarningsEvent]:
    sql = "SELECT * FROM earnings_events WHERE 1=1"
    params: list = []
    if date_from:
        sql += " AND event_date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND event_date <= ?"
        params.append(date_to)
    if tickers:
        sql += f" AND ticker IN ({','.join('?' * len(tickers))})"
        params.extend(tickers)
    sql += " ORDER BY event_date ASC, ticker ASC"
    return [_row_to_earnings(r) for r in conn.execute(sql, params).fetchall()]


def get_earnings_for(conn: sqlite3.Connection, ticker: str) -> list[EarningsEvent]:
    cur = conn.execute(
        "SELECT * FROM earnings_events WHERE ticker = ? ORDER BY event_date DESC", (ticker,))
    return [_row_to_earnings(r) for r in cur.fetchall()]


def count_unread_alerts(conn: sqlite3.Connection, user_id: int) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) FROM alerts a
        WHERE (a.user_id IS NULL OR a.user_id = ?)
        AND NOT EXISTS (
            SELECT 1 FROM alert_reads ar
            WHERE ar.user_id = ? AND ar.dedup_key = a.dedup_key
        )
        """,
        (user_id, user_id),
    ).fetchone()[0]


def mark_alerts_read(conn: sqlite3.Connection, user_id: int,
                     keys: list[str] | None = None, read_at: str = "") -> None:
    if keys:
        conn.executemany(
            "INSERT OR IGNORE INTO alert_reads (user_id, dedup_key, read_at) VALUES (?, ?, ?)",
            [(user_id, k, read_at) for k in keys],
        )
    else:
        conn.execute(
            "INSERT OR IGNORE INTO alert_reads (user_id, dedup_key, read_at) "
            "SELECT ?, dedup_key, ? FROM alerts WHERE user_id IS NULL OR user_id = ?",
            (user_id, read_at, user_id),
        )
    conn.commit()


def alert_exists(conn: sqlite3.Connection, dedup_key: str) -> bool:
    return conn.execute("SELECT 1 FROM alerts WHERE dedup_key = ? LIMIT 1", (dedup_key,)).fetchone() is not None


# ---------- alert_state (transition memory) ----------

def get_alert_state(conn: sqlite3.Connection, ticker: str) -> dict | None:
    row = conn.execute("SELECT * FROM alert_state WHERE ticker = ?", (ticker,)).fetchone()
    return dict(row) if row else None


def upsert_alert_state(
    conn: sqlite3.Connection, ticker: str, score: int | None,
    golden_cross: bool | None, insider_cluster_buy: bool | None, updated_at: str,
    *, ta_recommendation: str | None = None, ta_breakout_status: str | None = None,
    ta_ma_state: str | None = None, ta_conviction: int | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO alert_state
            (ticker, score, golden_cross, insider_cluster_buy, updated_at,
             ta_recommendation, ta_breakout_status, ta_ma_state, ta_conviction)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            score=excluded.score, golden_cross=excluded.golden_cross,
            insider_cluster_buy=excluded.insider_cluster_buy, updated_at=excluded.updated_at,
            ta_recommendation=excluded.ta_recommendation,
            ta_breakout_status=excluded.ta_breakout_status,
            ta_ma_state=excluded.ta_ma_state, ta_conviction=excluded.ta_conviction
        """,
        (ticker, score, int(golden_cross) if golden_cross is not None else None,
         int(insider_cluster_buy) if insider_cluster_buy is not None else None, updated_at,
         ta_recommendation, ta_breakout_status, ta_ma_state, ta_conviction),
    )
    conn.commit()


# ---------- users, sessions & recovery codes ----------
def create_user(conn: sqlite3.Connection, email: str, password_hash: str,
                created_at: str, is_admin: bool = False, approved: bool = True) -> User:
    cur = conn.execute(
        "INSERT INTO users (email, password_hash, created_at, is_admin, approved_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (email, password_hash, created_at, int(is_admin), created_at if approved else None),
    )
    conn.commit()
    return get_user(conn, cur.lastrowid)


def get_pending_users(conn: sqlite3.Connection) -> list[User]:
    cur = conn.execute("SELECT * FROM users WHERE approved_at IS NULL ORDER BY id ASC")
    return [User(**dict(row)) for row in cur.fetchall()]


def approve_user(conn: sqlite3.Connection, user_id: int, at: str) -> bool:
    """Approve a pending account. False when it is unknown or already approved."""
    cur = conn.execute(
        "UPDATE users SET approved_at = ? WHERE id = ? AND approved_at IS NULL", (at, user_id))
    conn.commit()
    return cur.rowcount == 1


def get_user(conn: sqlite3.Connection, user_id: int) -> User | None:
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return User(**dict(row)) if row else None


def get_user_by_email(conn: sqlite3.Connection, email: str) -> User | None:
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return User(**dict(row)) if row else None


def count_users(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


# Every table that carries a user_id. Only sessions, recovery_codes and
# oauth_identities declare ON DELETE CASCADE, so deleting the users row alone
# would strand the rest under an id SQLite may reissue to the next account.
# Sweep all of them explicitly and keep this list in step with the schema.
_PER_USER_TABLES = (
    "watchlist",
    "watchlists",
    "portfolio",
    "transactions",
    "cash_balances",
    "equity_snapshots",
    "bots",
    "bot_events",
    "orders",
    "audit_log",
    "saved_rules",
    "rule_variants",
    "rule_signals",
    "scanner_prefs",
    "scanner_alert_log",
    "telegram_links",
    "telegram_codes",
    "target_weights",
    "position_rules",
    "risk_settings",
    "signal_mute_overrides",
    "alerts",
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


def delete_user(conn: sqlite3.Connection, user_id: int) -> bool:
    """Delete an account and every row it owns. Returns False if unknown.

    Shared market data (ticker-keyed tables, `stock_analysis`, market alerts
    with no user_id) is deliberately untouched — it belongs to the instance,
    not to the account.
    """
    if get_user(conn, user_id) is None:
        return False
    try:
        for table in _PER_USER_TABLES:
            if _has_column(conn, table, "user_id"):
                conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return True


def set_onboarded(conn: sqlite3.Connection, user_id: int) -> None:
    """Mark that the account has seen the guided tour (persists across devices)."""
    conn.execute("UPDATE users SET onboarded = 1 WHERE id = ?", (user_id,))
    conn.commit()


def get_oauth_identity(
    conn: sqlite3.Connection, provider: str, provider_user_id: str
) -> OAuthIdentity | None:
    row = conn.execute(
        "SELECT * FROM oauth_identities WHERE provider = ? AND provider_user_id = ?",
        (provider, provider_user_id),
    ).fetchone()
    return OAuthIdentity(**dict(row)) if row else None


def create_oauth_identity(conn: sqlite3.Connection, identity: OAuthIdentity) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO oauth_identities
            (provider, provider_user_id, user_id, email, created_at)
        VALUES (:provider, :provider_user_id, :user_id, :email, :created_at)
        """,
        identity.model_dump(),
    )
    conn.commit()


def get_users(conn: sqlite3.Connection) -> list[User]:
    cur = conn.execute("SELECT * FROM users ORDER BY id ASC")
    return [User(**dict(row)) for row in cur.fetchall()]


def set_totp_secret(conn: sqlite3.Connection, user_id: int, secret: str) -> None:
    conn.execute("UPDATE users SET totp_secret = ? WHERE id = ?", (secret, user_id))
    conn.commit()


def enable_totp(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("UPDATE users SET totp_enabled = 1 WHERE id = ?", (user_id,))
    conn.commit()


def create_session(conn: sqlite3.Connection, session: AuthSession) -> None:
    conn.execute(
        """
        INSERT INTO sessions (token_hash, user_id, state, created_at, expires_at, last_seen_at)
        VALUES (:token_hash, :user_id, :state, :created_at, :expires_at, :last_seen_at)
        """,
        session.model_dump(),
    )
    conn.commit()


def get_session(conn: sqlite3.Connection, token_hash: str) -> AuthSession | None:
    row = conn.execute(
        "SELECT * FROM sessions WHERE token_hash = ?", (token_hash,)
    ).fetchone()
    return AuthSession(**dict(row)) if row else None


def touch_session(conn: sqlite3.Connection, token_hash: str, last_seen_at: str) -> None:
    conn.execute(
        "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
        (last_seen_at, token_hash),
    )
    conn.commit()


def delete_session(conn: sqlite3.Connection, token_hash: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
    conn.commit()


def purge_expired_sessions(conn: sqlite3.Connection, now_iso: str) -> None:
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso,))
    conn.commit()


def replace_recovery_codes(conn: sqlite3.Connection, user_id: int,
                           code_hashes: list[str]) -> None:
    conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))
    conn.executemany(
        "INSERT INTO recovery_codes (user_id, code_hash) VALUES (?, ?)",
        [(user_id, h) for h in code_hashes],
    )
    conn.commit()


def consume_recovery_code(conn: sqlite3.Connection, user_id: int,
                          code_hash: str, used_at: str) -> bool:
    """Mark one unused code as used. Returns False if no such unused code."""
    cur = conn.execute(
        "UPDATE recovery_codes SET used_at = ? "
        "WHERE user_id = ? AND code_hash = ? AND used_at IS NULL",
        (used_at, user_id, code_hash),
    )
    conn.commit()
    return cur.rowcount == 1


def count_unused_recovery_codes(conn: sqlite3.Connection, user_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM recovery_codes WHERE user_id = ? AND used_at IS NULL",
        (user_id,),
    ).fetchone()[0]


def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})"))


def claim_legacy_rows(conn: sqlite3.Connection, user_id: int) -> None:
    """Assign pre-auth single-user data (user_id=0 sentinel) to `user_id`.

    Called when the first account registers, so an existing single-user DB
    migrates to its owner. No-op on tables that predate the user_id column.
    """
    for table in ("watchlist", "watchlists", "portfolio", "transactions", "cash_balances",
                  "notify_profile"):
        if _has_column(conn, table, "user_id"):
            conn.execute(f"UPDATE {table} SET user_id = ? WHERE user_id = 0", (user_id,))
    conn.commit()


# --- Thread safety -------------------------------------------------------
# One shared SQLite connection (check_same_thread=False) is used by FastAPI's
# threadpool AND the APScheduler jobs. sqlite3 forbids *concurrent* use of a
# single connection, which otherwise corrupts cursor results (all-NULL rows,
# InterfaceError, "tuple index out of range"). Serialize every public db access
# on a re-entrant lock carried by the connection object (see connect()), so the
# lock is shared even if this module is imported under two names. Holding the
# lock for the whole function keeps each execute+fetch atomic; RLock lets db
# functions call each other on the same thread without deadlock.
def _conn_lock(args, kwargs):
    conn = args[0] if args else kwargs.get("conn")
    return getattr(conn, "_app_lock", None)


def _synchronized(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        lock = _conn_lock(args, kwargs)
        if lock is None:
            return fn(*args, **kwargs)
        with lock:
            return fn(*args, **kwargs)
    return wrapper


_module = sys.modules[__name__]
for _name, _obj in list(vars(_module).items()):
    if (
        inspect.isfunction(_obj)
        and _obj.__module__ == __name__
        and not _name.startswith("_")
    ):
        setattr(_module, _name, _synchronized(_obj))
