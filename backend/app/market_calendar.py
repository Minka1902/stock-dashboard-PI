"""Trading-day calendars and clock-based session status — NYSE and TASE.

No external dependency at runtime: both calendars are small explicit tables.

NYSE: weekends plus a hardcoded set of observed holidays / half days, with
pre-market (04:00) and after-hours (to 20:00) sessions around 09:30–16:00 ET.

TASE (Tel Aviv, Asia/Jerusalem): **Monday–Friday since 2026-01-05** (before
that the week was Sunday–Thursday). Sessions, per TASE's announcement as
reported by Globes: Mon–Thu 10:00–17:35, Fri 10:00–13:50 (derivatives to
14:00). Verified against Yahoo intraday bars for TA35.TA / TEVA.TA through
2026-09: the last 5-minute bar starts 17:25 Mon–Thu and 13:45 on Fridays.
TASE has no pre-market or after-hours trading, so its status is only ever
LIVE or CLOSED.

TASE holidays follow the Hebrew calendar. The closure and early-close dates
below are copied from exchange_calendars 4.13.2's XTAE calendar (a maintained
source, which already models the Mon–Fri week; its 17:15 Mon–Thu close is the
pre-2026 hour, so only its *dates* are used). The 2026 list was cross-checked
against the days Yahoo has no TA35.TA/TEVA.TA bars — all 14 closures through
2026-09-25 match. ASSUMPTION: 2027–2028 dates are XTAE's rule-derived
projections; TASE publishes the official list each year — re-check them
against the TASE vacation schedule when it does, and extend the tables.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# Observed NYSE full-day closures (New Year's, MLK, Presidents', Good Friday,
# Memorial Day, Juneteenth, Independence Day, Labor Day, Thanksgiving, Christmas).
_HOLIDAYS: set[str] = {
    # 2025
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}

# Early-close sessions (regular close 13:00 ET instead of 16:00): typically the
# day before Independence Day, the day after Thanksgiving, and Christmas Eve.
_HALF_DAYS: set[str] = {
    "2025-07-03", "2025-11-28", "2025-12-24",
    "2026-11-27", "2026-12-24",
    "2027-11-26", "2027-12-23",
}

# Session boundaries in minutes-from-midnight, US Eastern.
_PRE_OPEN = 4 * 60          # 04:00 pre-market open
_REGULAR_OPEN = 9 * 60 + 30  # 09:30 regular open
_REGULAR_CLOSE = 16 * 60     # 16:00 regular close (13:00 on half days)
_HALF_CLOSE = 13 * 60
_POST_CLOSE = 20 * 60        # 20:00 after-hours close


# ---------- TASE ----------

_TASE_TZ = ZoneInfo("Asia/Jerusalem")
_TASE_WEEK_SWITCH = date(2026, 1, 5)   # first Monday of the Mon–Fri week

# Full-day closures on what would otherwise be trading days (XTAE, see above).
_TASE_HOLIDAYS: set[str] = {
    # 2026 — verified against observed Yahoo closures through 2026-09-25
    "2026-03-03", "2026-04-01", "2026-04-02", "2026-04-07", "2026-04-08",
    "2026-04-21", "2026-04-22", "2026-05-21", "2026-05-22", "2026-07-23",
    "2026-09-11", "2026-09-18", "2026-09-21", "2026-09-25", "2026-10-02",
    # 2027 (projected — confirm against TASE's published schedule)
    "2027-03-23", "2027-04-21", "2027-04-22", "2027-04-27", "2027-04-28",
    "2027-05-11", "2027-05-12", "2027-06-10", "2027-06-11", "2027-08-12",
    "2027-10-01", "2027-10-08", "2027-10-11", "2027-10-15", "2027-10-22",
    # 2028 (projected)
    "2028-04-10", "2028-04-11", "2028-04-17", "2028-05-01", "2028-05-02",
    "2028-05-30", "2028-05-31", "2028-08-01", "2028-09-20", "2028-09-21",
    "2028-09-22", "2028-09-29", "2028-10-04", "2028-10-05", "2028-10-11",
    "2028-10-12",
}

# Chol HaMoed (intermediate festival days): trading closes at 14:15.
_TASE_EARLY_CLOSE: set[str] = {
    "2026-04-06", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01",
    "2027-04-26", "2027-10-18", "2027-10-19", "2027-10-20", "2027-10-21",
    "2028-04-12", "2028-04-13", "2028-10-09", "2028-10-10",
}

_TASE_OPEN = 10 * 60               # 10:00
_TASE_CLOSE = 17 * 60 + 35         # 17:35 Mon–Thu (incl. closing auction)
_TASE_FRIDAY_CLOSE = 13 * 60 + 50  # 13:50 Fri (equities)
_TASE_EARLY = 14 * 60 + 15         # 14:15 on Chol HaMoed
_TASE_SUNDAY_CLOSE = 15 * 60 + 40  # 15:40 Sundays, pre-2026 week only

EXCHANGES = ("NYSE", "TASE")


def exchange_for_ticker(ticker: str) -> str | None:
    """"TASE" for Tel Aviv (".TA") listings, "NYSE" for US symbols (the US
    session clock covers Nasdaq too), None for FX pairs, which trade ~24/5."""
    t = str(ticker or "").strip().upper()
    if t.endswith("=X"):
        return None
    if t.endswith(".TA"):
        return "TASE"
    return "NYSE"


def _tase_weekday(d: date) -> bool:
    if d >= _TASE_WEEK_SWITCH:
        return d.weekday() <= 4            # Mon–Fri
    return d.weekday() in (6, 0, 1, 2, 3)  # Sun–Thu (the old week)


def _tase_is_trading_day(d: date) -> bool:
    return _tase_weekday(d) and d.isoformat() not in _TASE_HOLIDAYS


def _tase_close_minutes(d: date) -> int:
    if d.isoformat() in _TASE_EARLY_CLOSE:
        return _TASE_EARLY
    if d.weekday() == 4:
        return _TASE_FRIDAY_CLOSE
    if d.weekday() == 6:
        return _TASE_SUNDAY_CLOSE
    return _TASE_CLOSE


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


# ---------- NYSE ----------

def _nyse_is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        return False
    return d.isoformat() not in _HOLIDAYS


def is_trading_day(d: date, exchange: str = "NYSE") -> bool:
    """True if `d` is a regular trading day on `exchange` ("NYSE" | "TASE")."""
    if exchange == "TASE":
        return _tase_is_trading_day(d)
    return _nyse_is_trading_day(d)


def session_hours(d: date, exchange: str = "NYSE") -> tuple[str, str] | None:
    """Regular session ("HH:MM", "HH:MM") in the exchange's local time, or
    None when `d` is not a trading day there."""
    if not is_trading_day(d, exchange):
        return None
    if exchange == "TASE":
        return _hhmm(_TASE_OPEN), _hhmm(_tase_close_minutes(d))
    close = _HALF_CLOSE if d.isoformat() in _HALF_DAYS else _REGULAR_CLOSE
    return _hhmm(_REGULAR_OPEN), _hhmm(close)


def _is_us_eastern_dst(d: date) -> bool:
    """US DST runs 2nd Sunday of March → 1st Sunday of November.

    Transitions happen at 02:00 local, but the market is always closed then, so
    treating the whole transition day by its post-2am offset is exact for every
    session boundary we care about.
    """
    march = date(d.year, 3, 1)
    second_sunday_march = march + timedelta(days=(6 - march.weekday()) % 7 + 7)
    nov = date(d.year, 11, 1)
    first_sunday_nov = nov + timedelta(days=(6 - nov.weekday()) % 7)
    return second_sunday_march <= d < first_sunday_nov


def _to_eastern(now: datetime) -> datetime:
    """Convert an aware (or assumed-UTC) datetime to US Eastern wall time."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    utc = now.astimezone(timezone.utc)
    offset = -4 if _is_us_eastern_dst(utc.date()) else -5
    return utc.astimezone(timezone(timedelta(hours=offset)))


