"""Per-source schedules: validation, triggers, next-due maths, the DB table,
the one-job-per-source scheduler wiring and the admin schedule API.

The point of the whole feature (task 9): the schedule is the only thing that
decides when a source runs, and a run that does not happen is never silent. It
is queued (runs late), deferred with a reason and a next attempt, or an error.
"""
import importlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import db, schedules
from app.models import SourceSchedule, SourceStatus
from tests.conftest import authenticate, drain_refresh

UTC = timezone.utc


def _sched(**kw):
    base = dict(source="vix", mode="interval", interval_seconds=180, times=[],
                days=list(schedules.DAYS), tz="UTC", enabled=True)
    base.update(kw)
    return SourceSchedule(**base)


def _status(state="ok", refreshed=None, success=None, next_attempt=None):
    return SourceStatus(source="vix", last_refreshed_at=refreshed, status=state,
                        record_count=1, last_success_at=success,
                        next_attempt_at=next_attempt)


# ---------------------------------------------------------------- validation
def test_validate_rejects_intervals_under_a_minute():
    with pytest.raises(ValueError, match="60"):
        schedules.validate(_sched(interval_seconds=59))


def test_validate_rejects_bad_times_and_requires_one_in_times_mode():
    with pytest.raises(ValueError):
        schedules.validate(_sched(mode="times", times=["25:00"]))
    with pytest.raises(ValueError):
        schedules.validate(_sched(mode="times", times=["6am"]))
    with pytest.raises(ValueError, match="at least one"):
        schedules.validate(_sched(mode="times", times=[]))


def test_validate_rejects_unknown_timezone_mode_and_days():
    with pytest.raises(ValueError, match="timezone"):
        schedules.validate(_sched(tz="Mars/Olympus"))
    with pytest.raises(ValueError, match="mode"):
        schedules.validate(_sched(mode="sometimes"))
    with pytest.raises(ValueError, match="day"):
        schedules.validate(_sched(days=["funday"]))
    with pytest.raises(ValueError, match="day"):
        schedules.validate(_sched(days=[]))


def test_validate_canonicalises_times_and_days():
    s = schedules.validate(_sched(mode="times", times=["18:00", "6:05", "18:00"],
                                  days=["FRI", "mon"]))
    assert s.times == ["06:05", "18:00"]
    assert s.days == ["mon", "fri"]


def test_defaults_are_seeded_from_the_registry_cadences():
    class Spec:
        def __init__(self, min_interval=None, retry_interval=None):
            self.min_interval = min_interval
            self.retry_interval = retry_interval

    specs = {
        "vix": Spec(),
        "gdelt": Spec(86400, 1800),
        "boom_score": Spec(),
        "alerts": Spec(),
    }
    rows = {s.source: s for s in schedules.defaults_from_specs(specs, 180, "UTC")}
    assert rows["vix"].interval_seconds == 180
    assert rows["gdelt"].interval_seconds == 86400
    assert rows["gdelt"].retry_seconds == 1800
    # boom_score/alerts are one derived step with its own row, not two jobs.
    assert "boom_score" not in rows and "alerts" not in rows
    assert rows[schedules.DERIVED].interval_seconds == 180


def test_margin_debt_seeds_weekly_monday_morning_slot():
    class Spec:
        min_interval = 7 * 86400
        retry_interval = 21600

    rows = {s.source: s for s in schedules.defaults_from_specs({"margin_debt": Spec()}, 180, "UTC")}
    md = schedules.validate(rows["margin_debt"])
    assert md.mode == "times"
    assert md.times == ["06:00"] and md.days == ["mon"]
    assert md.tz == "Asia/Jerusalem"
    assert md.retry_seconds == 21600


# ------------------------------------------------------------------ triggers
def test_interval_trigger_fires_after_the_interval():
    trig = schedules.build_trigger(_sched(interval_seconds=600))
    prev = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)  # a Tuesday
    assert trig.get_next_fire_time(prev, prev) == prev + timedelta(seconds=600)


def test_interval_trigger_honours_the_day_filter():
    trig = schedules.build_trigger(_sched(interval_seconds=3600, days=["mon", "tue", "wed", "thu", "fri"]))
    fri_late = datetime(2026, 9, 25, 23, 30, tzinfo=UTC)
    nxt = trig.get_next_fire_time(fri_late, fri_late)
    assert nxt.weekday() == 0  # skips the weekend, lands on Monday
    assert nxt >= datetime(2026, 9, 28, 0, 0, tzinfo=UTC)


