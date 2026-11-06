#!/usr/bin/env python3
"""One-click updater for the Raspberry Pi install. Stdlib only.

`apply` is what stocks-update.service runs after POST /api/update/apply wrote
<STOCKS_LOG_DIR>/update-request.json (app/updater.py::spawn_update):

  1. pull      git pull --ff-only origin main
  2. deps      backend/.venv/bin/python -m pip install -r requirements.txt
  3. frontend  the prebuilt web app for the new commit, from the web-dist
               branch CI publishes (waits for CI up to WEB_DIST_WAIT_SECONDS)
  4. restart   systemctl restart stocks-dashboard.service, then wait until
               /api/health reports the new commit

Any failure after the pull rolls back: git reset --hard <previous>, the old
packages, the previous web app (frontend/dist.prev), restart. Progress goes to
<STOCKS_LOG_DIR>/update-status.json in the schema app/updater.py and the UI
read, and a transcript to update.log next to it.

`fetch-dist <commit>` installs the web app for one commit; pi/install.sh uses it.

It runs as its own systemd unit, so restarting stocks-dashboard.service in
step 4 never kills it.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
DIST = REPO / "frontend" / "dist"
PYTHON = BACKEND / ".venv" / "bin" / "python"
LOG_DIR = Path(os.environ.get("STOCKS_LOG_DIR") or BACKEND / "logs")
STATUS_FILE = LOG_DIR / "update-status.json"
REQUEST_FILE = LOG_DIR / "update-request.json"
LOG_FILE = LOG_DIR / "update.log"

SERVICE = "stocks-dashboard.service"
REMOTE, BRANCH, WEB_DIST_BRANCH = "origin", "main", "web-dist"
WEB_DIST_DEPTH = 20
WEB_DIST_WAIT_SECONDS = 600
WEB_DIST_POLL_SECONDS = 30
HEALTH_WAIT_SECONDS = 240
TOOL_TIMEOUT_SECONDS = 900
ERROR_RE = re.compile(r"error|fatal|failed|unexpected|cannot|not found|denied|conflict|abort", re.I)

STEPS = [
    ("pull", "Pull from GitHub"),
    ("deps", "Install Python packages"),
    ("frontend", "Download the web app"),
    ("restart", "Restart"),
]


class StepFailed(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}\n")


def tail(output: str) -> str:
    """The lines worth showing in the UI: errors first, else the last three."""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    errors = [ln for ln in lines if ERROR_RE.search(ln)]
    return " | ".join(errors[:4] or lines[-3:]) or "no output"


def run(*cmd, cwd=None, timeout=TOOL_TIMEOUT_SECONDS) -> str:
    """Run a tool (in the repo unless told otherwise), log its output, return
    stdout; raise StepFailed with the tail."""
    log("> " + " ".join(map(str, cmd)))
    try:
        proc = subprocess.run([str(c) for c in cmd], cwd=cwd or REPO, capture_output=True, text=True,
                              timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(f"    {exc}")
        raise StepFailed(str(exc)) from exc
    output = (proc.stdout or "") + (proc.stderr or "")
    for line in output.splitlines():
        log(f"    {line}")
    if proc.returncode != 0:
        raise StepFailed(tail(output))
    return proc.stdout


def git(*args, **kw) -> str:
    return run("git", *args, **kw).strip()


# ---------- web app ----------

def find_web_dist(commit: str) -> str | None:
    """The web-dist commit CI built from `commit`, or None while it doesn't exist."""
    try:
        git("fetch", "--quiet", f"--depth={WEB_DIST_DEPTH}", REMOTE,
            f"+refs/heads/{WEB_DIST_BRANCH}:refs/remotes/{REMOTE}/{WEB_DIST_BRANCH}")
    except StepFailed:
        return None
    found = git("log", f"{REMOTE}/{WEB_DIST_BRANCH}", "--format=%H", "-n1",
                f"--grep=^source: {commit}$")
    return found or None


