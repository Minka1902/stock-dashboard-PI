"""FINRA margin statistics — monthly margin-account debit balances, layered.

Tier 1: the FINRA Query API (public dataset, no auth). Tier 2: the public
statistics page, with the monthly "Debit Balances in Customers' Securities
Margin Accounts" figures ($ millions) parsed defensively out of the HTML.
Tier 3: the Excel workbook linked from that page. Tier 4: a real headless
Chromium (Playwright) loading the same page and workbook — FINRA's Cloudflare
front rejects plain HTTP clients (API 401, page 403) but serves a real browser.
Whichever tier succeeds is recorded in the source status; if all fail, the
combined errors surface via the source-status UI. %YoY (the signal input) is
computed at read time vs the same month a year earlier.
"""
import asyncio
import io
import os
import re
import sys
import time
from datetime import datetime

import httpx

from app import config
from app.ingest import FetchResult
from app.models import MarginDebtPoint

_URL = "https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics"
# FINRA Query API (https://developer.finra.org): public datasets allow
# unauthenticated reads. Group/name candidates are tried in order — dataset
# naming has shifted over time.
_API_URLS = [
    "https://api.finra.org/data/group/finra/name/marginStatistics",
    "https://api.finra.org/data/group/FINRA/name/marginStatistics",
]
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

# "January 2026" or "Jan-26", followed within a few non-digit chars by the
# first dollar figure of the row (debit balances; later columns are free
# credit balances and get skipped by anchoring on the first match).
_MONTHS_FULL = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)
_ROW_FULL = re.compile(rf"({_MONTHS_FULL})\s+(\d{{4}})\D{{0,40}}?([\d,]{{4,}})")
_ROW_ABBR = re.compile(r"\b([A-Z][a-z]{2})-(\d{2})\D{0,40}?([\d,]{4,})")

# Plausibility bounds in $ millions (margin debt has been ~$50B–$1.1T historically).
_MIN_VALUE = 10_000
_MAX_VALUE = 10_000_000


def _month_key(name: str, year: str) -> str | None:
    month_fmt = "%B" if len(name) > 3 else "%b"  # "January" vs "Jan"/"May"
    year_fmt = "%Y" if len(year) == 4 else "%y"
    try:
        return datetime.strptime(f"{name} {year}", f"{month_fmt} {year_fmt}").strftime("%Y-%m")
    except ValueError:
        return None


def parse_response(html: str) -> list[MarginDebtPoint]:
    text = re.sub(r"<[^>]+>", " ", html)

    by_month: dict[str, float] = {}
    for pattern in (_ROW_FULL, _ROW_ABBR):
        for name, year, raw_value in pattern.findall(text):
            month = _month_key(name, year)
            if month is None:
                continue
            try:
                value = float(raw_value.replace(",", ""))
            except ValueError:
                continue
            if not _MIN_VALUE <= value <= _MAX_VALUE:
                continue
            by_month.setdefault(month, value)

    return [MarginDebtPoint(month=m, debit_balances=v) for m, v in sorted(by_month.items())]


def _normalize_debit(value) -> float | None:
    """Coerce a debit-balance cell to $ millions, or None if implausible."""
    try:
        v = float(str(value).replace(",", "").replace("$", ""))
    except (ValueError, TypeError):
        return None
    if v > _MAX_VALUE and _MIN_VALUE <= v / 1_000_000 <= _MAX_VALUE:
        v = v / 1_000_000  # dataset published in dollars, not millions
    return v if _MIN_VALUE <= v <= _MAX_VALUE else None


_ISO_MONTH = re.compile(r"^(\d{4})-(\d{2})")


