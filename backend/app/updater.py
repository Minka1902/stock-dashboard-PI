"""In-app update: compare this working tree against GitHub, and hand off an update.

The app runs straight from a git checkout (the Windows service included — see
windows/README.md), so "is there a newer version?" is a git question:

    git fetch origin main
    git rev-list --count HEAD..origin/main     -> how far behind
    git rev-list --count origin/main..HEAD     -> local commits (blocks a fast-forward)
    git log HEAD..origin/main                  -> what the new commits are

Nothing here ever invents a status. If git is missing, or GitHub can't be
reached, the check says exactly that and reports no update.

Applying is deliberately NOT done in this process: the update restarts the very
server that would be running it. `spawn_update` hands off to a detached
windows/update.ps1 which pulls, installs, rebuilds, restarts, and rolls back on
failure, writing its progress to `<LOG_DIR>/update-status.json` for the UI.

Every git call passes `-c safe.directory=<repo>`: the service runs as
LocalSystem, and git refuses ("dubious ownership") to touch a tree owned by the
user who cloned it.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel

from app import config
from app.version import __version__

logger = logging.getLogger(__name__)

# backend/app/updater.py -> repo root is three levels up.
REPO_ROOT = Path(__file__).resolve().parents[2]
REMOTE = "origin"
BRANCH = "main"
SERVICE_NAME = "SignalDashboard"

FETCH_TIMEOUT_SECONDS = 30
GIT_TIMEOUT_SECONDS = 10
CACHE_TTL_SECONDS = 3600
CHECK_INTERVAL_HOURS = 6
MAX_COMMITS = 50
MAX_DIRTY_FILES = 20
# A "running" status file older than this is treated as a dead updater (the
# machine rebooted mid-update, say) rather than blocking updates forever.
STALE_RUN_SECONDS = 30 * 60

STATUS_FILE_NAME = "update-status.json"
UPDATE_SCRIPT = REPO_ROOT / "windows" / "update.ps1"

_FIELD_SEP = "\x1f"
_VERSION_RE = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']""", re.MULTILINE)


class GitError(RuntimeError):
    """A git invocation failed; the message is safe to show to the user."""


class Commit(BaseModel):
    sha: str
    subject: str
    author: str
    date: str  # ISO-8601 committer date, as git reports it


class UpdateCheck(BaseModel):
    checked_at: str
    ok: bool                       # the check itself ran (git + network worked)
    error: str | None = None
    current_version: str = __version__
    current_commit: str | None = None
    branch: str | None = None      # None when HEAD is detached
    remote: str = f"{REMOTE}/{BRANCH}"
    remote_commit: str | None = None
    behind: int = 0
    ahead: int = 0
    commits: list[Commit] = []
    commits_truncated: bool = False
    latest_version: str | None = None
    dirty_files: list[str] = []    # tracked modifications only
    dirty_count: int = 0
    update_available: bool = False
    can_apply: bool = False
    blocked_reason: str | None = None


# ---------- pure helpers (unit-tested directly) ----------

def git_log_format() -> str:
    return "%h%x1f%s%x1f%an%x1f%cI"


def parse_log(text: str) -> list[Commit]:
    """Parse `git log --format=%h%x1f%s%x1f%an%x1f%cI` output."""
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split(_FIELD_SEP)
        if len(parts) != 4:
            continue  # never guess at a malformed record
        sha, subject, author, date = (p.strip() for p in parts)
        out.append(Commit(sha=sha, subject=subject, author=author, date=date))
    return out


def parse_count(text: str) -> int:
    try:
        return max(0, int(text.strip()))
    except ValueError as exc:
        raise GitError(f"unexpected git output: {text.strip()[:80]!r}") from exc


def parse_porcelain(text: str) -> list[str]:
    """Paths with tracked modifications from `git status --porcelain=v1`.

    Untracked files (`??`) and ignored (`!!`) are skipped: they don't stop a
    fast-forward unless a pulled file collides with one, and in that case
    `git pull --ff-only` refuses on its own and the updater rolls back.
    """
    paths = []
    for line in text.splitlines():
        if len(line) < 4 or line.startswith(("??", "!!")):
            continue
        paths.append(line[3:].strip())
    return paths


def parse_version(source: str) -> str | None:
    match = _VERSION_RE.search(source or "")
    return match.group(1) if match else None


def parse_branch(text: str) -> str | None:
    name = text.strip()
    return None if not name or name == "HEAD" else name


def blocked_reason(check: UpdateCheck) -> str | None:
    """Why a one-click update can't run right now, or None if it can.

    Ordered so the most fundamental problem is the one reported.
    """
    if not check.ok:
        return check.error or "the update check failed"
    if check.behind == 0:
        return "already up to date"
    if check.branch != BRANCH:
        where = f"branch '{check.branch}'" if check.branch else "a detached HEAD"
        return f"the server is on {where}, not '{BRANCH}' — switch branches by hand to update"
    if check.dirty_count:
        return (f"{check.dirty_count} tracked file(s) have local changes — "
                "commit or discard them first")
    if check.ahead:
        return (f"this checkout has {check.ahead} local commit(s) not on {REMOTE}/{BRANCH}, "
                "so it can't fast-forward — merge by hand")
    return None


