import httpx
import pytest

from app.models import MarginDebtPoint
from app.sources import margin_debt


@pytest.fixture(autouse=True)
def _no_real_browser(monkeypatch):
    """No test may launch Chromium or reach FINRA. Tests that exercise the
    browser tier replace these stubs with their own."""
    def _blocked(*a, **kw):
        raise RuntimeError("browser disabled in tests")
    monkeypatch.setattr(margin_debt, "_run_browser_session", _blocked)

SAMPLE_HTML = """
<html><body>
<h1>Margin Statistics</h1>
<table>
  <tr><th>Month/Year</th><th>Debit Balances in Customers' Securities Margin Accounts</th>
      <th>Free Credit Balances in Customers' Cash Accounts</th></tr>
  <tr><td>May 2026</td><td>1,050,123</td><td>180,000</td></tr>
  <tr><td>April 2026</td><td>1,010,500</td><td>175,250</td></tr>
  <tr><td>March 2026</td><td>995,000</td><td>170,000</td></tr>
</table>
<table>
  <tr><td>May-25</td><td>677,499</td><td>160,000</td></tr>
  <tr><td>Apr-25</td><td>650,000</td><td>158,000</td></tr>
</table>
</body></html>
"""


def test_parse_extracts_first_column_per_month():
    points = margin_debt.parse_response(SAMPLE_HTML)
    by_month = {p.month: p.debit_balances for p in points}
    assert by_month["2026-05"] == 1_050_123.0  # not the 180,000 credit column
    assert by_month["2026-04"] == 1_010_500.0
    assert by_month["2025-05"] == 677_499.0


def test_parse_handles_both_month_formats_sorted_asc():
    points = margin_debt.parse_response(SAMPLE_HTML)
    months = [p.month for p in points]
    assert months == sorted(months)
    assert "2025-04" in months  # "Apr-25" abbreviated form
    assert "2026-03" in months  # "March 2026" full form


def test_parse_drops_implausible_values():
    html = "<table><tr><td>May 2026</td><td>1,234</td></tr></table>"  # < 10,000 → junk
    assert margin_debt.parse_response(html) == []


def test_parse_returns_empty_on_garbage():
    assert margin_debt.parse_response("") == []
    assert margin_debt.parse_response("<html><p>maintenance page</p></html>") == []


def _point(month, value):
    return MarginDebtPoint(month=month, debit_balances=value)


def test_compute_yoy_math():
    points = [_point("2025-05", 677_499.0), _point("2026-05", 1_050_123.0)]
    rows = margin_debt.compute_yoy(points)
    assert rows[0]["yoy_pct"] is None  # no 2024-05
    assert rows[1]["yoy_pct"] == round((1_050_123.0 / 677_499.0 - 1) * 100, 1)  # ≈ 55.0


def test_compute_yoy_missing_prior_year_is_none():
    rows = margin_debt.compute_yoy([_point("2026-05", 1_000_000.0), _point("2026-04", 990_000.0)])
    assert all(r["yoy_pct"] is None for r in rows)


def test_compute_yoy_zero_prior_is_none():
    rows = margin_debt.compute_yoy([_point("2025-05", 0.0), _point("2026-05", 1_000_000.0)])
    assert rows[1]["yoy_pct"] is None


def test_compute_yoy_preserves_month_and_balance():
    rows = margin_debt.compute_yoy([_point("2026-05", 1_000_000.0)])
    assert rows == [{"month": "2026-05", "debit_balances": 1_000_000.0, "yoy_pct": None}]


# ---------- FINRA Query API tier ----------

