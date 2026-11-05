"""Outbound delivery for the daily suggestion digest.

Email uses stdlib smtplib; SMS uses Twilio's REST API via httpx (no extra
dependency). Both are gated on env-var config and never raise — they return a
status string, mirroring the resilience of `ingest.run_source`. When a channel
isn't configured they return "skipped: ... not configured" so the feature is
fully exercisable without credentials.
"""
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage

import httpx

from app import config, db, telegram
from app.models import Alert, NotifyProfile, SuggestionLogEntry
from app.suggestions import build_digest, render_email, render_sms


def send_email(profile: NotifyProfile, subject: str, text: str, html: str) -> str:
    if not profile.email or not profile.email_enabled:
        return "skipped: email not enabled"
    if not (config.SMTP_HOST and config.SMTP_FROM):
        return "skipped: smtp not configured"
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = config.SMTP_FROM
        msg["To"] = profile.email
        msg.set_content(text)
        msg.add_alternative(f"<html><body>{html}</body></html>", subtype="html")

        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as server:
            if config.SMTP_STARTTLS:
                server.starttls(context=ssl.create_default_context())
            if config.SMTP_USER and config.SMTP_PASSWORD:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(msg)
        return "sent"
    except Exception as exc:  # never let delivery crash the caller
        return f"error: {exc}"


def send_sms(profile: NotifyProfile, body: str) -> str:
    if not profile.phone or not profile.sms_enabled:
        return "skipped: sms not enabled"
    if not (config.TWILIO_ACCOUNT_SID and config.TWILIO_AUTH_TOKEN and config.TWILIO_FROM_PHONE):
        return "skipped: twilio not configured"
    try:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{config.TWILIO_ACCOUNT_SID}/Messages.json"
        resp = httpx.post(
            url,
            data={"To": profile.phone, "From": config.TWILIO_FROM_PHONE, "Body": body},
            auth=(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN),
            timeout=20,
        )
        resp.raise_for_status()
        return "sent"
    except Exception as exc:
        return f"error: {exc}"


def send_digest_for_user(conn, user_id: int, for_date: str | None = None) -> list[dict]:
    """Build one user's digest and deliver on their enabled+configured channels.

    Logs each channel's outcome to suggestion_log and returns per-channel status.
    """
    digest = build_digest(conn, for_date, user_id)
    for_date = digest["for_date"]
    profile = db.get_notify_profile(conn, user_id)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    subject, text, html = render_email(digest)
    sms_body = render_sms(digest)

    results = [
        {"channel": "email", "status": send_email(profile, subject, text, html)},
        {"channel": "sms", "status": send_sms(profile, sms_body)},
    ]

    db.insert_suggestion_log(conn, [
        SuggestionLogEntry(created_at=now, for_date=for_date, channel=r["channel"], status=r["status"])
        for r in results
    ])
    return results


def send_digest(conn, for_date: str | None = None) -> list[dict]:
    """Scheduled delivery: every user with an enabled channel gets their own digest."""
    results = []
    for user in db.get_users(conn):
        profile = db.get_notify_profile(conn, user.id)
        if not (profile.email_enabled or profile.sms_enabled):
            continue
        results.extend(send_digest_for_user(conn, user.id, for_date))
    return results


def push_alert(conn, alert: Alert) -> None:
    """Push a single alert to every user with an enabled+configured channel
    (email, SMS, a linked Telegram chat) —
    or, for a user's own alert (alert.user_id set), to that user only.
    Resilient; sets alert.pushed when at least one delivery succeeded.
    Logged to suggestion_log."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    subject = f"[{alert.severity.upper()}] {alert.ticker}: {alert.title}"
    body = f"{alert.title} — {alert.message}"
    html = f"<b>{alert.ticker}</b> · {alert.title}<br>{alert.message}"

    statuses = []
    users = db.get_users(conn) if alert.user_id is None else [db.get_user(conn, alert.user_id)]
    for user in filter(None, users):
        profile = db.get_notify_profile(conn, user.id)
        if profile.email_enabled or profile.sms_enabled:
            statuses.append(("email", send_email(profile, subject, body, html)))
            statuses.append(("sms", send_sms(profile, f"{alert.ticker}: {body}")))
        tg = telegram.send_to_user(conn, user.id, f"{subject}\n{alert.message}")
        if tg != "skipped":
            statuses.append(("telegram", tg))
    if any(s == "sent" for _, s in statuses):
        alert.pushed = True

    db.insert_suggestion_log(conn, [
        SuggestionLogEntry(created_at=now, for_date=alert.created_at[:10],
                           channel="alert", status=f"{ch}: {st}")
        for ch, st in statuses
    ])


def push_stored(conn, alerts: list[Alert]) -> None:
    """Push alerts that are already stored, and record which got through."""
    for alert in alerts:
        push_alert(conn, alert)
        if alert.pushed:
            db.mark_alert_pushed(conn, alert.dedup_key)
