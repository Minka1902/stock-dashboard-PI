"""X (Twitter) watcher for market-moving accounts.

Two providers:
  * Official X API v2 when ``STOCKS_X_BEARER`` is set → status "ok".
  * Otherwise unofficial Nitter-style RSS mirrors → the records are real and get
    stored, but the source is stamped as an error-style *warning* (degraded
    provenance) so the UI flags that no official key is configured.

Parsing helpers (`parse_rss`, `extract_tickers`) are pure and unit-tested; only
``fetch`` does HTTP.
"""
import html
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx

from app import config
from app.ingest import FetchResult
from app.models import XPost

_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; stock-dashboard/1.0)"}

# $TSLA style cashtags: 1-5 uppercase letters after a dollar sign.
_CASHTAG_RE = re.compile(r"\$([A-Za-z]{1,5})\b")
_TAG_RE = re.compile(r"<[^>]+>")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_text(raw: str) -> str:
    """Strip HTML tags/entities from an RSS title or description."""
    if not raw:
        return ""
    return html.unescape(_TAG_RE.sub("", raw)).strip()


def _iso_from_pubdate(pubdate: str) -> str:
    """RFC-822 RSS pubDate → strict UTC ISO 8601, or "" when unparseable.

    Never returns the raw RFC-822 string: mixing "Mon, 07 Jul 2025 ..." with
    ISO timestamps in the same column string-sorts by weekday name, which is
    what made the "All" feed group by account instead of by date (Task 19).
    The caller substitutes fetched_at (also strict ISO) when this returns "".
    """
    try:
        dt = parsedate_to_datetime(pubdate)
    except (TypeError, ValueError):
        return ""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _post_id_from_guid(guid: str, link: str) -> str:
    """Nitter guids/links end in the numeric status id; use the trailing digits,
    else fall back to the last path segment."""
    src = guid or link or ""
    m = re.search(r"(\d{5,})", src)
    if m:
        return m.group(1)
    return src.rstrip("/").rsplit("/", 1)[-1]


def extract_tickers(text: str, known: set[str]) -> list[str]:
    """Detect tickers in post text: every ``$CASHTAG`` plus any whole-word match
    of a known (watched/held) ticker. Case-insensitive; de-duplicated; order
    preserved (cashtags first). No substring false positives — word boundaries
    only."""
    out: list[str] = []
    seen: set[str] = set()
    for m in _CASHTAG_RE.finditer(text):
        t = m.group(1).upper()
        if t not in seen:
            seen.add(t)
            out.append(t)
    upper_text = text.upper()
    for t in sorted(known):
        tu = t.upper()
        if tu in seen:
            continue
        if re.search(rf"\b{re.escape(tu)}\b", upper_text):
            seen.add(tu)
            out.append(tu)
    return out


def parse_rss(xml_text: str, account: str, known: set[str] | None = None) -> list[XPost]:
    """Parse a Nitter RSS feed into XPost records (newest-first as provided)."""
    known = known or set()
    fetched_at = _now_iso()
    root = ElementTree.fromstring(xml_text)
    posts: list[XPost] = []
    for item in root.iter("item"):
        title = _clean_text(item.findtext("title") or "")
        desc = _clean_text(item.findtext("description") or "")
        text = title or desc
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        pubdate = (item.findtext("pubDate") or "").strip()
        post_id = _post_id_from_guid(guid, link)
        if not post_id or not text:
            continue
        tickers = extract_tickers(text, known)
        posts.append(XPost(
            account=account,
            post_id=post_id,
            text=text,
            url=link,
            posted_at=_iso_from_pubdate(pubdate) or fetched_at,
            tickers=",".join(tickers),
            fetched_at=fetched_at,
        ))
    return posts


# ---- official X API v2 ----

def _fetch_official(accounts: list[str], known: set[str]) -> list[XPost]:
    fetched_at = _now_iso()
    out: list[XPost] = []
    headers = {**_HEADERS, "Authorization": f"Bearer {config.X_BEARER}"}
    with httpx.Client(timeout=config.X_TIMEOUT_SECONDS, headers=headers) as client:
        for account in accounts:
            u = client.get(f"https://api.twitter.com/2/users/by/username/{account}")
            u.raise_for_status()
            user_id = (u.json().get("data") or {}).get("id")
            if not user_id:
                continue
            r = client.get(
                f"https://api.twitter.com/2/users/{user_id}/tweets",
                params={"max_results": min(config.X_POSTS_LIMIT, 100),
                        "tweet.fields": "created_at"},
            )
            r.raise_for_status()
            for tw in (r.json().get("data") or []):
                text = tw.get("text") or ""
                pid = str(tw.get("id") or "")
                if not pid or not text:
                    continue
                out.append(XPost(
                    account=account,
                    post_id=pid,
                    text=text,
                    url=f"https://x.com/{account}/status/{pid}",
                    posted_at=tw.get("created_at") or fetched_at,
                    tickers=",".join(extract_tickers(text, known)),
                    fetched_at=fetched_at,
                ))
    return out


# ---- unofficial Nitter mirrors ----

def _fetch_mirrors(accounts: list[str], known: set[str]) -> list[XPost]:
    out: list[XPost] = []
    with httpx.Client(timeout=config.X_TIMEOUT_SECONDS, headers=_HEADERS,
                      follow_redirects=True) as client:
        for i, account in enumerate(accounts):
            for mirror in config.X_MIRRORS:
                try:
                    resp = client.get(f"{mirror}/{account}/rss")
                    resp.raise_for_status()
                    posts = parse_rss(resp.text, account, known)
                    if posts:
                        out.extend(posts[:config.X_POSTS_LIMIT])
                        break  # first parseable mirror wins for this account
                except Exception:
                    continue  # try the next mirror
            if i < len(accounts) - 1:
                time.sleep(0.3)  # be polite between accounts
    return out


def fetch(accounts: list[str], known_tickers: set[str]) -> FetchResult:
    """Fetch recent posts for ``accounts``.

    Official API path (bearer set) → FetchResult with no warning (status "ok").
    Mirror path → FetchResult carrying a warning (degraded provenance) if any
    data came back; raises if every mirror failed for every account (honest
    "no data" error, never fabricated)."""
    if config.X_BEARER:
        return FetchResult(_fetch_official(accounts, known_tickers))

    records = _fetch_mirrors(accounts, known_tickers)
    if not records:
        raise RuntimeError(
            "all X mirrors failed for all accounts — set STOCKS_X_BEARER for the official API"
        )
    return FetchResult(
        records,
        warning="unofficial mirror — set STOCKS_X_BEARER for official API",
    )