def test_parse_api_rows_millions_and_dollars():
    rows = [
        # $ millions (matches the published table scale)
        {"monthDt": "2026-05-01", "debitBalancesCustomersSecuritiesMarginAccounts": 1_050_123.0},
        # full dollars — must be normalized down to millions
        {"monthDt": "2026-04-01", "debitBalancesCustomersSecuritiesMarginAccounts": 995_000_000_000.0},
        # unrelated fields ignored
        {"monthDt": "2026-03-01", "freeCreditBalancesCashAccounts": 500_000.0},
    ]
    points = margin_debt.parse_api_rows(rows)
    assert [(p.month, p.debit_balances) for p in points] == [
        ("2026-04", 995_000.0), ("2026-05", 1_050_123.0),
    ]


def test_parse_api_rows_ignores_non_dicts_and_garbage():
    assert margin_debt.parse_api_rows(["x", 42, {"foo": "bar"}]) == []


# ---------- workbook tier ----------

def test_find_workbook_url_relative_and_absolute():
    html = '<a href="/sites/default/files/2026-06/margin-statistics.xlsx">xlsx</a>'
    assert margin_debt.find_workbook_url(html) == \
        "https://www.finra.org/sites/default/files/2026-06/margin-statistics.xlsx"
    html2 = '<a href="https://cdn.finra.org/margin_stats.xlsx">xlsx</a>'
    assert margin_debt.find_workbook_url(html2) == "https://cdn.finra.org/margin_stats.xlsx"
    assert margin_debt.find_workbook_url("<p>no link</p>") is None


def test_rows_to_points_handles_datetime_and_text_months():
    from datetime import datetime as dt
    rows = [
        ["Month", "Debit Balances"],                    # header skipped
        [dt(2026, 5, 1), 1_050_123.0],
        ["Apr-26", 1_020_000.0],
        ["March 2026", 990_000.0],
        ["2026-02", 970_000.0],
        ["not a month", 960_000.0],                     # no month -> skipped
        [dt(2026, 1, 1), 12.5],                         # implausible value -> skipped
    ]
    points = margin_debt.rows_to_points(rows)
    assert [(p.month, p.debit_balances) for p in points] == [
        ("2026-02", 970_000.0), ("2026-03", 990_000.0),
        ("2026-04", 1_020_000.0), ("2026-05", 1_050_123.0),
    ]


def test_parse_workbook_roundtrip():
    # Build a real xlsx in memory and run it through the workbook tier.
    import io
    from datetime import datetime as dt
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Month", "Debit Balances in Customers' Securities Margin Accounts"])
    ws.append([dt(2026, 4, 1), 1_020_000])
    ws.append([dt(2026, 5, 1), 1_050_123])
    buf = io.BytesIO()
    wb.save(buf)

    points = margin_debt.parse_workbook(buf.getvalue())
    assert [(p.month, p.debit_balances) for p in points] == [
        ("2026-04", 1_020_000.0), ("2026-05", 1_050_123.0),
    ]


# ---- tier fallback wiring ----------------------------------------------------
# FINRA currently answers 401 on the API and 403 on the statistics page. These
# lock down that the workbook tier is still *reached* and still *reported* in
# that state — it used to be skipped silently, so the status string claimed only
# two tiers had been tried.

class _Resp:
    def __init__(self, text="", content=b"", status=200):
        self.text = text
        self.content = content
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise httpx.HTTPStatusError(
                f"Client error '{self._status}'", request=None, response=None)

    def json(self):
        return {}


class _BlockedClient:
    """API 401 + page 403, i.e. exactly what FINRA returns today."""

    def __init__(self, workbook: bytes | None = None):
        self.workbook = workbook
        self.requested = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **kwargs):
        self.requested.append(url)
        if "api.finra.org" in url:
            return _Resp(status=401)
        if url.endswith((".xlsx", ".xls")):
            if self.workbook is None:
                return _Resp(status=404)
            return _Resp(content=self.workbook)
        return _Resp(status=403)  # the statistics page


def _client_factory(client):
    def _factory(*a, **kw):
        return client
    return _factory