def friendly_fetch_error(stderr: str) -> str:
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    detail = lines[-1] if lines else "no output from git"
    return f"could not reach GitHub: {detail}"


# ---------- git plumbing ----------

def _creationflags() -> int:
    # No console window flashing up under the service or the desktop shell.
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _git(*args: str, timeout: int = GIT_TIMEOUT_SECONDS) -> str:
    cmd = ["git", "-c", f"safe.directory={REPO_ROOT.as_posix()}", *args]
    env = dict(os.environ)
    # Never block a request thread on a credential prompt.
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    try:
        proc = subprocess.run(
            cmd, cwd=str(REPO_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, env=env,
            creationflags=_creationflags(),
        )
    except FileNotFoundError as exc:
        raise GitError("git is not installed or not on the server's PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {args[0]} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        err = GitError((proc.stderr or proc.stdout or "").strip() or f"git {args[0]} failed")
        err.stderr = proc.stderr or ""
        raise err
    return proc.stdout


def _head_short() -> str | None:
    try:
        return _git("rev-parse", "--short", "HEAD").strip() or None
    except GitError:
        return None


# The commit this process started from — what /api/health reports. Cached for
# the process lifetime on purpose: after an update, a *changed* value is the
# signal that the restarted server is actually running the new code.
STARTUP_COMMIT: str | None = _head_short()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run_check() -> UpdateCheck:
    check = UpdateCheck(checked_at=_now_iso(), ok=False)
    try:
        check.current_commit = _git("rev-parse", "--short", "HEAD").strip()
        check.branch = parse_branch(_git("rev-parse", "--abbrev-ref", "HEAD"))
        check.dirty_files = parse_porcelain(
            _git("status", "--porcelain=v1", "--untracked-files=no"))
    except GitError as exc:
        check.error = f"git unavailable: {exc}"
        check.blocked_reason = check.error
        return check
    check.dirty_count = len(check.dirty_files)
    check.dirty_files = check.dirty_files[:MAX_DIRTY_FILES]

    try:
        _git("fetch", "--quiet", REMOTE, BRANCH, timeout=FETCH_TIMEOUT_SECONDS)
    except GitError as exc:
        msg = str(exc)
        if "not installed" in msg:
            check.error = msg
        elif "timed out" in msg:
            check.error = f"could not reach GitHub: {msg}"
        else:
            check.error = friendly_fetch_error(getattr(exc, "stderr", "") or msg)
        check.blocked_reason = check.error
        return check

    upstream = f"{REMOTE}/{BRANCH}"
    try:
        check.remote_commit = _git("rev-parse", "--short", upstream).strip()
        check.behind = parse_count(_git("rev-list", "--count", f"HEAD..{upstream}"))
        check.ahead = parse_count(_git("rev-list", "--count", f"{upstream}..HEAD"))
        if check.behind:
            check.commits = parse_log(_git(
                "log", f"--max-count={MAX_COMMITS}", f"--format={git_log_format()}",
                f"HEAD..{upstream}"))
            check.commits_truncated = check.behind > len(check.commits)
        try:
            check.latest_version = parse_version(
                _git("show", f"{upstream}:backend/app/version.py"))
        except GitError:
            check.latest_version = None  # file moved/absent upstream: say nothing
    except GitError as exc:
        check.error = f"git failed: {exc}"
        check.blocked_reason = check.error
        return check

    check.ok = True
    check.update_available = check.behind > 0
    check.blocked_reason = blocked_reason(check)
    check.can_apply = check.blocked_reason is None
    return check


_lock = threading.Lock()
_cache: tuple[float, UpdateCheck] | None = None


def check(force: bool = False) -> UpdateCheck:
    """The latest check, re-running it when forced or older than an hour.

    Serialized: two concurrent `git fetch`es in one repo fight over
    `.git/FETCH_HEAD` and the ref locks.
    """
    global _cache
    with _lock:
        if not force and _cache and time.monotonic() - _cache[0] < CACHE_TTL_SECONDS:
            return _cache[1]
        result = _run_check()
        _cache = (time.monotonic(), result)
        return result


def cached() -> UpdateCheck | None:
    return _cache[1] if _cache else None


def clear_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def scheduled_check() -> None:
    """Scheduler entry point. Skips while an update is running — a fetch in
    the middle of the updater's `git pull` would contend for the same locks."""
    if is_apply_running():
        return
    result = check(force=True)
    if result.ok and result.update_available:
        logger.info("update available: %s commit(s) behind %s/%s",
                    result.behind, REMOTE, BRANCH)
    elif not result.ok:
        logger.warning("update check failed: %s", result.error)


def schedule(scheduler) -> None:
    """Register the 6-hourly check. First run ~2 min after start so a boot
    doesn't stack a network fetch on top of the first ingest cycle."""
    scheduler.add_job(
        scheduled_check,
        "interval",
        hours=CHECK_INTERVAL_HOURS,
        id="update_check",
        replace_existing=True,
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=2),
    )


# ---------- apply progress (written by windows/update.ps1) ----------

def status_path() -> Path:
    return Path(config.LOG_DIR) / STATUS_FILE_NAME


def read_apply_status() -> dict | None:
    path = status_path()
    if not path.exists():
        return None
    try:
        # utf-8-sig: tolerate a BOM should PowerShell ever write one.
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return {"state": "unknown", "message": f"status file unreadable: {exc}"}


def _parse_iso(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_apply_running(status: dict | None = None) -> bool:
    status = status if status is not None else read_apply_status()
    if not status or status.get("state") != "running":
        return False
    updated = _parse_iso(status.get("updated_at") or status.get("started_at"))
    if updated is None:
        return False
    return (datetime.now(timezone.utc) - updated).total_seconds() < STALE_RUN_SECONDS


STEPS = [
    ("pull", "Pull from GitHub"),
    ("deps", "Install Python packages"),
    ("frontend", "Rebuild the web app"),
    ("restart", "Restart"),
]


def initial_status(previous: str, target: str | None, mode: str) -> dict:
    now = _now_iso()
    return {
        "state": "running",
        "mode": mode,
        "started_at": now,
        "updated_at": now,
        "finished_at": None,
        "previous_commit": previous,
        "target_commit": target,
        "new_commit": None,
        "message": "starting the updater",
        "steps": [{"id": sid, "label": label, "state": "pending", "detail": ""}
                  for sid, label in STEPS],
    }


def write_status(status: dict) -> None:
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(status, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ---------- run mode + hand-off ----------

def _service_pid() -> int | None:
    if sys.platform != "win32":
        return None
    try:
        out = subprocess.run(
            ["sc", "queryex", SERVICE_NAME], capture_output=True, text=True,
            timeout=5, creationflags=_creationflags()).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"PID\s*:\s*(\d+)", out)
    pid = int(match.group(1)) if match else 0
    return pid or None  # 0 = service stopped


def _ancestor_pids() -> list[int]:
    pids = [os.getppid()]
    try:
        import psutil
        pids += [p.pid for p in psutil.Process().parents()]
    except Exception:  # noqa: BLE001 - psutil is optional; the parent pid alone suffices
        pass
    return pids


def detect_mode() -> str:
    """'service' when this process is hosted by the SignalDashboard NSSM
    service, else 'dev'.

    The signal is structural, not a flag someone has to remember to set: NSSM
    is the service's process and runs python as its direct child, so the
    service's PID is one of our ancestors exactly when we are the service.
    STOCKS_UPDATE_MODE=service|dev overrides it.
    """
    override = os.environ.get("STOCKS_UPDATE_MODE", "").strip().lower()
    if override in ("service", "dev"):
        return override
    pid = _service_pid()
    return "service" if pid and pid in _ancestor_pids() else "dev"


def build_command(previous: str, mode: str, port: int) -> list[str]:
    return [
        "powershell.exe", "-NoProfile", "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        # The script finds the repo and the service name through
        # windows/Common.ps1, like the other service scripts.
        "-File", str(UPDATE_SCRIPT),
        "-PreviousCommit", previous,
        "-Mode", mode,
        "-StatusDir", str(Path(config.LOG_DIR)),
        "-Port", str(port),
    ]


def spawn_update(previous: str, mode: str, port: int) -> None:
    """Start update.ps1 fully detached from this process.

    It has to outlive us: its last step restarts this server. The script
    relaunches itself once more (stage 2) so its parent pid is a process that
    has already exited — NSSM's stop kills the service's process *tree* by
    parent pid, and would otherwise take the updater down mid-restart.
    """
    if sys.platform != "win32":
        raise RuntimeError("the one-click updater is Windows-only (windows/update.ps1)")
    if not UPDATE_SCRIPT.exists():
        raise RuntimeError(f"{UPDATE_SCRIPT} is missing")
    # DETACHED_PROCESS: no console at all (so no window, and no Ctrl-C shared
    # with the server's console group when NSSM stops it).
    base = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    kwargs = dict(cwd=str(REPO_ROOT), stdin=subprocess.DEVNULL,
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    cmd = build_command(previous, mode, port)
    try:
        subprocess.Popen(cmd, creationflags=base | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs)
    except OSError:
        # The job we run in (if any) forbids breakaway; the stage-2 relaunch
        # still orphans the real worker from our process tree.
        subprocess.Popen(cmd, creationflags=base, **kwargs)
