"""Is the Pi online? A cheap TCP probe that tells an outage from a broken source.

While offline, every network source records one `deferred: network offline`
run instead of an error per run (main._run_source_now), and the minute jobs
that need live quotes or Telegram wait. The moment the probe reaches the
network again, main._netwatch re-arms every source deferred for the outage to
run now, so the Pi catches up within a probe interval of the network returning.

Plain TCP connects to IP addresses, no DNS and no HTTP: a DNS-only outage still
reads as online, and its failures stay visible as the sources' own errors.
"""
import socket
import threading
from datetime import datetime, timezone

from app import config

OFFLINE_REASON = "network offline"

_lock = threading.Lock()
_online = True
_changed_at: datetime | None = None


def _reachable(host_port: str, timeout: float) -> bool:
    host, _, port = host_port.rpartition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def probe() -> bool:
    return any(_reachable(h, config.NETCHECK_TIMEOUT_SECONDS) for h in config.NETCHECK_HOSTS)


def is_online() -> bool:
    return _online


def state() -> dict:
    with _lock:
        return {
            "online": _online,
            "since": _changed_at.isoformat(timespec="seconds") if _changed_at else None,
            "hosts": config.NETCHECK_HOSTS,
        }


def check() -> bool | None:
    """Probe once. Returns the new state when it flipped, None when it didn't."""
    global _online, _changed_at
    online = probe()
    with _lock:
        if online == _online:
            return None
        _online, _changed_at = online, datetime.now(timezone.utc)
    return online


def reset() -> None:
    """Back to the startup assumption (online). For tests."""
    global _online, _changed_at
    with _lock:
        _online, _changed_at = True, None