def parse_api_rows(rows: list) -> list[MarginDebtPoint]:
    """Rows from the FINRA Query API -> points. Field names are matched
    defensively (a month-ish key + a debit/margin key) so schema drift
    degrades to 'no rows' instead of wrong numbers."""
    by_month: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        month = None
        debit = None
        for key, value in row.items():
            lk = key.lower()
            if month is None and isinstance(value, str) and ("month" in lk or "date" in lk or lk.endswith("dt")):
                m = _ISO_MONTH.match(value.strip())
                if m:
                    month = f"{m.group(1)}-{m.group(2)}"
            if debit is None and "debit" in lk and ("margin" in lk or "securities" in lk):
                debit = _normalize_debit(value)
        if month and debit is not None:
            by_month.setdefault(month, debit)
    return [MarginDebtPoint(month=m, debit_balances=v) for m, v in sorted(by_month.items())]


_XLSX_HREF = re.compile(r'href="([^"]*margin[^"]*\.xlsx?)"', re.IGNORECASE)


def find_workbook_url(html: str) -> str | None:
    m = _XLSX_HREF.search(html)
    if not m:
        return None
    url = m.group(1)
    return url if url.startswith("http") else f"https://www.finra.org{url}"


def rows_to_points(rows: list[list]) -> list[MarginDebtPoint]:
    """Excel rows (raw cell values) -> points: month cell + first plausible
    debit figure per row. Shared by the workbook tier and its tests."""
    by_month: dict[str, float] = {}
    for cells in rows:
        month = None
        debit = None
        for value in cells:
            if month is None and isinstance(value, datetime):
                month = value.strftime("%Y-%m")
            elif month is None and isinstance(value, str):
                s = value.strip()
                m = _ISO_MONTH.match(s)
                if m:
                    month = f"{m.group(1)}-{m.group(2)}"
                else:
                    fm = re.match(rf"({_MONTHS_FULL})\s+(\d{{4}})", s) or re.match(r"([A-Z][a-z]{2})-(\d{2})$", s)
                    if fm:
                        month = _month_key(fm.group(1), fm.group(2))
            elif debit is None and isinstance(value, (int, float)):
                debit = _normalize_debit(value)
        if month and debit is not None:
            by_month.setdefault(month, debit)
    return [MarginDebtPoint(month=m, debit_balances=v) for m, v in sorted(by_month.items())]


def parse_workbook(content: bytes) -> list[MarginDebtPoint]:
    """Thin openpyxl shell around rows_to_points."""
    import openpyxl

    book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    rows: list[list] = []
    for sheet in book.worksheets:
        for row in sheet.iter_rows(values_only=True):
            rows.append(list(row))
    return rows_to_points(rows)


# ---- Tier 4: headless browser --------------------------------------------------
#
# Verified live (2026-09): FINRA's Cloudflare hard-blocks ("Sorry, you have been
# blocked") a headless Chromium that announces itself as "HeadlessChrome" in its
# User-Agent, and serves the page normally to the same browser with an ordinary
# Chrome UA. So the only disguise here is dropping the "Headless" token from the
# real browser's own version string — no stealth plugins, no captcha solving.
# If Cloudflare ever escalates to an interactive challenge, this tier fails
# with the page title in the error rather than working around it.

_INSTALL_CMD = r".venv\Scripts\python.exe -m playwright install chromium"
_NOT_INSTALLED = "headless browser not installed — run: "

# Challenge interstitials ("Just a moment...") clear themselves; the hard block
# page ("Attention Required! | Cloudflare") never does. Either way we wait for
# evidence of the real page: the workbook link or the statistics table text.
_READY_JS = """() => {
  if (/just a moment|attention required|checking your browser/i.test(document.title)) return false;
  if (document.querySelector('a[href*=".xls"]')) return true;
  const body = document.body ? document.body.innerText : '';
  return /Debit Balances/i.test(body);
}"""


