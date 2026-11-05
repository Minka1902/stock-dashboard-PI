"""Supervised uvicorn launcher.

Runs the API as a child process, logs how it exited, and restarts it with
backoff. The app is a single in-process worker holding the scheduler, the TTL
caches, the rate limiter and the SQLite connections, so if it dies there is
nothing else to take over — previously it just stopped, silently, and the only
evidence went to a console window that had already been closed.

Usage:
    python run_server.py [--port 8000] [--host 127.0.0.1] [--reload]
                         [--max-restarts N] [--no-supervise]
"""
import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

BACKOFF_SECONDS = [2, 5, 10, 30, 60]
# A crash within this many seconds of starting counts as "failed to start"
# rather than "ran and later died", and escalates the backoff.
FAST_FAIL_SECONDS = 20

logger = logging.getLogger("supervisor")


def _trusted_proxies() -> str:
    """STOCKS_TRUSTED_PROXY_IPS, read straight from the environment.

    This is a launcher; it deliberately does not import app.config (same reason
    STOCKS_LOG_DIR is read by hand below). uvicorn wraps the app in its own
    ProxyHeadersMiddleware whose default trust list is "127.0.0.1"; passing the
    value through keeps that layer and app/security.py::_client_ip agreeing on
    one boundary instead of two independent defaults.
    """
    return os.environ.get("STOCKS_TRUSTED_PROXY_IPS", "").strip()


# Open /api/stream connections never finish on their own; without a bound,
# uvicorn would wait for them on stop and the lifespan's WAL checkpoint with it.
GRACEFUL_SHUTDOWN_SECONDS = 5


def _uvicorn_command(args) -> list[str]:
    cmd = [
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", args.host, "--port", str(args.port),
        "--timeout-graceful-shutdown", str(GRACEFUL_SHUTDOWN_SECONDS),
    ]
    # Empty would parse as a single trusted "" host, so only pass it when set.
    if _trusted_proxies():
        cmd += ["--forwarded-allow-ips", _trusted_proxies()]
    if args.reload:
        # --reload-dir keeps the watcher off .venv and the SQLite files.
        cmd += ["--reload", "--reload-dir", "app"]
    return cmd


def _setup_logging(log_dir: Path) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_dir / "supervisor.log", encoding="utf-8"))
    except OSError:
        pass  # console-only is better than refusing to start
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s supervisor: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--max-restarts", type=int, default=10)
    parser.add_argument(
        "--no-supervise", action="store_true",
        help="run uvicorn in this process, without the restart loop "
             "(what the Windows service uses — see windows/README.md)")
    args = parser.parse_args()

    # Run from backend/ so the relative default STOCKS_DB_PATH resolves the same
    # way it does when uvicorn is started by hand.
    os.chdir(Path(__file__).resolve().parent)

    log_dir = Path(os.environ.get("STOCKS_LOG_DIR") or (Path.cwd() / "logs"))
    _setup_logging(log_dir)

    if args.no_supervise:
        # In-process on purpose, not a child. Under a service manager the stop
        # signal is delivered to *this* pid; with uvicorn in a subprocess it
        # would never reach the server, and app.main's lifespan shutdown — which
        # ends with PRAGMA wal_checkpoint(TRUNCATE) on both connections — would
        # be skipped on every stop. Reload is a dev-only flag and needs the
        # subprocess form, so it keeps the old path.
        if args.reload:
            return subprocess.call(_uvicorn_command(args))
        import uvicorn
        kwargs = {"timeout_graceful_shutdown": GRACEFUL_SHUTDOWN_SECONDS}
        if _trusted_proxies():
            kwargs["forwarded_allow_ips"] = _trusted_proxies()
        logger.info("starting uvicorn in-process on %s:%s", args.host, args.port)
        uvicorn.run("app.main:app", host=args.host, port=args.port, **kwargs)
        return 0

    cmd = _uvicorn_command(args)

    restarts = 0
    while True:
        started = time.monotonic()
        logger.info("starting: %s", " ".join(cmd))
        try:
            code = subprocess.call(cmd)
        except KeyboardInterrupt:
            logger.info("interrupted; shutting down")
            return 0
        uptime = time.monotonic() - started

        if code == 0:
            logger.info("exited cleanly after %.1fs", uptime)
            return 0

        # A long-running process that dies gets a fresh restart budget; one that
        # dies immediately is almost certainly misconfigured, and retrying it
        # ten times in a row just fills the log.
        if uptime >= FAST_FAIL_SECONDS:
            restarts = 0
        logger.error("exited with code %s after %.1fs", code, uptime)

        if restarts >= args.max_restarts:
            logger.critical(
                "giving up after %s consecutive fast failures — see backend.log",
                restarts)
            return code

        delay = BACKOFF_SECONDS[min(restarts, len(BACKOFF_SECONDS) - 1)]
        restarts += 1
        logger.info("restarting in %ss (attempt %s)", delay, restarts)
        try:
            time.sleep(delay)
        except KeyboardInterrupt:
            return 0


if __name__ == "__main__":
    sys.exit(main())
