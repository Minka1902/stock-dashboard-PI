from datetime import date, datetime, timezone

from app.market_calendar import is_trading_day, market_status, next_trading_day


def test_weekend_is_not_a_trading_day():
    assert is_trading_day(date(2026, 1, 3)) is False  # Saturday
    assert is_trading_day(date(2026, 1, 4)) is False  # Sunday


def test_holiday_is_not_a_trading_day():
    assert is_trading_day(date(2026, 1, 1)) is False  # New Year's Day (Thu)
    assert is_trading_day(date(2026, 7, 3)) is False   # Independence Day observed


def test_normal_weekday_is_a_trading_day():
    assert is_trading_day(date(2026, 1, 2)) is True   # Friday
    assert is_trading_day(date(2026, 1, 5)) is True   # Monday


def test_next_trading_day_skips_weekend():
    # Friday Jan 2 -> Monday Jan 5 (Jan 3/4 are the weekend).
    assert next_trading_day(date(2026, 1, 2)) == date(2026, 1, 5)


def test_next_trading_day_skips_holiday():
    # Wed Dec 31 2025 -> Jan 1 2026 is a holiday -> Fri Jan 2.
    assert next_trading_day(date(2025, 12, 31)) == date(2026, 1, 2)


# ---- clock-based session status (market_status) ----
# All inputs are UTC; summer dates are EDT (UTC-4), winter dates EST (UTC-5).

def _utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_premarket_open_boundary():
    # 2025-07-07 Monday (EDT): 04:00 ET = 08:00 UTC opens pre-market.
    assert market_status(_utc(2025, 7, 7, 8, 0)) == "PRE"
    assert market_status(_utc(2025, 7, 7, 7, 59)) == "CLOSED"


def test_regular_open_boundary():
    # 09:29 ET → PRE, 09:30 ET (13:30 UTC) → LIVE.
    assert market_status(_utc(2025, 7, 7, 13, 29)) == "PRE"
    assert market_status(_utc(2025, 7, 7, 13, 30)) == "LIVE"


def test_regular_close_boundary():
    # 15:59 ET → LIVE, 16:00 ET (20:00 UTC) → POST.
    assert market_status(_utc(2025, 7, 7, 19, 59)) == "LIVE"
    assert market_status(_utc(2025, 7, 7, 20, 0)) == "POST"


def test_post_close_boundary():
    # 19:59 ET → POST, 20:00 ET (00:00 UTC next day) → CLOSED.
    assert market_status(_utc(2025, 7, 7, 23, 59)) == "POST"
    assert market_status(_utc(2025, 7, 8, 0, 0)) == "CLOSED"


def test_regular_open_in_winter_est():
    # 2026-01-05 Monday (EST): 09:30 ET = 14:30 UTC → LIVE.
    assert market_status(_utc(2026, 1, 5, 14, 29)) == "PRE"
    assert market_status(_utc(2026, 1, 5, 14, 30)) == "LIVE"


def test_holiday_is_closed_midday():
    # 2025-07-04 Independence Day, noon ET = 16:00 UTC.
    assert market_status(_utc(2025, 7, 4, 16, 0)) == "CLOSED"


def test_weekend_is_closed_midday():
    # 2025-07-05 Saturday, noon ET = 16:00 UTC.
    assert market_status(_utc(2025, 7, 5, 16, 0)) == "CLOSED"


def test_half_day_early_close():
    # 2025-07-03 half day: 12:59 ET → LIVE, 13:00 ET (17:00 UTC) → POST.
    assert market_status(_utc(2025, 7, 3, 16, 59)) == "LIVE"
    assert market_status(_utc(2025, 7, 3, 17, 0)) == "POST"


# ---- TASE (Tel Aviv) — Mon–Fri since 2026-01-05, no extended session ----
# Israel is UTC+2 in winter (IST) and UTC+3 in summer (IDT).
from app.market_calendar import (  # noqa: E402
    exchange_for_ticker, session_hours, market_statuses,
)


