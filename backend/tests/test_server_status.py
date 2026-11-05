"""The admin-only /api/server/* introspection surface."""
import importlib

import pytest
from fastapi.testclient import TestClient

from app import db, quotes as quotes_module
from tests.conftest import authenticate


@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "server.db"))
    from app import config, main as main_module
    importlib.reload(config)
    importlib.reload(main_module)
    quotes_module._cache.clear()
    yield main_module
    quotes_module._cache.clear()


@pytest.fixture
def client(app_module):
    with TestClient(app_module.app) as c:
        authenticate(c)  # first account registered becomes admin
        yield c


def test_overview_reports_uptime_scheduler_and_db(client):
    body = client.get("/api/server/overview").json()
    assert body["uptime_seconds"] >= 0
    assert body["scheduler"]["running"] is True
    ids = {j["id"] for j in body["scheduler"]["jobs"]}
    assert ids >= {"daily_analysis", "src:vix", "src:derived"}
    assert "refresh_all" not in ids
    # Jobs waiting on the single refresh thread are visible, not hidden.
    assert isinstance(body["queued"], list)
    assert body["db"]["size_bytes"] > 0
    assert "version" in body and "python" in body
    assert isinstance(body["running_sources"], dict)


def test_process_stats_degrade_honestly_without_psutil(app_module, monkeypatch):
    """Zeros would be indistinguishable from an idle machine, so an absent
    dependency has to say so."""
    monkeypatch.setattr(app_module, "psutil", None)
    stats = app_module._process_stats()
    assert stats["available"] is False
    assert "psutil" in stats["reason"]
    # ...and no fabricated numbers alongside it.
    assert "rss_bytes" not in stats and "cpu_percent" not in stats


def test_process_stats_present_when_psutil_is(app_module):
    stats = app_module._process_stats()
    if stats["available"]:
        assert stats["rss_bytes"] > 0
        assert stats["num_threads"] >= 1


def test_server_routes_are_admin_only(app_module):
    with TestClient(app_module.app) as admin:
        authenticate(admin)
        assert admin.get("/api/server/overview").status_code == 200

        other = TestClient(app_module.app)
        authenticate(other, email="notadmin@example.com")
        for path in ("/api/server/overview", "/api/server/sources", "/api/server/events"):
            assert other.get(path).status_code == 403, path


def test_sources_expose_both_clocks_and_the_next_run(client, app_module):
    now = "2026-08-01T10:00:00+00:00"
    db.update_source_status(
        app_module.conn, "margin_debt", now, "error: RuntimeError: 401", 0, success=False)

    rows = {r["source"]: r for r in client.get("/api/server/sources").json()}
    md = rows["margin_debt"]
    assert md["last_refreshed_at"] == now
    assert md["last_success_at"] is None      # never succeeded
    assert md["schedule"]["interval_seconds"] is not None
    assert md["next_run_at"] is not None


def test_sources_list_every_registered_source_even_if_never_run(client, app_module):
    """A source that has never run used to be missing from the page entirely."""
    rows = {r["source"]: r for r in client.get("/api/server/sources").json()}
    assert set(rows) == set(app_module.SOURCES)
    vix = rows["vix"]
    assert vix["never_run"] is True
    assert vix["status"] is None
    assert vix["next_run_at"] is not None       # "never run: next at ..."
    # boom_score/alerts run as the derived step and say so.
    assert rows["boom_score"]["schedule"]["source"] == "derived"


def test_events_interleave_source_runs_and_job_runs(client, app_module):
    c = app_module.conn
    db.record_source_run(c, "gdelt", "2026-08-01T10:00:00+00:00",
                         "2026-08-01T10:00:02+00:00", "error", 2000, 0, "429")
    db.record_source_run(c, "vix", "2026-08-01T10:01:00+00:00",
                         "2026-08-01T10:01:01+00:00", "ok", 1000, 126)
    db.record_job_run(c, "daily_analysis", "missed", "2026-08-01T10:02:00+00:00")

    events = client.get("/api/server/events?limit=10").json()
    assert [e["at"] for e in events] == sorted((e["at"] for e in events), reverse=True)
    kinds = {e["kind"] for e in events}
    assert kinds == {"source", "job"}
    missed = next(e for e in events if e["outcome"] == "missed")
    assert missed["kind"] == "job" and missed["id"] == "daily_analysis"
    # Stable keys for the UI, never the array index.
    assert len({e["key"] for e in events}) == len(events)


def test_events_limit_is_bounded(client):
    assert len(client.get("/api/server/events?limit=99999").json()) <= 300


def test_events_filter_by_kind_and_id(client, app_module):
    """'Show similar' filters on the server, not over the last page client-side."""
    c = app_module.conn
    for i in range(5):
        db.record_source_run(c, "gdelt", f"2026-08-01T10:0{i}:00+00:00",
                             f"2026-08-01T10:0{i}:01+00:00", "ok", 1000, 3)
        db.record_source_run(c, "vix", f"2026-08-01T11:0{i}:00+00:00",
                             f"2026-08-01T11:0{i}:01+00:00", "ok", 1000, 3)
    db.record_job_run(c, "daily_analysis", "executed", "2026-08-01T12:00:00+00:00", 5000)
    db.record_job_run(c, "prune_history", "executed", "2026-08-01T12:01:00+00:00", 10)

    only_gdelt = client.get("/api/server/events?kind=source&id=gdelt&limit=3").json()
    assert len(only_gdelt) == 3
    assert {(e["kind"], e["id"]) for e in only_gdelt} == {("source", "gdelt")}

    only_job = client.get("/api/server/events?kind=job&id=daily_analysis").json()
    assert [(e["kind"], e["id"]) for e in only_job] == [("job", "daily_analysis")]
    assert only_job[0]["duration_ms"] == 5000

    assert client.get("/api/server/events?kind=bogus").status_code == 400


def test_source_runs_endpoint_carries_the_full_traceback(client, app_module):
    from app import ingest

    def boom():
        raise RuntimeError("deep failure")

    ingest.run_source(app_module.conn, "vix", boom, db.upsert_vix)
    runs = client.get("/api/server/sources/vix/runs?limit=10").json()
    assert runs[0]["outcome"] == "error"
    assert "Traceback" in runs[0]["error_detail"]
    events = client.get("/api/server/events?kind=source&id=vix").json()
    assert events[0]["has_error_detail"] is True
    assert "error_detail" not in events[0]      # the log stays light
    assert client.get("/api/server/sources/bogus/runs").status_code == 404


def test_job_events_record_their_duration(app_module):
    from apscheduler.events import EVENT_JOB_EXECUTED

    app_module._timed_job("prune_history", lambda: None)

    class _Event:
        code = EVENT_JOB_EXECUTED
        job_id = "prune_history"
        exception = None
        scheduled_run_time = None

    app_module._on_job_event(_Event())
    run = db.get_job_runs(app_module.refresh_conn, job_id="prune_history")[0]
    assert run.event == "executed"
    assert run.duration_ms is not None and run.duration_ms >= 0


def test_max_instances_is_recorded_as_coalesced_not_dropped(app_module):
    from apscheduler.events import EVENT_JOB_MAX_INSTANCES

    class _Event:
        code = EVENT_JOB_MAX_INSTANCES
        job_id = "src:vix"
        exception = None

    app_module._on_job_event(_Event())
    run = db.get_job_runs(app_module.refresh_conn)[0]
    assert run.event == "coalesced"
    assert "merged into that run" in run.detail