def test_times_trigger_fires_at_each_wall_clock_time_in_its_zone():
    tz = ZoneInfo("Asia/Jerusalem")
    trig = schedules.build_trigger(_sched(mode="times", times=["06:00", "18:30"],
                                          days=["mon"], tz="Asia/Jerusalem"))
    sat = datetime(2026, 9, 26, 12, 0, tzinfo=tz)
    first = trig.get_next_fire_time(None, sat)
    assert first.astimezone(tz).strftime("%a %H:%M") == "Mon 06:00"
    second = trig.get_next_fire_time(first, first)
    assert second.astimezone(tz).strftime("%a %H:%M") == "Mon 18:30"


def test_describe_is_human_readable():
    assert schedules.describe(_sched(interval_seconds=180)) == "every 3 min"
    assert "06:00" in schedules.describe(
        _sched(mode="times", times=["06:00"], days=["mon"], tz="Asia/Jerusalem"))


# ------------------------------------------------------------------ next due
NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


def test_never_run_source_is_due_now():
    assert schedules.next_due(_sched(), None, NOW) == NOW


def test_healthy_source_is_due_one_interval_after_its_last_success():
    last = (NOW - timedelta(minutes=1)).isoformat()
    due = schedules.next_due(_sched(interval_seconds=600), _status("ok", last, last), NOW)
    assert due == NOW + timedelta(minutes=9)


def test_overdue_source_is_due_now_not_in_the_past():
    last = (NOW - timedelta(days=3)).isoformat()
    assert schedules.next_due(_sched(interval_seconds=600), _status("ok", last, last), NOW) == NOW


def test_failed_source_is_due_one_retry_after_the_attempt():
    tried = (NOW - timedelta(minutes=10)).isoformat()
    s = _sched(interval_seconds=14 * 86400, retry_seconds=1800)
    due = schedules.next_due(s, _status("error: RuntimeError: 401", tried, None), NOW)
    assert due == NOW + timedelta(minutes=20)


def test_deferred_source_is_due_at_its_recorded_next_attempt():
    later = (NOW + timedelta(minutes=7)).isoformat()
    st = _status("deferred: rate limited", NOW.isoformat(), None, next_attempt=later)
    assert schedules.next_due(_sched(), st, NOW) == NOW + timedelta(minutes=7)


def test_times_source_that_missed_a_fire_is_due_now():
    """A weekly run missed while the machine was off fires late, not never."""
    s = _sched(mode="times", times=["06:00"], days=["mon"], tz="UTC")
    last = datetime(2026, 9, 14, 6, 0, 5, tzinfo=UTC).isoformat()  # last Monday's run
    assert schedules.next_due(s, _status("ok", last, last), NOW) == NOW  # the 21st was missed


def test_retry_seconds_defaults():
    assert schedules.retry_after_seconds(_sched(interval_seconds=180)) == 180
    assert schedules.retry_after_seconds(_sched(interval_seconds=180, retry_seconds=90)) == 90
    assert schedules.retry_after_seconds(
        _sched(mode="times", times=["06:00"])) == schedules.DEFAULT_TIMES_RETRY_SECONDS


# ----------------------------------------------------------------------- db
def test_seed_is_idempotent_and_never_overwrites_an_edit(conn):
    db.seed_source_schedules(conn, [_sched(source="vix", interval_seconds=180)])
    edited = _sched(source="vix", interval_seconds=900, updated_at="2026-09-01T00:00:00+00:00")
    db.upsert_source_schedule(conn, edited)
    db.seed_source_schedules(conn, [_sched(source="vix", interval_seconds=180)])
    got = db.get_source_schedules(conn)["vix"]
    assert got.interval_seconds == 900


def test_seed_refreshes_rows_nobody_edited(conn):
    """A row still carrying an old seeded default (updated_at NULL: no admin
    ever saved it) follows the registry's new default — e.g. margin_debt went
    from every 14 days to a Monday 06:00 slot, and existing installs must pick
    that up rather than keep the stale seed forever."""
    db.seed_source_schedules(conn, [_sched(source="margin_debt", interval_seconds=14 * 86400)])
    new_default = _sched(source="margin_debt", mode="times", times=["06:00"], days=["mon"],
                         tz="Asia/Jerusalem", retry_seconds=21600)
    db.seed_source_schedules(conn, [new_default])
    got = db.get_source_schedules(conn)["margin_debt"]
    assert got.mode == "times"
    assert got.times == ["06:00"]
    assert got.days == ["mon"]
    assert got.tz == "Asia/Jerusalem"
    assert got.updated_at is None  # still a default, so it keeps following the registry


