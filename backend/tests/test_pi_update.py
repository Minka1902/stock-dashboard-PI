"""pi/update.py against real git repos: a bare 'GitHub', a dev clone that
pushes main and the CI-built web-dist branch, and the 'Pi' checkout it updates.
pip and systemctl are stubbed; git, the status file and the dist swap are real."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "pi" / "update.py"


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(repo, path, text, message):
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _publish_web_dist(dev, source_sha, html):
    """What the CI job does: one orphan-branch commit per main commit."""
    work = dev.parent / f"dist-{source_sha[:7]}"
    _git(dev.parent, "clone", "-q", str(dev.parent / "remote.git"), str(work))
    try:
        _git(work, "fetch", "-q", "origin", "web-dist")
        _git(work, "checkout", "-q", "-B", "web-dist", "origin/web-dist")
    except subprocess.CalledProcessError:
        _git(work, "checkout", "-q", "--orphan", "web-dist")
        _git(work, "rm", "-rqf", ".")
    for f in work.iterdir():
        if f.name != ".git":
            subprocess.run(["rm", "-rf", str(f)], check=True)
    (work / "index.html").write_text(html)
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", f"source: {source_sha}")
    _git(work, "push", "-q", "origin", "web-dist")


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_AUTHOR_NAME", "t")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "t@x.co")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "t")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "t@x.co")
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", "remote.git")
    dev = tmp_path / "dev"
    _git(tmp_path, "clone", "-q", str(tmp_path / "remote.git"), str(dev))
    v1 = _commit(dev, "backend/requirements.txt", "fastapi\n", "v1")
    _git(dev, "push", "-q", "origin", "HEAD:main")
    _publish_web_dist(dev, v1, "<p>v1</p>")

    pi = tmp_path / "pi"
    _git(tmp_path, "clone", "-q", "-b", "main", str(tmp_path / "remote.git"), str(pi))

    spec = importlib.util.spec_from_file_location("pi_update", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    logs = tmp_path / "logs"
    for name, value in {
        "REPO": pi, "BACKEND": pi / "backend", "DIST": pi / "frontend" / "dist",
        "PYTHON": Path(subprocess.check_output(["which", "python3"], text=True).strip()),
        "LOG_DIR": logs, "STATUS_FILE": logs / "update-status.json",
        "REQUEST_FILE": logs / "update-request.json", "LOG_FILE": logs / "update.log",
        "WEB_DIST_WAIT_SECONDS": 0,
    }.items():
        monkeypatch.setattr(mod, name, value)
    restarts = []
    monkeypatch.setattr(mod, "install_deps", lambda: None)
    monkeypatch.setattr(mod, "restart", lambda expect, port: restarts.append(expect))
    assert mod.main(["fetch-dist", v1]) == 0
    return {"mod": mod, "dev": dev, "pi": pi, "v1": v1, "logs": logs, "restarts": restarts}


def _request(world, previous=None):
    world["logs"].mkdir(exist_ok=True)
    (world["logs"] / "update-request.json").write_text(
        json.dumps({"previous_commit": previous or world["v1"][:7], "port": 8000}))


def _status(world):
    return json.loads((world["logs"] / "update-status.json").read_text())


def _push_v2(world, with_web_dist=True):
    v2 = _commit(world["dev"], "backend/app.py", "print('v2')\n", "v2")
    _git(world["dev"], "push", "-q", "origin", "HEAD:main")
    if with_web_dist:
        _publish_web_dist(world["dev"], v2, "<p>v2</p>")
    return v2


def test_fetch_dist_installs_the_build_for_that_commit(world):
    assert (world["pi"] / "frontend" / "dist" / "index.html").read_text() == "<p>v1</p>"


def test_apply_pulls_installs_swaps_the_web_app_and_restarts(world):
    v2 = _push_v2(world)
    _request(world)
    assert world["mod"].main(["apply"]) == 0
    status = _status(world)
    assert status["state"] == "done", status
    assert [s["state"] for s in status["steps"]] == ["done"] * 4
    assert _git(world["pi"], "rev-parse", "HEAD") == v2
    dist = world["pi"] / "frontend"
    assert (dist / "dist" / "index.html").read_text() == "<p>v2</p>"
    assert (dist / "dist.prev" / "index.html").read_text() == "<p>v1</p>"
    assert world["restarts"] == [v2[:7]]
    assert not (world["logs"] / "update-request.json").exists()


def test_a_failed_restart_rolls_everything_back(world, monkeypatch):
    v2 = _push_v2(world)

    def restart(expect, port):
        world["restarts"].append(expect)
        if expect == v2[:7]:
            raise world["mod"].StepFailed("did not come back")

    monkeypatch.setattr(world["mod"], "restart", restart)
    _request(world)
    assert world["mod"].main(["apply"]) == 1
    status = _status(world)
    assert status["state"] == "rolled_back"
    assert status["steps"][-1] == {"id": "rollback", "label": "Roll back",
                                   "state": "done", "detail": f"back on {world['v1'][:7]}"}
    assert _git(world["pi"], "rev-parse", "HEAD") == world["v1"]
    assert (world["pi"] / "frontend" / "dist" / "index.html").read_text() == "<p>v1</p>"
    assert world["restarts"] == [v2[:7], world["v1"][:7]]


def test_no_ci_build_for_the_new_commit_rolls_back(world):
    _push_v2(world, with_web_dist=False)
    _request(world)
    assert world["mod"].main(["apply"]) == 1
    status = _status(world)
    assert status["state"] == "rolled_back"
    frontend = next(s for s in status["steps"] if s["id"] == "frontend")
    assert frontend["state"] == "failed" and "no web app built" in frontend["detail"]
    assert _git(world["pi"], "rev-parse", "HEAD") == world["v1"]


def test_a_moved_checkout_changes_nothing(world):
    _push_v2(world)
    _request(world, previous="0000000")
    assert world["mod"].main(["apply"]) == 1
    status = _status(world)
    assert status["state"] == "failed" and status["message"].startswith("Nothing was changed")
    assert _git(world["pi"], "rev-parse", "HEAD") == world["v1"]
    assert world["restarts"] == []