def fetch_dist(commit: str, wait_seconds: int | None = None) -> str:
    """Install the web app built from `commit` into frontend/dist, keeping the
    previous one as dist.prev; waits up to WEB_DIST_WAIT_SECONDS for CI to
    publish it. Returns the web-dist commit used."""
    wait_seconds = WEB_DIST_WAIT_SECONDS if wait_seconds is None else wait_seconds
    deadline = time.monotonic() + wait_seconds
    while (built := find_web_dist(commit)) is None:
        if time.monotonic() >= deadline:
            raise StepFailed(f"no web app built for {commit[:7]} on the {WEB_DIST_BRANCH} branch "
                             f"after {wait_seconds}s — check the 'pi' workflow on GitHub")
        time.sleep(WEB_DIST_POLL_SECONDS)
    archive = subprocess.run(["git", "archive", built], cwd=REPO, capture_output=True, check=True).stdout
    fresh = DIST.with_name("dist.new")
    shutil.rmtree(fresh, ignore_errors=True)
    with tarfile.open(fileobj=BytesIO(archive)) as tar:
        tar.extractall(fresh, filter="data")
    if not (fresh / "index.html").is_file():
        shutil.rmtree(fresh, ignore_errors=True)
        raise StepFailed(f"web-dist commit {built[:7]} has no index.html")
    previous = DIST.with_name("dist.prev")
    shutil.rmtree(previous, ignore_errors=True)
    if DIST.exists():
        DIST.rename(previous)
    fresh.rename(DIST)
    return built


def restore_dist() -> None:
    previous = DIST.with_name("dist.prev")
    if previous.is_dir():
        shutil.rmtree(DIST, ignore_errors=True)
        previous.rename(DIST)


# ---------- service ----------

def health_commit(port: int) -> str | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as resp:
            return json.load(resp).get("commit")
    except (OSError, ValueError):
        return None


def same_commit(a: str | None, b: str | None) -> bool:
    return bool(a and b and (a.startswith(b) or b.startswith(a)))


def restart(expect: str, port: int) -> None:
    run("systemctl", "restart", SERVICE)
    deadline = time.monotonic() + HEALTH_WAIT_SECONDS
    while time.monotonic() < deadline:
        if same_commit(health_commit(port), expect):
            return
        time.sleep(2)
    raise StepFailed(f"{SERVICE} did not come back on port {port} with commit {expect} "
                     f"within {HEALTH_WAIT_SECONDS}s")


def install_deps() -> None:
    run(PYTHON, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
        "-r", "requirements.txt", cwd=BACKEND)
    # Byte-compile now: the hardened service can't write __pycache__ itself,
    # and a Pi 3 would otherwise recompile every module on every start.
    subprocess.run([str(PYTHON), "-m", "compileall", "-q", "app"], cwd=BACKEND, check=False)


# ---------- status ----------

class Status:
    def __init__(self, previous: str):
        self.data = {
            "state": "running", "mode": "service",
            "started_at": now_iso(), "updated_at": now_iso(), "finished_at": None,
            "previous_commit": previous, "target_commit": None, "new_commit": None,
            "message": "updating",
            "steps": [{"id": sid, "label": label, "state": "pending", "detail": ""}
                      for sid, label in STEPS],
        }
        self.save()

    def save(self) -> None:
        self.data["updated_at"] = now_iso()
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATUS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        os.replace(tmp, STATUS_FILE)

    def step(self, sid: str, state: str, detail: str = "") -> None:
        for s in self.data["steps"]:
            if s["id"] == sid:
                s["state"] = state
                if detail:
                    s["detail"] = detail
        log(f"step {sid} -> {state} {detail}")
        self.save()

    def finish(self, state: str, message: str) -> int:
        self.data.update(state=state, message=message, finished_at=now_iso())
        self.save()
        log(f"finished: {state} - {message}")
        return 0 if state == "done" else 1


