"""Raspberry Pi health for the Server page: CPU temperature, the firmware's
under-voltage / throttling flags, memory, swap, disk and the last backup.

Each field is what was read, or None when it can't be read (off a Pi, or
without psutil) — never a guessed value. Under-voltage is the most common
cause of a Pi 3 crashing or corrupting its SD card, so it is reported first.
"""
import shutil
from pathlib import Path

from app import backup, config

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is in requirements.txt
    psutil = None

THERMAL_PATH = Path("/sys/class/thermal/thermal_zone0/temp")

# Bits of the firmware's get_throttled word (as `vcgencmd get_throttled`).
THROTTLE_BITS = {
    0: "under-voltage now",
    1: "ARM frequency capped now",
    2: "throttled now",
    3: "soft temperature limit now",
    16: "under-voltage has occurred since boot",
    17: "ARM frequency capping has occurred since boot",
    18: "throttling has occurred since boot",
    19: "soft temperature limit has occurred since boot",
}


def decode_throttled(value: int) -> list[str]:
    return [label for bit, label in THROTTLE_BITS.items() if value >> bit & 1]


def _read_int(path: Path, base: int = 10) -> int | None:
    try:
        return int(path.read_text(encoding="ascii").strip(), base)
    except (OSError, ValueError):
        return None


def _throttled() -> dict | None:
    value = _read_int(Path(config.PI_THROTTLED_PATH), 16)
    if value is None:
        return None
    return {"raw": hex(value), "ok": value == 0, "flags": decode_throttled(value)}


def _cpu_temp_c() -> float | None:
    milli = _read_int(THERMAL_PATH)
    return round(milli / 1000, 1) if milli is not None else None


def _usage(total: int, used: int) -> dict:
    return {"total_bytes": total, "used_bytes": used,
            "percent": round(used / total * 100, 1) if total else None}


def snapshot() -> dict:
    disk = shutil.disk_usage(Path(config.DB_PATH).resolve().parent)
    mem = psutil.virtual_memory() if psutil else None
    swap = psutil.swap_memory() if psutil else None
    return {
        "throttled": _throttled(),
        "cpu_temp_c": _cpu_temp_c(),
        "memory": _usage(mem.total, mem.total - mem.available) if mem else None,
        "swap": _usage(swap.total, swap.used) if swap else None,
        "disk": _usage(disk.total, disk.used),
        "backup": backup.latest(),
    }