def _browser_user_agent(version: str) -> str:
    """The launched browser's real Chrome major, minus the HeadlessChrome token."""
    major = (version or "").split(".", 1)[0] or "120"
    if sys.platform == "darwin":
        platform = "Macintosh; Intel Mac OS X 10_15_7"
    elif sys.platform.startswith("linux"):
        platform = "X11; Linux x86_64"
    else:
        platform = "Windows NT 10.0; Win64; x64"
    return (f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36")


def _install_hint() -> str:
    hint = _NOT_INSTALLED + _INSTALL_CMD
    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if browsers_path:
        # The Windows service installs browsers machine-wide; a plain install
        # would land in the installing user's profile, which LocalSystem can't see.
        hint += f" (with PLAYWRIGHT_BROWSERS_PATH={browsers_path} set)"
    return hint


def _import_playwright():
    """Lazy import so the module (and the other tiers) work without playwright."""
    try:
        import playwright.async_api as api
    except ImportError:
        raise RuntimeError(
            _NOT_INSTALLED + r".venv\Scripts\python.exe -m pip install playwright, then "
            + _INSTALL_CMD) from None
    return api


def describe_browser_error(exc: BaseException) -> str:
    """Playwright errors are multi-line banners; keep them status-sized, and
    turn 'no browser binary' into the command that fixes it."""
    msg = str(exc)
    if "Executable doesn't exist" in msg or "playwright install" in msg:
        return _install_hint()
    first = next((ln.strip() for ln in msg.splitlines() if ln.strip()), type(exc).__name__)
    return first[:300]


async def _browser_session(api, timeout_s: float) -> tuple[str, bytes | None, str | None]:
    """Load the statistics page in headless Chromium and, if possible, pull the
    workbook through the same browser context (so Cloudflare's clearance
    cookies carry). Returns (page_html, workbook_bytes | None, workbook_error)."""
    deadline = time.monotonic() + timeout_s

    def left_ms() -> float:
        ms = (deadline - time.monotonic()) * 1000
        if ms <= 0:
            raise TimeoutError(f"browser budget of {timeout_s:g}s exhausted")
        return ms

    async with api.async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, timeout=left_ms())
        try:
            context = await browser.new_context(
                user_agent=_browser_user_agent(browser.version),
                locale="en-US",
                viewport={"width": 1366, "height": 900},
            )
            page = await context.new_page()
            resp = await page.goto(_URL, wait_until="domcontentloaded", timeout=left_ms())
            status = resp.status if resp else None
            try:
                await page.wait_for_function(_READY_JS, timeout=left_ms())
            except (api.TimeoutError, TimeoutError):
                title = await page.title()
                raise RuntimeError(
                    f"page never cleared Cloudflare (HTTP {status}, title {title!r})") from None
            html = await page.content()

            workbook: bytes | None = None
            wb_error: str | None = None
            wb_url = config.MARGIN_DEBT_WORKBOOK_URL or find_workbook_url(html)
            if not wb_url:
                wb_error = "no link found on page"
            else:
                try:
                    wb_resp = await context.request.get(
                        wb_url, timeout=left_ms(), headers={"Referer": _URL})
                    if wb_resp.ok:
                        workbook = await wb_resp.body()
                    else:
                        wb_error = f"HTTP {wb_resp.status}"
                except Exception as exc:  # the page html is still usable
                    wb_error = describe_browser_error(exc)
            return html, workbook, wb_error
        finally:
            await browser.close()


def _run_browser_session(api, timeout_s: float) -> tuple[str, bytes | None, str | None]:
    # An explicit loop, not sync_playwright(): the sync API builds its loop from
    # the global policy, and `uvicorn --reload` on Windows installs
    # WindowsSelectorEventLoopPolicy, whose loops cannot spawn the browser
    # subprocess (NotImplementedError). Proactor always can. Runs on the
    # scheduler/refresh worker thread, which has no loop of its own.
    loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
    try:
        # Hard ceiling on top of the per-step budgets (launch/close overheads).
        return loop.run_until_complete(
            asyncio.wait_for(_browser_session(api, timeout_s), timeout_s + 15))
    finally:
        loop.close()