def _tase_status(now: datetime) -> str:
    local = now.astimezone(_TASE_TZ)
    d = local.date()
    if not _tase_is_trading_day(d):
        return "CLOSED"
    minutes = local.hour * 60 + local.minute
    return "LIVE" if _TASE_OPEN <= minutes < _tase_close_minutes(d) else "CLOSED"


def market_status(now: datetime | None = None, exchange: str = "NYSE") -> str:
    """Clock-based session for `exchange` at `now` (default: current UTC time).

    Returns one of PRE | LIVE | POST | CLOSED, matching the per-quote
    `market_state` enum so the frontend can treat either as authoritative.
    TASE has no extended session, so it is only ever LIVE or CLOSED.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if exchange == "TASE":
        return _tase_status(now)
    et = _to_eastern(now)
    if not is_trading_day(et.date()):
        return "CLOSED"
    minutes = et.hour * 60 + et.minute
    close = _HALF_CLOSE if et.date().isoformat() in _HALF_DAYS else _REGULAR_CLOSE
    if _PRE_OPEN <= minutes < _REGULAR_OPEN:
        return "PRE"
    if _REGULAR_OPEN <= minutes < close:
        return "LIVE"
    if close <= minutes < _POST_CLOSE:
        return "POST"
    return "CLOSED"


def market_statuses(now: datetime | None = None) -> dict[str, str]:
    """{exchange: status} for every modelled exchange at `now`."""
    return {ex: market_status(now, ex) for ex in EXCHANGES}


def next_trading_day(after: date | None = None, exchange: str = "NYSE") -> date:
    """The next trading day on `exchange` strictly after `after` (default today)."""
    if after is None:
        after = date.today()
    d = after + timedelta(days=1)
    while not is_trading_day(d, exchange):
        d += timedelta(days=1)
    return d


def last_closed_session(now: datetime | None = None) -> date:
    """The most recent NYSE trading day whose regular session has ended at
    `now` — the date a closing snapshot belongs to, whenever it actually runs."""
    if now is None:
        now = datetime.now(timezone.utc)
    et = _to_eastern(now)
    d = et.date()
    close = _HALF_CLOSE if d.isoformat() in _HALF_DAYS else _REGULAR_CLOSE
    if is_trading_day(d) and et.hour * 60 + et.minute >= close:
        return d
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d
