"""Alert detection — fires on notable state changes, runs last in the refresh.

Two families of transition alerts, both diffed against a per-ticker `alert_state`
snapshot from the previous run so each event fires exactly once:

  * Boom Score events — score crossing the threshold, a golden cross newly
    forming, an insider cluster appearing, earnings soon, a large congress buy.
  * Technical-analysis events (the methodology's "warn before falls and before
    breakouts") — approaching support/resistance, confirmed breakouts, false
    breakouts, topping formations and Buy/Sell/Hold recommendation changes.

Every alert carries a deterministic dedup_key, so the 3-minute refresh cadence
never produces duplicates. TA alerts only fire once the previous TA snapshot is
non-null (first pass just records state — no deploy-time alert storm). High
severity alerts are pushed via the existing email/SMS rails.
"""
from datetime import datetime, timezone

from app import config, db, notify
from app.models import Alert
from app.sources.boom_score import _parse_amount_weight

_SEVERITY = {
    "boom_cross": "high",
    "insider_cluster": "high",
    "golden_cross": "medium",
    "earnings_soon": "medium",
    "congress_buy": "medium",
    # TA transition alerts
    "breakdown_warning": "high",
    "breakout_setup": "medium",
    "breakout_confirmed": "high",
    "false_breakout": "high",
    "topping_formation": "high",
    "recommendation_change": "high",   # overridden to medium when new value is "hold"
}


# What each alert type actually means, kept next to the detectors that fire it.
# Lives on the backend on purpose: a copy in the frontend would drift the moment
# a new type is added here, and an alert with no explanation is the failure mode
# this is meant to prevent. `what` states the trigger, `why` says why it's worth
# knowing, `look_at` points at what to check before acting.
ALERT_MEANING = {
    "boom_cross": {
        "what": "The composite Boom Score crossed the alert threshold.",
        "why": "Several independent signals lined up at once rather than a single source moving.",
        "look_at": "The component chips on the Boom Score page — which signals actually fired.",
    },
    "golden_cross": {
        "what": "The 50-day moving average crossed above the 200-day.",
        "why": "A conventional trend-change marker. It is a lagging signal, not a prediction.",
        "look_at": "Whether volume confirms the move, and where the nearest resistance sits.",
    },
    "insider_cluster": {
        "what": "Multiple company insiders bought within a short window (SEC Form 4).",
        "why": "Clustered buying by people with the most context is a stronger tell than one filing.",
        "look_at": "The Insider trades pane: who bought, their role, and the sizes.",
    },
    "earnings_soon": {
        "what": "Earnings are due within the next week.",
        "why": "Earnings can override every other signal, in either direction.",
        "look_at": "Position size and whether you want exposure through the print.",
    },
    "congress_buy": {
        "what": "A member of Congress disclosed a sizeable purchase.",
        "why": "Disclosures lag the trade by up to 45 days, so this is context, not a live signal.",
        "look_at": "The disclosure date versus the transaction date on the Congress page.",
    },
    "breakdown_warning": {
        "what": "Price is approaching a support level that has held before.",
        "why": "The methodology warns before a fall rather than after it.",
        "look_at": "The support levels in Structure, and where your stop sits relative to them.",
    },
    "breakout_setup": {
        "what": "Price is coiling just under a resistance level.",
        "why": "A setup, not a breakout — it resolves in either direction.",
        "look_at": "The level itself, and whether volume is building into it.",
    },
    "breakout_confirmed": {
        "what": "Price broke above resistance and the move was confirmed by volume.",
        "why": "Confirmation is what separates a breakout from a false one.",
        "look_at": "The measured move and the R:R on the trade plan before chasing.",
    },
    "false_breakout": {
        "what": "Price broke a level and then fell back below it.",
        "why": "Failed breakouts often resolve hard in the opposite direction.",
        "look_at": "Whether the reclaim holds, and the next support beneath.",
    },
    "topping_formation": {
        "what": "The moving-average structure has rolled over into a topping pattern.",
        "why": "Structure deteriorating ahead of price is the earliest of the trend warnings.",
        "look_at": "The MA state in Structure and the pattern list for a matching formation.",
    },
    "recommendation_change": {
        "what": "The headline Buy/Sell/Hold call changed.",
        "why": "The composite read moved enough to change the conclusion, not just the score.",
        "look_at": "The evidence list under 'Why' — which inputs changed.",
    },
}


def _alert(now: str, ticker: str, type_: str, key: str, title: str, message: str,
           severity: str | None = None) -> Alert:
    return Alert(
        dedup_key=key, created_at=now, ticker=ticker, type=type_,
        severity=severity or _SEVERITY[type_], title=title, message=message,
    )


def _reco_message(ticker: str, a) -> str:
    bits = [f"{ticker} is now a {a.recommendation.upper()} (conviction {a.conviction})."]
    if a.entry is not None and a.stop is not None and a.target is not None:
        rr = f", R:R {a.rr}" if a.rr is not None else ""
        bits.append(f"Entry {a.entry}, stop {a.stop}, target {a.target}{rr}.")
    tops = [e.detail for e in a.evidence if e.signal != "neutral"][:2]
    if tops:
        bits.append(" ".join(tops))
    return " ".join(bits)