def fetch_via_browser() -> FetchResult:
    """Tier 4. Prefers the workbook (full history back to 1997) and falls back
    to the page's table (last ~13 months). Raises with a readable reason."""
    api = _import_playwright()
    try:
        html, workbook, wb_error = _run_browser_session(
            api, config.MARGIN_DEBT_BROWSER_TIMEOUT_SECONDS)
    except NotImplementedError:  # a RuntimeError subclass with an empty message
        raise RuntimeError("event loop cannot spawn the browser subprocess") from None
    except RuntimeError:
        raise
    except asyncio.TimeoutError:
        raise RuntimeError(
            f"timed out after {config.MARGIN_DEBT_BROWSER_TIMEOUT_SECONDS:g}s") from None
    except Exception as exc:
        raise RuntimeError(describe_browser_error(exc)) from None

    if workbook is not None:
        try:
            points = parse_workbook(workbook)
        except Exception as exc:
            points, wb_error = [], f"unreadable ({exc})"
        if points:
            return FetchResult(points, note="source: headless browser (workbook)")
        wb_error = wb_error or "no valid rows"
    points = parse_response(html)
    if points:
        return FetchResult(
            points, note=f"source: headless browser (page table; workbook: {wb_error})")
    raise RuntimeError(f"page loaded but no rows parsed (workbook: {wb_error})")


def fetch() -> list[MarginDebtPoint]:
    errors: list[str] = []
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        # Tier 1: the Query API (structured, complete history).
        for api_url in _API_URLS:
            try:
                resp = client.get(api_url, params={"limit": 1000},
                                  headers={**_HEADERS, "Accept": "application/json"})
                resp.raise_for_status()
                points = parse_api_rows(resp.json())
                if points:
                    return FetchResult(points, note="source: finra query api")
                errors.append(f"api {api_url.rsplit('/', 1)[-1]}: no usable rows")
            except Exception as exc:
                errors.append(f"api: {exc}")
                break  # same host; don't hammer it with the second candidate
        # Tier 2: the public statistics page.
        page_html = None
        try:
            resp = client.get(_URL, headers=_HEADERS)
            resp.raise_for_status()
            page_html = resp.text
            points = parse_response(page_html)
            if points:
                return points
            errors.append("page: no rows parsed")
        except httpx.HTTPError as exc:
            errors.append(f"page: {exc}")
        # Tier 3: the Excel workbook. Normally discovered from the page, but a
        # configured URL wins so the tier still works when the page is blocked.
        #
        # This tier used to be unreachable in exactly the situation it exists
        # for: when the page request failed, page_html stayed None, so the
        # `if wb_url` and `elif page_html is not None` arms were both skipped —
        # the workbook was never tried AND no error was appended, so the status
        # string silently omitted the whole tier.
        try:
            wb_url = config.MARGIN_DEBT_WORKBOOK_URL or (
                find_workbook_url(page_html) if page_html else None)
            if wb_url:
                resp = client.get(wb_url, headers=_HEADERS)
                resp.raise_for_status()
                points = parse_workbook(resp.content)
                if points:
                    return FetchResult(points, note="fallback: workbook")
                errors.append("workbook: no valid rows")
            elif page_html is not None:
                errors.append("workbook: no link found on page")
            else:
                errors.append(
                    "workbook: page unavailable, so no link to discover "
                    "(set STOCKS_MARGIN_DEBT_WORKBOOK_URL to try it directly)")
        except Exception as exc:
            errors.append(f"workbook: {exc}")
    # Tier 4: a real browser, outside the httpx client so it isn't held open.
    try:
        return fetch_via_browser()
    except Exception as exc:
        errors.append(f"browser: {exc}")
    raise RuntimeError("; ".join(errors))


def compute_yoy(points: list[MarginDebtPoint]) -> list[dict]:
    """[{month, debit_balances, yoy_pct|None}] — change vs same month prior year."""
    by_month = {p.month: p.debit_balances for p in points}
    out = []
    for p in points:
        prior_key = f"{int(p.month[:4]) - 1}{p.month[4:]}"
        prev = by_month.get(prior_key)
        yoy = round((p.debit_balances / prev - 1) * 100, 1) if prev and prev > 0 else None
        out.append({"month": p.month, "debit_balances": p.debit_balances, "yoy_pct": yoy})
    return out
