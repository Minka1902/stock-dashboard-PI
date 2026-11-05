"""One-time cleanup of TASE rows stored before ILA (agorot) normalization.

Before multi-currency support, every stored price for a ".TA" listing was in
agorot. Those rows are dropped once (the scheduler re-fetches them in shekels)
so a stored series never mixes units, and legacy TASE holdings — which the
new column would otherwise default to USD — are marked ILS.
"""
from app import db


def _seed_legacy(conn):
    conn.execute("INSERT INTO ohlc_series VALUES ('TEVA.TA','daily','[]','t')")
    conn.execute("INSERT INTO ohlc_series VALUES ('AAPL','daily','[]','t')")
    conn.execute("INSERT INTO stock_analysis VALUES ('TEVA.TA','t','{}')")
    conn.execute("INSERT INTO technical_signals (ticker, fetched_at, price) VALUES ('LUMI.TA','t',3100)")
    conn.execute("INSERT INTO technical_signals (ticker, fetched_at, price) VALUES ('MSFT','t',400)")
    conn.execute("INSERT INTO seasonality (ticker, computed_at, as_of) VALUES ('TEVA.TA','t','t')")
    conn.execute(
        "INSERT INTO portfolio (user_id, ticker, shares, avg_cost, added_at) "
        "VALUES (1,'TEVA.TA',10,120.5,'t')")
    conn.execute(
        "INSERT INTO portfolio (user_id, ticker, shares, avg_cost, added_at) "
        "VALUES (1,'AAPL',1,100,'t')")
    conn.execute("DELETE FROM data_migrations")
    conn.commit()


def _count(conn, table, ticker):
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE ticker = ?", (ticker,)).fetchone()[0]


def test_legacy_tase_price_rows_are_purged_once(conn):
    _seed_legacy(conn)
    db.init_schema(conn)
    assert _count(conn, "ohlc_series", "TEVA.TA") == 0
    assert _count(conn, "stock_analysis", "TEVA.TA") == 0
    assert _count(conn, "technical_signals", "LUMI.TA") == 0
    assert _count(conn, "seasonality", "TEVA.TA") == 0
    # US rows untouched
    assert _count(conn, "ohlc_series", "AAPL") == 1
    assert _count(conn, "technical_signals", "MSFT") == 1


def test_legacy_tase_holdings_are_marked_shekels(conn):
    _seed_legacy(conn)
    db.init_schema(conn)
    held = {h.ticker: h for h in db.get_portfolio(conn, 1)}
    assert held["TEVA.TA"].currency == "ILS"
    assert held["AAPL"].currency == "USD"


def test_purge_runs_only_once(conn):
    _seed_legacy(conn)
    db.init_schema(conn)
    # Fresh (already normalized) rows written after the migration survive
    # every later startup.
    conn.execute("INSERT INTO ohlc_series VALUES ('TEVA.TA','daily','[]','t2')")
    conn.commit()
    db.init_schema(conn)
    assert _count(conn, "ohlc_series", "TEVA.TA") == 1