def test_schedule_round_trips_lists(conn):
    s = _sched(source="margin_debt", mode="times", times=["06:00", "18:00"],
               days=["mon", "thu"], tz="Asia/Jerusalem", enabled=False, retry_seconds=21600)
    db.upsert_source_schedule(conn, s)
    got = db.get_source_schedule(conn, "margin_debt")
    assert got.times == ["06:00", "18:00"]
    assert got.days == ["mon", "thu"]
    assert got.enabled is False
    assert got.retry_seconds == 21600


# ------------------------------------------------------------ app + scheduler
@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "sched.db"))
    from app import config, main as main_module
    importlib.reload(config)
    importlib.reload(main_module)
    yield main_module


@pytest.fixture
def client(app_module):
    with TestClient(app_module.app) as c:
        authenticate(c)  # first account -> admin
        yield c


def test_one_job_per_source_on_a_single_serial_executor(client, app_module):
    jobs = {j.id: j for j in app_module.scheduler.get_jobs()}
    assert "refresh_all" not in jobs
    for name in app_module.SOURCES:
        if name in schedules.DERIVED_MEMBERS:
            continue
        job = jobs[f"src:{name}"]
        assert job.executor == "refresh"
        assert job.misfire_grace_time is None  # a late job runs late, never dropped
        assert job.coalesce is True
        assert job.max_instances == 1
    assert jobs[f"src:{schedules.DERIVED}"].executor == "refresh"
    assert jobs["daily_analysis"].executor == "refresh"


def test_no_source_job_fires_during_startup(client, app_module):
    """Startup staggers first runs so a restart never stampedes the pool."""
    now = datetime.now(UTC)
    for job in app_module.scheduler.get_jobs():
        if job.id.startswith("src:") and job.next_run_time:
            assert job.next_run_time > now + timedelta(seconds=5)


def test_get_schedules_lists_every_source_with_next_run(client, app_module):
    rows = client.get("/api/server/schedules").json()
    names = {r["source"] for r in rows}
    expected = {n for n in app_module.SOURCES if n not in schedules.DERIVED_MEMBERS}
    assert names == expected | {schedules.DERIVED}
    by = {r["source"]: r for r in rows}
    assert by["gdelt"]["interval_seconds"] == app_module.config.GDELT_MIN_INTERVAL_SECONDS
    assert by["x_posts"]["interval_seconds"] == 3600
    assert by["vix"]["next_run_at"] is not None
    assert by[schedules.DERIVED]["members"] == list(schedules.DERIVED_MEMBERS)
    assert by["vix"]["description"]


def test_put_schedule_validates(client):
    assert client.put("/api/server/schedules/vix", json={"interval_seconds": 5}).status_code == 400
    assert client.put("/api/server/schedules/vix",
                      json={"mode": "times", "times": ["99:99"]}).status_code == 400
    assert client.put("/api/server/schedules/vix", json={"tz": "Nope/Nope"}).status_code == 400
    assert client.put("/api/server/schedules/bogus", json={"enabled": False}).status_code == 404
    assert client.put("/api/server/schedules/boom_score", json={"enabled": False}).status_code == 404


