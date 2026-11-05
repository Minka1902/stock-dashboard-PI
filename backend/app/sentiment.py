"""Market-wide sentiment summary — contrarian signals from 5 indicators.

Pure DB read + threshold logic; no external calls. Signals:
  BUY / SELL / NEUTRAL          — contrarian read (extremes are entry/exit points)
  ALERT                          — VIX above the caution line, move underway
  EXTREME                        — VIX at a historical turning point
  NO_DATA                        — indicator has nothing stored yet
"""
from datetime import datetime, timezone

from app import config, db
from app.sources import margin_debt as margin_debt_source


def _fg_signal(score: float) -> str:
    if score <= config.SENT_FG_BUY:
        return "BUY"
    if score >= config.SENT_FG_SELL:
        return "SELL"
    return "NEUTRAL"


def _vix_signal(close: float) -> str:
    if close >= config.SENT_VIX_EXTREME:
        return "EXTREME"
    if close >= config.SENT_VIX_ALERT:
        return "ALERT"
    return "NEUTRAL"


def _aaii_signal(bullish: float, bearish: float) -> str:
    if bearish >= config.SENT_AAII_EXTREME_PCT or bearish - bullish >= config.SENT_AAII_SPREAD:
        return "BUY"
    if bullish >= config.SENT_AAII_EXTREME_PCT or bullish - bearish >= config.SENT_AAII_SPREAD:
        return "SELL"
    return "NEUTRAL"


def _margin_signal(yoy_pct: float) -> str:
    if yoy_pct >= config.SENT_MARGIN_EXTREME:
        return "EXTREME"
    if yoy_pct >= config.SENT_MARGIN_SELL:
        return "SELL"
    if yoy_pct <= config.SENT_MARGIN_BUY:
        return "BUY"
    return "NEUTRAL"


def _pc_signal(ratio: float) -> str:
    if ratio >= config.SENT_PC_BUY:
        return "BUY"
    if ratio <= config.SENT_PC_SELL:
        return "SELL"
    return "NEUTRAL"


_NO_DATA = {"value": None, "as_of": None, "signal": "NO_DATA"}


def build_summary(conn) -> dict:
    indicators: dict[str, dict] = {}

    fg = db.get_fear_greed(conn, days=30)
    if fg:
        latest = fg[-1]
        indicators["fear_greed"] = {
            "value": latest.score,
            "rating": latest.rating,
            "as_of": latest.captured_at,
            "signal": _fg_signal(latest.score),
        }
    else:
        indicators["fear_greed"] = {**_NO_DATA, "rating": None}

    vix_points = db.get_vix(conn, days=14)
    if vix_points:
        latest = vix_points[-1]
        closes = db.get_latest_vix_closes(conn, n=2)
        crossed_19 = (
            len(closes) == 2
            and closes[0] < config.SENT_VIX_ALERT <= closes[1]
        )
        indicators["vix"] = {
            "value": latest.close,
            "as_of": latest.date,
            "signal": _vix_signal(latest.close),
            "crossed_19": crossed_19,
        }
    else:
        indicators["vix"] = {**_NO_DATA, "crossed_19": False}

    aaii = db.get_latest_aaii(conn)
    if aaii:
        indicators["aaii"] = {
            "value": {"bullish": aaii.bullish, "neutral": aaii.neutral, "bearish": aaii.bearish},
            "as_of": aaii.week_ending,
            "signal": _aaii_signal(aaii.bullish, aaii.bearish),
        }
    else:
        indicators["aaii"] = dict(_NO_DATA)

    pc = db.get_latest_put_call(conn)
    if pc:
        indicators["put_call"] = {
            "value": pc.ratio,
            "as_of": pc.date,
            "signal": _pc_signal(pc.ratio),
        }
    else:
        indicators["put_call"] = dict(_NO_DATA)

    md_series = margin_debt_source.compute_yoy(db.get_margin_debt(conn))
    md_latest = next((r for r in reversed(md_series) if r["yoy_pct"] is not None), None)
    if md_latest:
        indicators["margin_debt"] = {
            "value": md_latest["yoy_pct"],
            "debit_balances": md_latest["debit_balances"],
            "as_of": md_latest["month"],
            "signal": _margin_signal(md_latest["yoy_pct"]),
        }
    else:
        indicators["margin_debt"] = {**_NO_DATA, "debit_balances": None}

    buy_count = sum(1 for i in indicators.values() if i["signal"] == "BUY")
    sell_count = sum(1 for i in indicators.values() if i["signal"] == "SELL")
    if buy_count >= 2 and buy_count > sell_count:
        lean = "BUY"
    elif sell_count >= 2 and sell_count > buy_count:
        lean = "SELL"
    else:
        lean = "NEUTRAL"

    return {
        "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "indicators": indicators,
        "overall": {"buy_count": buy_count, "sell_count": sell_count, "lean": lean},
    }
