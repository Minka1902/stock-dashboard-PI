from app import db, ingest
from app.models import ContractRecord


def _records():
    return [
        ContractRecord(
            external_id="A", award_id="AWD-A", recipient_name="Acme",
            amount=10.0, awarding_agency="DoD", start_date="2026-06-01",
        )
    ]


def test_run_source_stores_records_and_stamps_status(conn):
    def fake_fetch():
        return _records()

    ingest.run_source(conn, "usaspending", fake_fetch, db.upsert_contracts)

    assert len(db.get_contracts(conn)) == 1
    statuses = db.get_source_statuses(conn)
    assert statuses[0].source == "usaspending"
    assert statuses[0].status == "ok"
    assert statuses[0].record_count == 1
    assert statuses[0].last_refreshed_at is not None


def test_run_source_records_error_status(conn):
    def boom():
        raise RuntimeError("network down")

    ingest.run_source(conn, "usaspending", boom, db.upsert_contracts)

    statuses = db.get_source_statuses(conn)
    assert statuses[0].status.startswith("error:")
    assert statuses[0].record_count == 0
    # Failure still stamps a timestamp so the UI shows it tried.
    assert statuses[0].last_refreshed_at is not None


def test_run_source_always_runs_when_called(conn):
    """No hidden gate: the per-source schedule decides when to call run_source,
    and a call always fetches (there is no `skipped` outcome any more)."""
    call_count = 0

    def counting_fetch():
        nonlocal call_count
        call_count += 1
        return _records()

    ingest.run_source(conn, "usaspending", counting_fetch, db.upsert_contracts)
    ingest.run_source(conn, "usaspending", counting_fetch, db.upsert_contracts)
    assert call_count == 2


def test_run_source_records_fetch_result_note(conn):
    from app.ingest import FetchResult

    def fallback_fetch():
        return FetchResult(_records(), note="fallback: workbook")

    ingest.run_source(conn, "usaspending", fallback_fetch, db.upsert_contracts)
    status = {s.source: s for s in db.get_source_statuses(conn)}["usaspending"]
    assert status.status == "ok (fallback: workbook)"
    assert status.record_count == 1


def test_run_source_stores_error_detail_with_traceback(conn):
    def boom():
        raise RuntimeError("network down")

    ingest.run_source(conn, "usaspending", boom, db.upsert_contracts)
    status = {s.source: s for s in db.get_source_statuses(conn)}["usaspending"]
    assert status.status.startswith("error:")
    assert status.error_detail is not None
    assert "RuntimeError: network down" in status.error_detail
    assert "Traceback" in status.error_detail


def test_run_source_success_clears_stale_error_detail(conn):
    def boom():
        raise RuntimeError("boom")

    ingest.run_source(conn, "usaspending", boom, db.upsert_contracts)
    assert {s.source: s for s in db.get_source_statuses(conn)}["usaspending"].error_detail

    ingest.run_source(conn, "usaspending", _records, db.upsert_contracts)
    status = {s.source: s for s in db.get_source_statuses(conn)}["usaspending"]
    assert status.status == "ok"
    assert status.error_detail is None


def test_run_source_warning_flags_error_but_keeps_data(conn):
    from app.ingest import FetchResult

    def degraded_fetch():
        return FetchResult(_records(), warning="unofficial mirror — set STOCKS_X_BEARER")

    ingest.run_source(conn, "usaspending", degraded_fetch, db.upsert_contracts)
    status = {s.source: s for s in db.get_source_statuses(conn)}["usaspending"]
    assert status.status.startswith("error:")
    assert "unofficial mirror" in status.status
    assert status.record_count == 1  # data was still stored
    assert len(db.get_contracts(conn)) == 1
