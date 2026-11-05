"""Per-source schedules: validation, APScheduler triggers and next-due maths.

Pure (no SQLite, no scheduler instance) so it is unit-tested directly; the DB
rows live in app/db.py and the job wiring in app/main.py.

A schedule is the *only* thing that decides when a source runs. There is no
second throttle inside ingest.run_source any more, so a scheduled run always
fetches, and every outcome is visible: ok, error (retried after
`retry_after_seconds`), or deferred (the source named its own next attempt).
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.models import SourceSchedule, SourceStatus

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MODES = ("interval", "times")

# boom_score reads every other source, alerts diffs boom_score, regime reads
# the sentiment sources, automute measures the stored components and rules
# evaluates the saved rules on stored bars, so they
# run as one ordered step with a single schedule row of its own.
DERIVED = "derived"
DERIVED_MEMBERS = ("boom_score", "alerts", "regime", "automute", "rules")

MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 60 * 86400
MAX_TIMES = 24
# Retry after an error for an "at times" source with no explicit retry. An
# interval source retries on its own interval unless told otherwise.
DEFAULT_TIMES_RETRY_SECONDS = 1800


def parse_hhmm(value: str) -> tuple[int, int]:
    """Validate "HH:MM" (24h) and return (hour, minute). Raises ValueError."""
    parts = str(value).strip().split(":")
    if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
        raise ValueError("time must be HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("time must be HH:MM in 24h range")
    return hour, minute


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception as exc:  # ZoneInfoNotFoundError, ValueError on odd input
        raise ValueError(f"unknown timezone: {name!r}") from exc


def validate(s: SourceSchedule) -> SourceSchedule:
    """Return a canonical copy of `s` or raise ValueError saying what is wrong."""
    if s.mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    _zone(s.tz)

    days = []
    for d in s.days:
        key = str(d).strip().lower()[:3]
        if key not in DAYS:
            raise ValueError(f"unknown day: {d!r}")
        if key not in days:
            days.append(key)
    if not days:
        raise ValueError("pick at least one day")
    days.sort(key=DAYS.index)

    interval = s.interval_seconds
    if s.mode == "interval":
        if interval is None:
            raise ValueError("interval mode needs interval_seconds")
        interval = int(interval)
        if interval < MIN_INTERVAL_SECONDS:
            raise ValueError(f"interval must be at least {MIN_INTERVAL_SECONDS} seconds")
        if interval > MAX_INTERVAL_SECONDS:
            raise ValueError(f"interval must be at most {MAX_INTERVAL_SECONDS // 86400} days")

    times = sorted({"%02d:%02d" % parse_hhmm(t) for t in s.times})
    if s.mode == "times" and not times:
        raise ValueError("at-times mode needs at least one HH:MM time")
    if len(times) > MAX_TIMES:
        raise ValueError(f"at most {MAX_TIMES} times per day")

    retry = s.retry_seconds
    if retry is not None:
        retry = int(retry)
        if retry < MIN_INTERVAL_SECONDS:
            raise ValueError(f"retry must be at least {MIN_INTERVAL_SECONDS} seconds")

    return s.model_copy(update={
        "days": days, "times": times, "interval_seconds": interval, "retry_seconds": retry,
    })


# Sources whose default is a wall-clock slot rather than an interval.
# margin_debt: FINRA publishes monthly; one headless-browser fetch a week
# (Monday 06:00 Israel time) is plenty and keeps Chromium launches rare.
# equity_snapshot: closing values, after the US close (TASE closes earlier).
SEED_TIMES = {
    "margin_debt": {"times": ["06:00"], "days": ["mon"], "tz": "Asia/Jerusalem"},
    "equity_snapshot": {"times": ["16:15"], "days": ["mon", "tue", "wed", "thu", "fri"],
                        "tz": "America/New_York"},
}


def defaults_from_specs(specs: dict, refresh_seconds: int, tz: str) -> list[SourceSchedule]:
    """Seed rows from the registry: each source's old min_interval becomes its
    interval (the fast default otherwise) and retry_interval its retry.
    SEED_TIMES entries seed an at-times row instead."""
    out = []
    for name, spec in specs.items():
        if name in DERIVED_MEMBERS:
            continue
        if name in SEED_TIMES:
            out.append(SourceSchedule(
                source=name, mode="times", retry_seconds=spec.retry_interval,
                interval_seconds=max(MIN_INTERVAL_SECONDS, int(spec.min_interval or refresh_seconds)),
                **SEED_TIMES[name],
            ))
            continue
        out.append(SourceSchedule(
            source=name, mode="interval",
            interval_seconds=max(MIN_INTERVAL_SECONDS, int(spec.min_interval or refresh_seconds)),
            retry_seconds=spec.retry_interval, tz=tz,
        ))
    out.append(SourceSchedule(
        source=DERIVED, mode="interval",
        interval_seconds=max(MIN_INTERVAL_SECONDS, int(refresh_seconds)), tz=tz,
    ))
    return out


class DaysFilterTrigger(BaseTrigger):
    """Wrap a trigger so it only fires on the given weekdays (in `tz`).

    Used for "every N minutes, weekdays only". A fire time that lands on an
    excluded day moves to the first inner fire time of the next allowed day.
    """

    def __init__(self, inner: BaseTrigger, days: list[str], tz: ZoneInfo):
        self.inner = inner
        self.days = frozenset(DAYS.index(d) for d in days)
        self.tz = tz

    def get_next_fire_time(self, previous_fire_time, now):
        nxt = self.inner.get_next_fire_time(previous_fire_time, now)
        for _ in range(14):  # at most a week of excluded days (plus DST slack)
            if nxt is None or nxt.astimezone(self.tz).weekday() in self.days:
                return nxt
            local = nxt.astimezone(self.tz)
            next_day = datetime.combine(local.date() + timedelta(days=1), time(0), tzinfo=self.tz)
            nxt = self.inner.get_next_fire_time(None, next_day)
        return None

    def __str__(self):
        names = ",".join(DAYS[i] for i in sorted(self.days))
        return f"{self.inner} on {names}"

    def __repr__(self):
        return f"<DaysFilterTrigger ({self})>"


def build_trigger(s: SourceSchedule) -> BaseTrigger:
    tz = _zone(s.tz)
    if s.mode == "interval":
        # A fixed epoch (a Monday, local midnight) keeps the grid deterministic:
        # "every day" lands on midnight, not on whatever second it was built.
        trig = IntervalTrigger(seconds=int(s.interval_seconds), timezone=tz,
                               start_date=datetime(2000, 1, 3, tzinfo=tz))
        if set(s.days) == set(DAYS):
            return trig
        return DaysFilterTrigger(trig, s.days, tz)
    dow = ",".join(s.days)
    crons = [
        CronTrigger(day_of_week=dow, hour=h, minute=m, timezone=tz)
        for h, m in (parse_hhmm(t) for t in s.times)
    ]
    return crons[0] if len(crons) == 1 else OrTrigger(crons)


def retry_after_seconds(s: SourceSchedule) -> int:
    if s.retry_seconds:
        return int(s.retry_seconds)
    if s.mode == "interval" and s.interval_seconds:
        return int(s.interval_seconds)
    return DEFAULT_TIMES_RETRY_SECONDS


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _align_to_days(when: datetime, s: SourceSchedule) -> datetime:
    """Move an interval-mode due time off an excluded day."""
    if set(s.days) == set(DAYS):
        return when
    tz = _zone(s.tz)
    allowed = {DAYS.index(d) for d in s.days}
    local = when.astimezone(tz)
    for _ in range(8):
        if local.weekday() in allowed:
            return local.astimezone(when.tzinfo or timezone.utc)
        local = datetime.combine(local.date() + timedelta(days=1), time(0), tzinfo=tz)
    return when


def next_due(s: SourceSchedule, status: SourceStatus | None, now: datetime) -> datetime:
    """When this source should next run, given how its last run ended.

    - never run                -> now
    - deferred                 -> the next attempt the source asked for
    - error                    -> last attempt + retry
    - ok, interval mode        -> last success + interval
    - ok, at-times mode        -> first scheduled time after the last success
    Anything already in the past is due *now*: a run missed while the machine
    was off (or the pool was busy) happens late rather than never.
    """
    last_try = _parse(status.last_refreshed_at) if status else None
    if status is None or last_try is None:
        return now

    state = status.status or ""
    if state.startswith("deferred"):
        due = _parse(status.next_attempt_at) or last_try + timedelta(seconds=retry_after_seconds(s))
    elif state.startswith("error"):
        due = last_try + timedelta(seconds=retry_after_seconds(s))
    else:
        anchor = _parse(status.last_success_at) or last_try
        if s.mode == "interval":
            due = anchor + timedelta(seconds=int(s.interval_seconds))
        else:
            due = build_trigger(s).get_next_fire_time(None, anchor) or now

    due = max(due, now)
    return _align_to_days(due, s) if s.mode == "interval" else due


def _fmt_seconds(n: int) -> str:
    if n % 86400 == 0:
        d = n // 86400
        return "day" if d == 1 else f"{d} days"
    if n % 3600 == 0:
        h = n // 3600
        return "hour" if h == 1 else f"{h} h"
    if n % 60 == 0:
        return f"{n // 60} min"
    return f"{n} s"


def _fmt_days(days: list[str]) -> str:
    if set(days) == set(DAYS):
        return ""
    if days == ["mon", "tue", "wed", "thu", "fri"]:
        return " Mon–Fri"
    return " " + ", ".join(d.capitalize() for d in days)


def describe(s: SourceSchedule) -> str:
    """"every 3 min", "every day Mon–Fri", "at 06:00, 18:00 Mon (Asia/Jerusalem)"."""
    if s.mode == "interval":
        return f"every {_fmt_seconds(int(s.interval_seconds))}{_fmt_days(s.days)}"
    return f"at {', '.join(s.times)}{_fmt_days(s.days) or ' daily'} ({s.tz})"
