from app.sources import aaii

FETCHED_AT = "2026-07-02T10:00:00+00:00"

SAMPLE_HTML = """
<html><body>
<h1>AAII Investor Sentiment Survey</h1>
<p>Week Ending 6/24/2026</p>
<table>
  <tr><td>Bullish</td><td>21.3%</td></tr>
  <tr><td>Neutral</td><td>27.5%</td></tr>
  <tr><td>Bearish</td><td>51.2%</td></tr>
</table>
</body></html>
"""


def test_parse_extracts_percentages():
    records = aaii.parse_response(SAMPLE_HTML, FETCHED_AT)
    assert len(records) == 1
    r = records[0]
    assert r.bullish == 21.3
    assert r.neutral == 27.5
    assert r.bearish == 51.2
    assert r.fetched_at == FETCHED_AT


def test_parse_extracts_week_ending_us_format():
    records = aaii.parse_response(SAMPLE_HTML, FETCHED_AT)
    assert records[0].week_ending == "2026-06-24"


def test_parse_long_date_fallback():
    html = SAMPLE_HTML.replace("Week Ending 6/24/2026", "Results for June 24, 2026")
    records = aaii.parse_response(html, FETCHED_AT)
    assert records[0].week_ending == "2026-06-24"


def test_parse_missing_date_falls_back_to_fetched_at():
    html = SAMPLE_HTML.replace("Week Ending 6/24/2026", "")
    records = aaii.parse_response(html, FETCHED_AT)
    assert records[0].week_ending == "2026-07-02"


def test_parse_returns_empty_on_missing_label():
    html = SAMPLE_HTML.replace("Bearish", "Something")
    assert aaii.parse_response(html, FETCHED_AT) == []


def test_parse_returns_empty_when_percentages_do_not_sum():
    html = SAMPLE_HTML.replace("51.2%", "5.2%")  # sum ≈ 54 → reject
    assert aaii.parse_response(html, FETCHED_AT) == []


def test_parse_returns_empty_on_out_of_range_pct():
    html = SAMPLE_HTML.replace("51.2%", "151.2%")
    assert aaii.parse_response(html, FETCHED_AT) == []


def test_parse_returns_empty_on_blank_page():
    assert aaii.parse_response("<html></html>", FETCHED_AT) == []


# ---------- spreadsheet fallback (rows_to_sentiments) ----------

def test_rows_to_sentiments_fraction_format():
    # sentiment.xls stores bull/neutral/bear as fractions summing to ~1.
    rows = [
        (None, []),                                # header
        ("2026-06-25", [0.375, 0.318, 0.307]),
        ("2026-07-02", [0.412, 0.301, 0.287]),
    ]
    out = aaii.rows_to_sentiments(rows, "2026-07-06T00:00:00+00:00")
    assert [s.week_ending for s in out] == ["2026-06-25", "2026-07-02"]
    assert out[1].bullish == 41.2
    assert out[1].bearish == 28.7


def test_rows_to_sentiments_percent_format():
    rows = [("2026-07-02", [41.2, 30.1, 28.7])]
    out = aaii.rows_to_sentiments(rows, "2026-07-06T00:00:00+00:00")
    assert len(out) == 1
    assert out[0].neutral == 30.1


def test_rows_to_sentiments_rejects_garbage():
    rows = [
        ("2026-07-02", [1500.0, 30.0, 28.0]),   # implausible
        ("2026-07-02", [0.9, 0.9, 0.9]),        # doesn't sum to ~1
        (None, [0.4, 0.3, 0.3]),                # no date
        ("2026-07-02", [0.4]),                  # too few values
    ]
    assert aaii.rows_to_sentiments(rows, "2026-07-06T00:00:00+00:00") == []


def test_rows_to_sentiments_keeps_most_recent_limit():
    rows = [(f"2026-01-{d:02d}", [0.4, 0.3, 0.3]) for d in range(1, 29)]
    out = aaii.rows_to_sentiments(rows, "2026-07-06T00:00:00+00:00", limit=5)
    assert len(out) == 5
    assert out[-1].week_ending == "2026-01-28"