def test_blocked_page_still_reports_the_workbook_tier(monkeypatch):
    """The old code appended nothing for the workbook when the page failed."""
    client = _BlockedClient()
    monkeypatch.setattr(margin_debt.httpx, "Client", _client_factory(client))
    monkeypatch.setattr(margin_debt.config, "MARGIN_DEBT_WORKBOOK_URL", "")

    with pytest.raises(RuntimeError) as excinfo:
        margin_debt.fetch()

    message = str(excinfo.value)
    assert "api" in message
    assert "page" in message
    assert "workbook" in message, "the workbook tier must not be silently omitted"


def test_configured_workbook_url_is_used_when_the_page_is_blocked(monkeypatch):
    """The escape hatch: a known workbook URL keeps tier 3 usable at all."""
    import io
    from datetime import datetime as dt
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Month", "Debit Balances"])
    ws.append([dt(2026, 6, 1), 1_060_000])
    buf = io.BytesIO()
    wb.save(buf)

    client = _BlockedClient(workbook=buf.getvalue())
    monkeypatch.setattr(margin_debt.httpx, "Client", _client_factory(client))
    monkeypatch.setattr(
        margin_debt.config, "MARGIN_DEBT_WORKBOOK_URL",
        "https://www.finra.org/sites/default/files/margin-statistics.xlsx")

    points = margin_debt.fetch()
    assert [(p.month, p.debit_balances) for p in points] == [("2026-06", 1_060_000.0)]
    assert points.note == "fallback: workbook"
    assert any(u.endswith(".xlsx") for u in client.requested)


def test_margin_debt_cadence_is_weekly_with_a_short_retry():
    """Cadence is about when we ASK; the retry gate is a separate clock, so one
    401 can't freeze the source for the full week."""
    from app import config as app_config

    assert app_config.MARGIN_DEBT_MIN_INTERVAL_SECONDS == 7 * 86400
    assert app_config.MARGIN_DEBT_RETRY_INTERVAL_SECONDS < 86400


def test_margin_debt_and_gdelt_are_not_forced_by_the_daily_run():
    """force_on_daily=True would bypass both gates every weekday."""
    from app import main as main_module

    sources = main_module.build_sources(main_module.conn)
    for name in ("margin_debt", "gdelt"):
        assert sources[name].force_on_daily is False, name
        assert sources[name].retry_interval is not None, name


# ---- tier 4: headless browser ------------------------------------------------
# The Playwright layer (_run_browser_session) is mocked; these cover what we do
# with its output and how its failures are reported.

def _xlsx(rows):
    import io
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _fake_browser(monkeypatch, html="", workbook=None, wb_error=None):
    calls = []

    def _session(api, timeout_s):
        calls.append(timeout_s)
        return html, workbook, wb_error

    monkeypatch.setattr(margin_debt, "_import_playwright", lambda: object())
    monkeypatch.setattr(margin_debt, "_run_browser_session", _session)
    return calls


def test_browser_tier_prefers_the_workbook(monkeypatch):
    from datetime import datetime as dt
    book = _xlsx([["Month", "Debit Balances"],
                  [dt(2026, 7, 1), 1_417_225], [dt(2026, 8, 1), 1_453_832]])
    _fake_browser(monkeypatch, html=SAMPLE_HTML, workbook=book)

    points = margin_debt.fetch_via_browser()
    assert [(p.month, p.debit_balances) for p in points] == [
        ("2026-07", 1_417_225.0), ("2026-08", 1_453_832.0)]
    assert points.note == "source: headless browser (workbook)"


def test_browser_tier_falls_back_to_the_page_table(monkeypatch):
    _fake_browser(monkeypatch, html=SAMPLE_HTML, workbook=None, wb_error="HTTP 404")

    points = margin_debt.fetch_via_browser()
    by_month = {p.month: p.debit_balances for p in points}
    assert by_month["2026-05"] == 1_050_123.0
    assert "page table" in points.note and "HTTP 404" in points.note


def test_browser_tier_unreadable_workbook_still_uses_the_table(monkeypatch):
    _fake_browser(monkeypatch, html=SAMPLE_HTML, workbook=b"not an xlsx")
    points = margin_debt.fetch_via_browser()
    assert points and "page table" in points.note