def rollback(status: Status, failed: str, reason: str, previous_full: str,
             previous: str, port: int) -> int:
    status.step(failed, "failed", reason)
    status.data["steps"].append({"id": "rollback", "label": "Roll back", "state": "running", "detail": ""})
    status.save()
    problems = []
    for what, action in (
        ("git reset", lambda: git("reset", "--hard", previous_full)),
        ("reinstalling the old packages", install_deps),
        ("restoring the previous web app", restore_dist),
        ("restarting", lambda: restart(previous, port)),
    ):
        try:
            action()
        except Exception as exc:  # noqa: BLE001 - every problem is reported, none aborts
            problems.append(f"{what} failed: {exc}")
    rb = status.data["steps"][-1]
    if not problems:
        rb.update(state="done", detail=f"back on {previous}")
        return status.finish("rolled_back", f"Update failed at '{failed}': {reason}. "
                                            f"Rolled back to {previous}.")
    rb.update(state="failed", detail="; ".join(problems))
    return status.finish("failed", f"Update failed at '{failed}': {reason}. ROLLBACK INCOMPLETE - "
                                   f"{'; '.join(problems)}. See {LOG_FILE}.")


def apply() -> int:
    try:
        request = json.loads(REQUEST_FILE.read_text(encoding="utf-8"))
        REQUEST_FILE.unlink()
    except (OSError, ValueError) as exc:
        log(f"no usable update request: {exc}")
        return 1
    previous, port = request["previous_commit"], int(request.get("port") or 8000)
    status = Status(previous)
    log(f"worker started: repo={REPO} previous={previous}")

    try:
        head = git("rev-parse", "HEAD")
        previous_full = git("rev-parse", "--verify", f"{previous}^{{commit}}")
    except StepFailed as exc:
        status.step("pull", "failed", str(exc))
        return status.finish("failed", f"Nothing was changed: {exc}")
    if head != previous_full:
        status.step("pull", "failed", f"HEAD is {head[:7]}, expected {previous}")
        return status.finish("failed", "Nothing was changed: the checkout moved since the update "
                                       "was requested.")
    if not PYTHON.exists():
        status.step("pull", "failed", f"the backend venv is missing ({PYTHON})")
        return status.finish("failed", "Nothing was changed: the backend venv is missing.")

    status.step("pull", "running")
    try:
        git("pull", "--ff-only", REMOTE, BRANCH)
    except StepFailed as exc:
        if git("rev-parse", "HEAD") == previous_full:
            status.step("pull", "failed", str(exc))
            return status.finish("failed", f"Nothing was changed: git pull failed ({exc}).")
        return rollback(status, "pull", str(exc), previous_full, previous, port)
    new_full = git("rev-parse", "HEAD")
    new = new_full[:7]
    status.data.update(new_commit=new, target_commit=new)
    status.step("pull", "done", f"{previous} -> {new}")

    for sid, action, done in (
        ("deps", install_deps, ""),
        ("frontend", lambda: fetch_dist(new_full), "prebuilt by CI"),
        ("restart", lambda: restart(new, port), f"{SERVICE} is back on {new}"),
    ):
        status.step(sid, "running")
        try:
            action()
        except Exception as exc:  # noqa: BLE001 - anything after the pull rolls back
            return rollback(status, sid, str(exc) or type(exc).__name__,
                            previous_full, previous, port)
        status.step(sid, "done", done)
    return status.finish("done", f"Updated {previous} -> {new}.")


def main(argv: list[str]) -> int:
    if argv[:1] == ["fetch-dist"] and len(argv) == 2:
        try:
            print(fetch_dist(git("rev-parse", "--verify", f"{argv[1]}^{{commit}}")))
        except StepFailed as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return 0
    if argv in ([], ["apply"]):
        return apply()
    print("usage: update.py [apply] | update.py fetch-dist <commit>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
