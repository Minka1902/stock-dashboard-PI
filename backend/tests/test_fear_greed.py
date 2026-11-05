from app.sources import fear_greed

SAMPLE_PAYLOAD = {
    "fear_and_greed": {
        "score": 42.3,
        "rating": "Fear",
        "timestamp": "2026-06-22T10:00:00+00:00",
    }
}


def test_parse_extracts_score_and_rating():
    snaps = fear_greed.parse_response(SAMPLE_PAYLOAD, "2026-06-22T10:00:00+00:00")
    assert len(snaps) == 1
    assert snaps[0].score == 42.3
    assert snaps[0].rating == "Fear"
    assert snaps[0].captured_at == "2026-06-22T10:00:00+00:00"


def test_parse_rounds_score():
    payload = {"fear_and_greed": {"score": 42.345678, "rating": "Fear"}}
    snaps = fear_greed.parse_response(payload, "2026-06-22T10:00:00+00:00")
    assert snaps[0].score == 42.3


def test_parse_returns_empty_on_missing_key():
    assert fear_greed.parse_response({}, "2026-06-22T10:00:00+00:00") == []


def test_parse_returns_empty_on_missing_score():
    payload = {"fear_and_greed": {"rating": "Fear"}}
    assert fear_greed.parse_response(payload, "2026-06-22T10:00:00+00:00") == []


def test_parse_returns_empty_on_bad_score_type():
    payload = {"fear_and_greed": {"score": "n/a", "rating": "Fear"}}
    assert fear_greed.parse_response(payload, "2026-06-22T10:00:00+00:00") == []