def test_put_schedule_reschedules_the_job(client, app_module):
    r = client.put("/api/server/schedules/vix",
                   json={"mode": "times", "times": ["06:00"], "days": ["mon"], "tz": "UTC"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "times" and body["times"] == ["06:00"]
    job = app_module.scheduler.get_job("src:vix")
    assert "cron" in str(job.trigger)
    # Never run -> due now, but never before the "soon" floor.
    assert job.next_run_time is not None
    assert db.get_source_schedule(app_module.conn, "vix").mode == "times"


def test_disabling_pauses_and_enabling_resumes(client, app_module):
    client.put("/api/server/schedules/vix", json={"enabled": False})
    assert app_module.scheduler.get_job("src:vix").next_run_time is None
    row = next(r for r in client.get("/api/server/schedules").json() if r["source"] == "vix")
    assert row["enabled"] is False and row["next_run_at"] is None
    client.put("/api/server/schedules/vix", json={"enabled": True})
    assert app_module.scheduler.get_job("src:vix").next_run_time is not None


def test_schedule_routes_are_admin_only(app_module):
    with TestClient(app_module.app) as admin:
        authenticate(admin)
        other = TestClient(app_module.app)
        authenticate(other, email="viewer@example.com")
        assert other.get("/api/server/schedules").status_code == 403
        assert other.put("/api/server/schedules/vix", json={"enabled": False}).status_code == 403
        assert other.post("/api/server/schedules/vix/run-now").status_code == 403


def test_run_now_runs_the_source_on_the_refresh_executor(client, app_module):
    calls = []
    spec = app_module.SOURCES["vix"]
    app_module.SOURCES["vix"] = spec._replace(fetch=lambda: calls.append(1) or [])
    try:
        r = client.post("/api/server/schedules/vix/run-now")
        assert r.status_code == 202
        drain_refresh()
        assert calls == [1]
        assert db.get_source_runs(app_module.conn, "vix")[0].outcome == "ok"
    finally:
        app_module.SOURCES["vix"] = spec


def test_run_now_on_derived_runs_its_members_in_order(client, app_module):
    order = []
    saved = {n: app_module.SOURCES[n] for n in schedules.DERIVED_MEMBERS}
    for n in schedules.DERIVED_MEMBERS:
        app_module.SOURCES[n] = saved[n]._replace(fetch=(lambda n=n: order.append(n) or []))
    try:
        assert client.post(f"/api/server/schedules/{schedules.DERIVED}/run-now").status_code == 202
        drain_refresh()
        assert order == ["boom_score", "alerts", "regime", "automute", "rules"]
    finally:
        app_module.SOURCES.update(saved)


def test_run_now_unknown_source_is_404(client):
    assert client.post("/api/server/schedules/bogus/run-now").status_code == 404


# -------------------------------------------------- outcomes drive the clock
def test_error_pulls_the_next_run_to_the_retry_interval(client, app_module):
    def boom():
        raise RuntimeError("upstream 403")

    client.put("/api/server/schedules/margin_debt",
               json={"interval_seconds": 14 * 86400, "retry_seconds": 3600})
    spec = app_module.SOURCES["margin_debt"]
    app_module.SOURCES["margin_debt"] = spec._replace(fetch=boom)
    try:
        client.post("/api/server/schedules/margin_debt/run-now")
        drain_refresh()
    finally:
        app_module.SOURCES["margin_debt"] = spec
    job = app_module.scheduler.get_job("src:margin_debt")
    delta = (job.next_run_time - datetime.now(UTC)).total_seconds()
    assert 3500 < delta <= 3600
    run = db.get_source_runs(app_module.conn, "margin_debt")[0]
    assert run.outcome == "error"
    assert run.next_attempt_at is not None  # the reason AND when it tries again
    assert "RuntimeError" in run.error_detail


def test_deferred_sets_the_next_run_to_the_sources_own_next_attempt(client, app_module):
    from app.ingest import SourceDeferred

    def cooling():
        raise SourceDeferred("rate limited (429)", retry_after_seconds=600)

    spec = app_module.SOURCES["gdelt"]
    app_module.SOURCES["gdelt"] = spec._replace(fetch=cooling)
    try:
        client.post("/api/server/schedules/gdelt/run-now")
        drain_refresh()
    finally:
        app_module.SOURCES["gdelt"] = spec
    job = app_module.scheduler.get_job("src:gdelt")
    delta = (job.next_run_time - datetime.now(UTC)).total_seconds()
    assert 540 < delta <= 600
    row = next(r for r in client.get("/api/server/sources").json() if r["source"] == "gdelt")
    assert row["status"].startswith("deferred")
    assert row["next_attempt_at"] is not None


def test_upstream_success_pulls_the_derived_step_forward(client, app_module):
    spec = app_module.SOURCES["vix"]
    app_module.SOURCES["vix"] = spec._replace(fetch=lambda: [])
    try:
        client.post("/api/server/schedules/vix/run-now")
        drain_refresh()
    finally:
        app_module.SOURCES["vix"] = spec
    job = app_module.scheduler.get_job(f"src:{schedules.DERIVED}")
    delta = (job.next_run_time - datetime.now(UTC)).total_seconds()
    assert delta <= app_module.config.DERIVED_DEBOUNCE_SECONDS + 1
