"""Analyst ratings and earnings dates from Yahoo Finance quoteSummary."""
from datetime import datetime, timezone

from app.models import AnalystSignal
from app.sources import yahoo

_YF_URL = (
    "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
    "?modules=calendarEvents,upgradeDowngradeHistory,recommendationTrend"
)
_HEADERS = yahoo.HEADERS
_UPGRADE_ACTIONS = {"up", "init", "reit"}  # positive analyst actions
_DOWNGRADE_ACTIONS = {"down"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_iso_date(ts) -> str | None:
    """Convert Unix timestamp (seconds) to ISO date string."""
    try:
        return datetime.utcfromtimestamp(int(ts)).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def parse_response(payload: dict, ticker: str, fetched_at: str) -> AnalystSignal | None:
    try:
        result = payload["quoteSummary"]["result"][0]
    except (KeyError, IndexError, TypeError):
        return None

    # --- next earnings date ---
    next_earnings: str | None = None
    try:
        dates = result["calendarEvents"]["earnings"]["earningsDate"]
        for entry in dates:
            raw_ts = entry.get("raw")
            iso = _to_iso_date(raw_ts)
            if iso and iso >= datetime.now(timezone.utc).date().isoformat():
                next_earnings = iso
                break
    except (KeyError, TypeError):
        pass

    # --- upgrade/downgrade history (last 30 days) ---
    recent_upgrades = 0
    recent_downgrades = 0
    latest_action: str | None = None
    latest_firm: str | None = None
    latest_to_grade: str | None = None
    cutoff_ts = datetime.now(timezone.utc).timestamp() - 30 * 86400

    try:
        history = result["upgradeDowngradeHistory"]["history"] or []
        sorted_history = sorted(
            history, key=lambda h: h.get("epochGradeDate", 0), reverse=True
        )
        for entry in sorted_history:
            epoch = entry.get("epochGradeDate", 0)
            action = (entry.get("action") or "").lower()
            if epoch >= cutoff_ts:
                if action in _UPGRADE_ACTIONS:
                    recent_upgrades += 1
                elif action in _DOWNGRADE_ACTIONS:
                    recent_downgrades += 1
        # latest action from the most recent entry (regardless of 30-day window)
        if sorted_history:
            top = sorted_history[0]
            latest_action = (top.get("action") or "").lower() or None
            latest_firm = top.get("firm") or None
            latest_to_grade = top.get("toGrade") or None
    except (KeyError, TypeError):
        pass

    # --- recommendation trend (current period "0m") ---
    rec_strong_buy = rec_buy = rec_hold = rec_sell = None
    try:
        trends = result["recommendationTrend"]["trend"] or []
        for trend in trends:
            if trend.get("period") == "0m":
                rec_strong_buy = trend.get("strongBuy")
                rec_buy = trend.get("buy")
                rec_hold = trend.get("hold")
                rec_sell = trend.get("sell")
                break
    except (KeyError, TypeError):
        pass

    return AnalystSignal(
        ticker=ticker,
        fetched_at=fetched_at,
        next_earnings=next_earnings,
        rec_strong_buy=rec_strong_buy,
        rec_buy=rec_buy,
        rec_hold=rec_hold,
        rec_sell=rec_sell,
        recent_upgrades=recent_upgrades,
        recent_downgrades=recent_downgrades,
        latest_action=latest_action,
        latest_firm=latest_firm,
        latest_to_grade=latest_to_grade,
    )


def fetch(tickers: list[str]) -> list[AnalystSignal]:
    fetched_at = _now_iso()
    results: list[AnalystSignal] = []
    with yahoo.client(timeout=15) as client:
        crumb = yahoo.get_crumb(client)
        for ticker in tickers:
            try:
                resp = client.get(yahoo.with_crumb(_YF_URL.format(ticker=ticker), crumb))
                resp.raise_for_status()
                record = parse_response(resp.json(), ticker, fetched_at)
                if record is not None:
                    results.append(record)
            except Exception:
                continue
    # Every ticker failing is a broken source, not a successful empty run.
    if tickers and not results:
        raise RuntimeError(f"no analyst signals returned for any of {len(tickers)} tickers")
    return results
