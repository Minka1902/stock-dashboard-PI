from datetime import datetime, timezone

from app.sources import vix


def _epoch(y, m, d, hour=0):
    return int(datetime(y, m, d, hour, tzinfo=timezone.utc).timestamp())


# 2026-06-29, 2026-06-30, 2026-07-01 (00:00 UTC)
_TS = [_epoch(2026, 6, 29), _epoch(2026, 6, 30), _epoch(2026, 7, 1)]

SAMPLE_PAYLOAD = {
    "chart": {
        "result": [
            {
                "timestamp": _TS,
                "indicators": {"quote": [{"close": [20.1, None, 31.5]}]},
            }
        ]
    }
}


def test_parse_extracts_daily_closes_skipping_none():
    points = vix.parse_response(SAMPLE_PAYLOAD)
    assert len(points) == 2
    assert points[0].close == 20.1
    assert points[1].close == 31.5


def test_parse_maps_epoch_to_iso_dates_ascending():
    points = vix.parse_response(SAMPLE_PAYLOAD)
    assert [p.date for p in points] == sorted(p.date for p in points)
    assert all(len(p.date) == 10 and p.date[4] == "-" for p in points)


def test_parse_dedupes_same_day_keeping_last():
    payload = {
        "chart": {
            "result": [
                {
                    # Two timestamps on the same UTC day.
                    "timestamp": [1782000000, 1782003600],
                    "indicators": {"quote": [{"close": [20.0, 21.0]}]},
                }
            ]
        }
    }
    points = vix.parse_response(payload)
    assert len(points) == 1
    assert points[0].close == 21.0


def test_parse_rounds_close():
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": [1782000000],
                    "indicators": {"quote": [{"close": [19.987654]}]},
                }
            ]
        }
    }
    assert vix.parse_response(payload)[0].close == 19.99


def test_parse_returns_empty_on_empty_result():
    assert vix.parse_response({"chart": {"result": []}}) == []


def test_parse_returns_empty_on_missing_keys():
    assert vix.parse_response({}) == []
    assert vix.parse_response({"chart": {}}) == []
    assert vix.parse_response({"chart": {"result": [{}]}}) == []
