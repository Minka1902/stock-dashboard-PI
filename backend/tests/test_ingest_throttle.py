"""Run outcomes and the two clocks, now that the schedule is the only gate.

History: `run_source` used to carry its own min_interval/retry_interval gate
and recorded a `skipped` run whenever it declined to fetch. That made "the
scheduler fired but nothing happened" a normal, silent outcome. The per-source
schedule (app/schedules.py + one APScheduler job per source) is now the only
thing that decides when a source runs, so `run_source` always runs and every
outcome is one of: ok, error (full traceback kept per run), or deferred (the
source itself asked to be retried later, with a reason and a next attempt).
"""
from datetime import datetime, timedelta, timezone

from app import db, ingest
from app.models import ContractRecord


def _records():
    return [
        ContractRecord(
            external_id="x1", award_id="A1", recipient_name="Acme", amount=1.0,
            awarding_agency="DoD", start_date="2026-01-01", description="thing",
        )
    ]


def _ago(**kwargs):
    return (datetime.now(timezone.utc) - timedelta(**kwargs)).isoformat(timespec="seconds")


def _status(conn, name="usaspending"):
    return {s.source: s for s in db.get_source_statuses(conn)}[name]


def test_success_sets_last_success_at(conn):
    ingest.run_source(conn, "usaspending", _records, db.upsert_contracts)
    row = _status(conn)
    assert row.last_success_at is not None
    assert row.last_success_at == row.last_refreshed_at
    assert row.last_duration_ms is not None


def test_failure_does_not_advance_last_success_at(conn):
    def boom():
        raise RuntimeError("upstream 403")

    ingest.run_source(conn, "usaspending", _records, db.upsert_contracts)
    good = _status(conn).last_success_at
    assert good is not None

    ingest.run_source(conn, "usaspending", boom, db.upsert_contracts)
    row = _status(conn)
    assert row.status.startswith("error")
    # The attempt clock moved; the success clock did not.
    assert row.last_success_at == good
    assert row.last_refreshed_at >= good


def test_run_source_never_gates_and_never_records_a_skip(conn):
    """Back-to-back runs both fetch: gating is the scheduler's job now."""
    calls = 0

    def counting():
        nonlocal calls
        calls += 1
        return _records()

    ingest.run_source(conn, "usaspending", counting, db.upsert_contracts)
    ingest.run_source(conn, "usaspending", counting, db.upsert_contracts)
    assert calls == 2
    outcomes = [r.outcome for r in db.get_source_runs(conn, "usaspending")]
    assert outcomes == ["ok", "ok"]
    assert "skipped" not in outcomes


def test_run_source_returns_the_outcome_and_run_id(conn):
    result = ingest.run_source(conn, "usaspending", _records, db.upsert_contracts)
    assert result.outcome == "ok"
    assert result.record_count == 1
    assert db.get_source_runs(conn, "usaspending")[0].id == result.run_id


def test_error_keeps_the_full_traceback_on_the_run_row(conn):
    def boom():
        raise RuntimeError("upstream 403 with a long body")

    result = ingest.run_source(conn, "usaspending", boom, db.upsert_contracts)
    assert result.outcome == "error"
    run = db.get_source_runs(conn, "usaspending")[0]
    assert run.detail.startswith("error: RuntimeError")      # the brief line
    assert "Traceback" in run.error_detail                   # the whole story
    assert "upstream 403 with a long body" in run.error_detail


def test_deferred_is_its_own_outcome_with_a_reason_and_next_attempt(conn):
    ingest.run_source(conn, "usaspending", _records, db.upsert_contracts)
    good = _status(conn)

    def cooling():
        raise ingest.SourceDeferred("rate limited (HTTP 429)", retry_after_seconds=600)

    result = ingest.run_source(conn, "usaspending", cooling, db.upsert_contracts)
    assert result.outcome == "deferred"
    assert result.next_attempt_at is not None

    run = db.get_source_runs(conn, "usaspending")[0]
    assert run.outcome == "deferred"
    assert "429" in run.detail
    assert run.next_attempt_at == result.next_attempt_at

    row = _status(conn)
    assert row.status.startswith("deferred:")
    assert row.next_attempt_at == result.next_attempt_at
    # Deferral is not a failure of the data we already have.
    assert row.record_count == good.record_count
    assert row.last_success_at == good.last_success_at
    assert row.error_detail is None


def test_success_clears_a_pending_next_attempt(conn):
    def cooling():
        raise ingest.SourceDeferred("busy", retry_after_seconds=60)

    ingest.run_source(conn, "usaspending", cooling, db.upsert_contracts)
    assert _status(conn).next_attempt_at is not None
    ingest.run_source(conn, "usaspending", _records, db.upsert_contracts)
    assert _status(conn).next_attempt_at is None


def test_scheduler_can_annotate_when_a_failed_run_tries_again(conn):
    def boom():
        raise RuntimeError("nope")

    result = ingest.run_source(conn, "usaspending", boom, db.upsert_contracts)
    at = "2026-09-26T12:00:00+00:00"
    db.set_source_next_attempt(conn, "usaspending", result.run_id, at)
    assert db.get_source_runs(conn, "usaspending")[0].next_attempt_at == at
    assert _status(conn).next_attempt_at == at


def test_degraded_provenance_is_not_treated_as_success(conn):
    """A FetchResult warning stores real data but must keep retrying."""
    def degraded():
        return ingest.FetchResult(_records(), warning="unofficial mirror")

    ingest.run_source(conn, "usaspending", degraded, db.upsert_contracts)
    row = _status(conn)
    assert row.status.startswith("error")
    assert row.record_count == 1          # the real records were still stored
    assert row.last_success_at is None    # but it does not satisfy the cadence


def test_run_stats_aggregate_outcomes(conn):
    def boom():
        raise RuntimeError("nope")

    def cooling():
        raise ingest.SourceDeferred("busy", retry_after_seconds=60)

    ingest.run_source(conn, "usaspending", _records, db.upsert_contracts)
    ingest.run_source(conn, "usaspending", boom, db.upsert_contracts)
    ingest.run_source(conn, "usaspending", cooling, db.upsert_contracts)

    stats = db.get_source_run_stats(conn)["usaspending"]
    assert stats["runs_total"] == 3
    assert stats["runs_ok"] == 1
    assert stats["runs_error"] == 1
    assert stats["runs_deferred"] == 1


def test_prune_history_trims_old_rows(conn):
    db.record_source_run(conn, "usaspending", _ago(days=90), _ago(days=90), "ok", 5, 1)
    db.record_source_run(conn, "usaspending", _ago(days=1), _ago(days=1), "ok", 5, 1)
    db.record_job_run(conn, "daily_analysis", "executed", _ago(days=200))
    db.record_job_run(conn, "daily_analysis", "executed", _ago(days=1))

    deleted = db.prune_history(conn, source_run_days=30, job_run_days=90, boom_history_days=400)
    assert deleted["source_runs"] == 1
    assert deleted["job_runs"] == 1
    assert len(db.get_source_runs(conn)) == 1
    assert len(db.get_job_runs(conn)) == 1


def test_gdelt_cooldown_is_a_deferral_not_an_error():
    from app.sources import gdelt

    assert issubclass(gdelt.GdeltCoolingDown, ingest.SourceDeferred)
    exc = gdelt.GdeltCoolingDown("GDELT rate-limited", retry_after_seconds=42)
    assert exc.retry_after_seconds == 42
    assert "rate-limited" in exc.reason
