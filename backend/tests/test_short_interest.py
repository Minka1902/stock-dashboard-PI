"""Unit tests for the short_interest source module."""
from app.sources.short_interest import parse_response


def _payload(short_pct=0.25, shares_short=50_000_000, short_ratio=3.5, prior=45_000_000):
    return {
        "quoteSummary": {
            "result": [{
                "defaultKeyStatistics": {
                    "shortPercentOfFloat": {"raw": short_pct, "fmt": "25%"},
                    "sharesShort": {"raw": shares_short, "fmt": "50M"},
                    "shortRatio": {"raw": short_ratio, "fmt": "3.5"},
                    "sharesShortPriorMonth": {"raw": prior, "fmt": "45M"},
                }
            }],
            "error": None,
        }
    }


def test_parse_response_basic():
    result = parse_response(_payload(), "GME", "2026-06-22T10:00:00+00:00")
    assert result is not None
    assert result.ticker == "GME"
    assert result.short_pct_float == 0.25
    assert result.shares_short == 50_000_000
    assert result.days_to_cover == 3.5
    assert result.prior_month_shares == 45_000_000


def test_squeeze_flag_set_when_above_threshold():
    result = parse_response(_payload(short_pct=0.16), "GME", "2026-06-22T10:00:00+00:00")
    assert result is not None
    assert result.squeeze_flag is True


def test_squeeze_flag_not_set_when_below_threshold():
    result = parse_response(_payload(short_pct=0.10), "MSFT", "2026-06-22T10:00:00+00:00")
    assert result is not None
    assert result.squeeze_flag is False


def test_squeeze_flag_exactly_at_threshold_not_set():
    result = parse_response(_payload(short_pct=0.15), "AAPL", "2026-06-22T10:00:00+00:00")
    assert result is not None
    assert result.squeeze_flag is False  # > 0.15, not >=


def test_parse_response_missing_stats_returns_none():
    payload = {"quoteSummary": {"result": [{}], "error": None}}
    assert parse_response(payload, "GME", "2026-06-22T10:00:00+00:00") is None


def test_parse_response_null_result_returns_none():
    payload = {"quoteSummary": {"result": None, "error": "Not found"}}
    assert parse_response(payload, "GME", "2026-06-22T10:00:00+00:00") is None


def test_parse_response_partial_fields():
    payload = {
        "quoteSummary": {
            "result": [{
                "defaultKeyStatistics": {
                    "shortPercentOfFloat": {"raw": 0.20},
                    # sharesShort, shortRatio, sharesShortPriorMonth absent
                }
            }],
            "error": None,
        }
    }
    result = parse_response(payload, "BBBY", "2026-06-22T10:00:00+00:00")
    assert result is not None
    assert result.short_pct_float == 0.20
    assert result.shares_short is None
    assert result.days_to_cover is None
    assert result.squeeze_flag is True
