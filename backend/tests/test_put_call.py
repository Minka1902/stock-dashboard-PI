from datetime import datetime, timezone

from app.sources import put_call


def _epoch_ms(y, m, d, hour=0):
    return int(datetime(y, m, d, hour, tzinfo=timezone.utc).timestamp() * 1000)


_DAY1_MS = _epoch_ms(2026, 6, 30)
_DAY2_MS = _epoch_ms(2026, 7, 1)

SAMPLE_PAYLOAD = {
    "fear_and_greed": {"score": 42.0, "rating": "Fear"},
    "put_call_options": {
        "timestamp": "2026-07-01T00:00:00+00:00",
        "score": 60.0,
        "rating": "fear",
        "data": [
            {"x": _DAY1_MS, "y": 0.92, "rating": "fear"},
            {"x": _DAY2_MS, "y": 1.05, "rating": "extreme fear"},
        ],
    },
}


def test_parse_extracts_ratios_with_iso_dates():
    points = put_call.parse_response(SAMPLE_PAYLOAD)
    assert len(points) == 2
    assert points[0].date == "2026-06-30"
    assert points[0].ratio == 0.92
    assert points[1].date == "2026-07-01"
    assert points[1].ratio == 1.05


def test_parse_dedupes_same_day_keeping_last():
    payload = {
        "put_call_options": {
            "data": [
                {"x": _DAY1_MS, "y": 0.9},
                {"x": _DAY1_MS + 3_600_000, "y": 0.95},  # same day, 1h later
            ]
        }
    }
    points = put_call.parse_response(payload)
    assert len(points) == 1
    assert points[0].ratio == 0.95


def test_parse_drops_out_of_range_ratios():
    payload = {
        "put_call_options": {
            "data": [
                {"x": _DAY1_MS, "y": 9.9},   # implausible
                {"x": _DAY2_MS, "y": 0.05},  # implausible
            ]
        }
    }
    assert put_call.parse_response(payload) == []


def test_parse_skips_malformed_entries():
    payload = {
        "put_call_options": {
            "data": [
                {"x": _DAY1_MS},           # missing y
                {"y": 0.9},                # missing x
                {"x": "n/a", "y": 0.9},    # bad x
                "not-a-dict",
                {"x": _DAY2_MS, "y": 0.88},
            ]
        }
    }
    points = put_call.parse_response(payload)
    assert len(points) == 1
    assert points[0].ratio == 0.88


def test_parse_returns_empty_when_key_absent():
    assert put_call.parse_response({}) == []
    assert put_call.parse_response({"fear_and_greed": {"score": 42.0}}) == []
    assert put_call.parse_response({"put_call_options": {}}) == []
    assert put_call.parse_response({"put_call_options": None}) == []