def test_browser_tier_with_nothing_parseable_raises(monkeypatch):
    _fake_browser(monkeypatch, html="<p>nothing</p>", workbook=None,
                  wb_error="no link found on page")
    with pytest.raises(RuntimeError, match="no rows parsed"):
        margin_debt.fetch_via_browser()


def test_fetch_uses_the_browser_when_every_http_tier_is_blocked(monkeypatch):
    client = _BlockedClient()
    monkeypatch.setattr(margin_debt.httpx, "Client", _client_factory(client))
    monkeypatch.setattr(margin_debt.config, "MARGIN_DEBT_WORKBOOK_URL", "")
    _fake_browser(monkeypatch, html=SAMPLE_HTML, wb_error="HTTP 403")

    points = margin_debt.fetch()
    assert len(points) == 5
    assert points.note.startswith("source: headless browser")


def test_missing_playwright_package_is_a_clear_error(monkeypatch):
    import sys
    # A None entry in sys.modules makes `import` raise ImportError.
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)

    with pytest.raises(RuntimeError) as excinfo:
        margin_debt.fetch_via_browser()
    msg = str(excinfo.value)
    assert msg.startswith("headless browser not installed — run: ")
    assert "pip install playwright" in msg
    assert "playwright install chromium" in msg


def test_missing_browser_binary_maps_to_the_install_command(monkeypatch):
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    exc = Exception(
        "BrowserType.launch: Executable doesn't exist at C:/pw/chrome.exe\n"
        "╔════╗\n║ Looks like Playwright was just installed ║")
    assert margin_debt.describe_browser_error(exc) == (
        "headless browser not installed — run: "
        r".venv\Scripts\python.exe -m playwright install chromium")

    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", r"C:\ProgramData\SignalDashboard\ms-playwright")
    assert r"PLAYWRIGHT_BROWSERS_PATH=C:\ProgramData\SignalDashboard\ms-playwright" in \
        margin_debt.describe_browser_error(exc)


def test_browser_launch_failure_is_reported_not_raised_raw(monkeypatch):
    def _session(api, timeout_s):
        raise Exception("BrowserType.launch: Executable doesn't exist at C:/nope\n=====")
    monkeypatch.setattr(margin_debt, "_import_playwright", lambda: object())
    monkeypatch.setattr(margin_debt, "_run_browser_session", _session)

    with pytest.raises(RuntimeError, match="headless browser not installed"):
        margin_debt.fetch_via_browser()


def test_describe_browser_error_keeps_the_first_line():
    exc = Exception("Page.goto: net::ERR_NAME_NOT_RESOLVED\nCall log:\n  - navigating")
    assert margin_debt.describe_browser_error(exc) == "Page.goto: net::ERR_NAME_NOT_RESOLVED"


def test_all_four_tiers_failing_aggregates_every_reason(monkeypatch):
    client = _BlockedClient()
    monkeypatch.setattr(margin_debt.httpx, "Client", _client_factory(client))
    monkeypatch.setattr(margin_debt.config, "MARGIN_DEBT_WORKBOOK_URL", "")

    def _session(api, timeout_s):
        raise RuntimeError(
            "page never cleared Cloudflare (HTTP 403, title 'Attention Required! | Cloudflare')")
    monkeypatch.setattr(margin_debt, "_import_playwright", lambda: object())
    monkeypatch.setattr(margin_debt, "_run_browser_session", _session)

    with pytest.raises(RuntimeError) as excinfo:
        margin_debt.fetch()
    message = str(excinfo.value)
    for tier in ("api", "page:", "workbook:", "browser: page never cleared Cloudflare"):
        assert tier in message, tier


def test_browser_user_agent_drops_the_headless_token():
    ua = margin_debt._browser_user_agent("153.0.8010.12")
    assert "Headless" not in ua
    assert "Chrome/153.0.0.0" in ua