def _ta_candidates(now: str, ticker: str, a, prev: dict) -> list[Alert]:
    """TA transition alerts for one ticker. Assumes the previous TA snapshot is
    non-null (seeding is handled by the caller)."""
    today = now[:10]
    out: list[Alert] = []
    bo = a.breakout
    prev_status = prev["ta_breakout_status"]
    prev_ma = prev["ta_ma_state"]
    prev_reco = prev["ta_recommendation"]

    if bo is not None:
        if bo.status == "approaching" and bo.direction == "down":
            out.append(_alert(now, ticker, "breakdown_warning",
                f"breakdown_warning|{ticker}|{bo.level}",
                "Breakdown warning",
                f"{ticker} is within 1 ATR of support {bo.level}. {bo.note}"))
        elif bo.status == "approaching" and bo.direction == "up":
            out.append(_alert(now, ticker, "breakout_setup",
                f"breakout_setup|{ticker}|{bo.level}",
                "Breakout setup",
                f"{ticker} is approaching resistance {bo.level}. {bo.note}"))
        if bo.status == "confirmed" and prev_status != "confirmed":
            out.append(_alert(now, ticker, "breakout_confirmed",
                f"breakout_confirmed|{ticker}|{bo.level}",
                "Breakout confirmed",
                f"{ticker} confirmed a break at {bo.level}. {bo.note}"))
        if bo.status == "failed" and prev_status != "failed":
            out.append(_alert(now, ticker, "false_breakout",
                f"false_breakout|{ticker}|{today}",
                "False breakout",
                f"{ticker}: {bo.note}"))

    if a.ma_state == "topping" and prev_ma != "topping":
        out.append(_alert(now, ticker, "topping_formation",
            f"topping|{ticker}|{today}",
            "Topping formation",
            f"{ticker} is forming a top — MAs converging after a run and price slipping below them. Consider trimming."))

    if prev_reco is not None and a.recommendation != prev_reco:
        severity = "high" if a.recommendation in ("buy", "sell") else "medium"
        out.append(_alert(now, ticker, "recommendation_change",
            f"reco|{ticker}|{a.recommendation}|{today}",
            f"Recommendation changed to {a.recommendation.upper()}",
            _reco_message(ticker, a), severity=severity))
    return out


def detect(conn) -> list[Alert]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    today = now[:10]
    threshold = config.ALERT_BOOM_THRESHOLD

    boom = {b.ticker: b for b in db.get_boom_scores(conn)}
    analyses = {a.ticker: a for a in db.get_all_analyses(conn)}
    # Alerts are for the user's own stakes; signal-source candidates surface via
    # Suggestions, not alerts.
    tickers = sorted(set(db.get_all_watched_tickers(conn)) | set(db.get_all_portfolio_tickers(conn)))
    new_alerts: list[Alert] = []

    for ticker in tickers:
        bs = boom.get(ticker)
        a = analyses.get(ticker)
        if bs is None and a is None:
            continue
        prev = db.get_alert_state(conn, ticker)
        candidates: list[Alert] = []

        # ---------- Boom Score events ----------
        if bs is not None:
            prev_score = prev["score"] if prev else None
            prev_golden = bool(prev["golden_cross"]) if prev and prev["golden_cross"] is not None else False
            prev_insider = bool(prev["insider_cluster_buy"]) if prev and prev["insider_cluster_buy"] is not None else False

            if prev_score is not None and prev_score < threshold <= bs.score:
                candidates.append(_alert(
                    now, ticker, "boom_cross", f"boom_cross|{ticker}|{today}",
                    f"Boom Score crossed {threshold}",
                    f"{ticker} Boom Score is now {bs.score} (was {prev_score}).",
                ))
            if prev is not None and not prev_golden and bs.golden_cross:
                candidates.append(_alert(
                    now, ticker, "golden_cross", f"golden_cross|{ticker}|{today}",
                    "Golden cross formed",
                    f"{ticker}'s 50-day average crossed above its 200-day average.",
                ))
            if prev is not None and not prev_insider and bs.insider_cluster_buy:
                candidates.append(_alert(
                    now, ticker, "insider_cluster", f"insider_cluster|{ticker}|{today}",
                    "Insider cluster buy",
                    f"Two or more insiders bought {ticker} on the open market recently.",
                ))
            if bs.earnings_soon:
                analyst = db.get_analyst_for(conn, ticker)
                ed = analyst.next_earnings if analyst else None
                if ed:
                    candidates.append(_alert(
                        now, ticker, "earnings_soon", f"earnings|{ticker}|{ed}",
                        "Earnings within 7 days",
                        f"{ticker} reports earnings on {ed} — elevated event risk.",
                    ))
            for buy in db.get_congress_buys_for(conn, ticker):
                if _parse_amount_weight(buy["amount_range"]) >= 1.5:
                    candidates.append(_alert(
                        now, ticker, "congress_buy",
                        f"congress|{ticker}|{buy['transaction_date']}|{buy['amount_range']}",
                        "Congress bought > $50K",
                        f"A legislator purchased {ticker} ({buy['amount_range']}) on {buy['transaction_date']}.",
                    ))

        # ---------- Technical-analysis transition events ----------
        if a is not None:
            ta_seeded = prev is not None and prev["ta_recommendation"] is not None
            if ta_seeded:
                candidates.extend(_ta_candidates(now, ticker, a, prev))

        for c in candidates:
            if not db.alert_exists(conn, c.dedup_key):
                new_alerts.append(c)

        # Combined snapshot (boom + TA). Runs even for analysis-only tickers.
        db.upsert_alert_state(
            conn, ticker,
            bs.score if bs else None,
            bs.golden_cross if bs else None,
            bs.insider_cluster_buy if bs else None,
            now,
            ta_recommendation=a.recommendation if a else None,
            ta_breakout_status=(a.breakout.status if a and a.breakout else None),
            ta_ma_state=a.ma_state if a else None,
            ta_conviction=a.conviction if a else None,
        )

    # Push high-severity new alerts (resilient; no-op without creds).
    for al in new_alerts:
        if al.severity == "high":
            try:
                notify.push_alert(conn, al)
            except Exception:
                pass

    return new_alerts
