"""Shared Yahoo Finance access.

The v10 `quoteSummary` endpoint rejects anonymous callers with
401 "Invalid Crumb". The handshake is: hit a Yahoo host to pick up the consent
cookies, exchange them for a crumb, then pass that crumb on every data request.
The v8 `chart` endpoints (bars, VIX) are unaffected and don't use this.

Kept in one place because three sources — fundamentals, analyst and
short_interest — all talk to quoteSummary and all broke the same way.
"""
import httpx

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
HEADERS = {"User-Agent": BROWSER_UA}

_COOKIE_URL = "https://fc.yahoo.com"
_CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"


def get_crumb(client: httpx.Client) -> str:
    """Seed the session cookies and exchange them for an API crumb.

    Raises when Yahoo won't issue one, so the caller's source is stamped as an
    error rather than quietly reporting a successful run over zero records.
    """
    try:
        client.get(_COOKIE_URL)  # 404 is expected; we only want the Set-Cookie
    except httpx.HTTPError:
        pass
    r = client.get(_CRUMB_URL)
    r.raise_for_status()
    crumb = r.text.strip()
    if not crumb or "<" in crumb:
        raise ValueError("Yahoo did not issue a crumb")
    return crumb


def with_crumb(url: str, crumb: str) -> str:
    """Append the crumb to a URL that already carries a query string.

    Appended rather than passed as httpx `params=`, which replaces the whole
    query string and would silently drop `modules=`.
    """
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}crumb={crumb}"


def client(timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(headers=HEADERS, timeout=timeout, follow_redirects=True)