def test_exchange_for_ticker():
    assert exchange_for_ticker("TEVA.TA") == "TASE"
    assert exchange_for_ticker("teva.ta") == "TASE"
    assert exchange_for_ticker("AAPL") == "NYSE"
    assert exchange_for_ticker("USDILS=X") is None


def test_tase_trades_monday_to_friday_from_2026():
    assert is_trading_day(date(2026, 9, 14), "TASE") is True    # Monday
    assert is_trading_day(date(2026, 9, 4), "TASE") is True     # Friday
    assert is_trading_day(date(2026, 9, 6), "TASE") is False    # Sunday
    assert is_trading_day(date(2026, 9, 5), "TASE") is False    # Saturday


def test_tase_before_the_switch_traded_sunday_to_thursday():
    assert is_trading_day(date(2025, 12, 28), "TASE") is True   # Sunday
    assert is_trading_day(date(2025, 12, 26), "TASE") is False  # Friday


def test_tase_holidays_2026_match_observed_closures():
    for d in [date(2026, 3, 3),    # Purim
              date(2026, 4, 1),    # Passover eve
              date(2026, 4, 22),   # Independence Day
              date(2026, 7, 23),   # Tisha B'Av
              date(2026, 9, 11),   # Rosh Hashanah eve (Friday)
              date(2026, 9, 21),   # Yom Kippur
              date(2026, 10, 2)]:  # Shemini Atzeret eve (Friday)
        assert is_trading_day(d, "TASE") is False, d
    # NYSE is unaffected by Israeli holidays, and vice versa.
    assert is_trading_day(date(2026, 9, 21), "NYSE") is True
    assert is_trading_day(date(2026, 7, 3), "TASE") is True     # US holiday


def test_tase_session_hours_monday_to_thursday_and_friday():
    assert session_hours(date(2026, 9, 14), "TASE") == ("10:00", "17:35")  # Monday
    assert session_hours(date(2026, 9, 4), "TASE") == ("10:00", "13:50")   # Friday
    assert session_hours(date(2026, 9, 28), "TASE") == ("10:00", "14:15")  # Chol HaMoed Sukkot
    assert session_hours(date(2026, 9, 21), "TASE") is None                # Yom Kippur


def test_tase_status_is_live_or_closed_never_pre_or_post():
    # Mon 2026-09-14, IDT (UTC+3): 10:00 local = 07:00 UTC.
    assert market_status(_utc(2026, 9, 14, 6, 59), "TASE") == "CLOSED"
    assert market_status(_utc(2026, 9, 14, 7, 0), "TASE") == "LIVE"
    assert market_status(_utc(2026, 9, 14, 14, 34), "TASE") == "LIVE"    # 17:34
    assert market_status(_utc(2026, 9, 14, 14, 35), "TASE") == "CLOSED"  # 17:35


def test_tase_friday_short_session():
    # Fri 2026-09-04, IDT: 13:49 local LIVE, 13:50 CLOSED.
    assert market_status(_utc(2026, 9, 4, 10, 49), "TASE") == "LIVE"
    assert market_status(_utc(2026, 9, 4, 10, 50), "TASE") == "CLOSED"


def test_tase_winter_offset():
    # Mon 2026-01-12, IST (UTC+2): 10:00 local = 08:00 UTC.
    assert market_status(_utc(2026, 1, 12, 7, 59), "TASE") == "CLOSED"
    assert market_status(_utc(2026, 1, 12, 8, 0), "TASE") == "LIVE"


def test_tase_holiday_is_closed_midday():
    assert market_status(_utc(2026, 9, 21, 9, 0), "TASE") == "CLOSED"


def test_next_trading_day_tase_skips_friday_holiday_and_weekend():
    # Thu 2026-09-24 -> Fri 09-25 is Sukkot eve (closed) -> Mon 09-28.
    assert next_trading_day(date(2026, 9, 24), "TASE") == date(2026, 9, 28)


def test_market_statuses_reports_both_exchanges():
    got = market_statuses(_utc(2026, 9, 14, 8, 0))  # 11:00 Israel, 04:00 ET
    assert got == {"NYSE": "PRE", "TASE": "LIVE"}
