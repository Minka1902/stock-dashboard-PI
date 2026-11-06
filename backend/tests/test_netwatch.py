import importlib
import socket
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app import db, netwatch
from tests.conftest import authenticate, drain_refresh


@pytest.fixture(autouse=True)
def _online_again():
    netwatch.reset()
    yield
    netwatch.reset()


def test_probe_is_online_when_any_host_connects(monkeypatch):
    tried = []

    def connect(address, timeout):
        tried.append(address)
        if address[0] == "1.1.1.1":
            raise OSError("no route to host")
        return socket.socket()

    monkeypatch.setattr(netwatch.config, "NETCHECK_HOSTS", ["1.1.1.1:443", "8.8.8.8:53"])
    monkeypatch.setattr(socket, "create_connection", connect)
    assert netwatch.probe() is True
    assert tried == [("1.1.1.1", 443), ("8.8.8.8", 53)]


def test_probe_is_offline_when_nothing_connects(monkeypatch):
    def connect(address, timeout):
        raise OSError("network is unreachable")

    monkeypatch.setattr(socket, "create_connection", connect)
    assert netwatch.probe() is False


def test_check_reports_only_transitions(monkeypatch):
    monkeypatch.setattr(netwatch, "probe", lambda: False)
    assert netwatch.check() is False
    assert netwatch.check() is None
    assert netwatch.state()["online"] is False and netwatch.state()["since"]
    monkeypatch.setattr(netwatch, "probe", lambda: True)
    assert netwatch.check() is True
    assert netwatch.check() is None
    assert netwatch.is_online() is True


# ------------------------------------------------------------ app + scheduler
@pytest.fixture
def app_module(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "net.db"))
    from app import config, main as main_module
    importlib.reload(config)
    importlib.reload(main_module)
    yield main_module


@pytest.fixture
def client(app_module):
    with TestClient(app_module.app) as c:
        authenticate(c)
        yield c


def _go(app_module, monkeypatch, online: bool):
    monkeypatch.setattr(netwatch, "probe", lambda: online)
    app_module._netwatch()


def test_offline_source_defers_without_calling_upstream(client, app_module, monkeypatch):
    calls = []
    spec = app_module.SOURCES["vix"]
    app_module.SOURCES["vix"] = spec._replace(fetch=lambda: calls.append(1) or [])
    try:
        _go(app_module, monkeypatch, online=False)
        client.post("/api/server/schedules/vix/run-now")
        drain_refresh()
    finally:
        app_module.SOURCES["vix"] = spec
    assert calls == []
    run = db.get_source_runs(app_module.conn, "vix")[0]
    assert run.outcome == "deferred" and run.detail == netwatch.OFFLINE_REASON
    job = app_module.scheduler.get_job("src:vix")
    assert (job.next_run_time - datetime.now(UTC)).total_seconds() > 3000


def test_network_back_rearms_only_the_offline_deferrals(client, app_module, monkeypatch):
    _go(app_module, monkeypatch, online=False)
    for name in ("vix", "aaii"):
        client.post(f"/api/server/schedules/{name}/run-now")
    drain_refresh()
    before = app_module.scheduler.get_job("src:fear_greed").next_run_time

    _go(app_module, monkeypatch, online=True)

    for name in ("vix", "aaii"):
        due = app_module.scheduler.get_job(f"src:{name}").next_run_time
        assert (due - datetime.now(UTC)).total_seconds() < 5
    assert app_module.scheduler.get_job("src:fear_greed").next_run_time == before
    details = [r.detail for r in db.get_job_runs(app_module.conn, job_id="netwatch")]
    assert details[0].startswith("network back online; re-armed 2 deferred source(s)")
    assert details[1].startswith("network offline")


def test_derived_step_still_runs_offline(client, app_module, monkeypatch):
    from app import schedules

    order = []
    saved = {n: app_module.SOURCES[n] for n in schedules.DERIVED_MEMBERS}
    for n in schedules.DERIVED_MEMBERS:
        app_module.SOURCES[n] = saved[n]._replace(fetch=(lambda n=n: order.append(n) or []))
    try:
        _go(app_module, monkeypatch, online=False)
        client.post(f"/api/server/schedules/{schedules.DERIVED}/run-now")
        drain_refresh()
    finally:
        app_module.SOURCES.update(saved)
    assert order == list(schedules.DERIVED_MEMBERS)


def test_minute_jobs_wait_while_offline(app_module, monkeypatch):
    calls = []
    job = app_module._when_online(lambda: calls.append(1))
    _go(app_module, monkeypatch, online=False)
    job()
    assert calls == []
    _go(app_module, monkeypatch, online=True)
    job()
    assert calls == [1]


def test_netwatch_runs_off_the_refresh_thread(client, app_module):
    job = app_module.scheduler.get_job("netwatch")
    assert job.executor == "default"
    assert "netwatch" in app_module._QUIET_JOBS


def test_overview_reports_the_network(client, app_module, monkeypatch):
    _go(app_module, monkeypatch, online=False)
    net = client.get("/api/server/overview").json()["network"]
    assert net["online"] is False and net["since"]
