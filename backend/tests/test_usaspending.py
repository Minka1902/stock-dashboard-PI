from app.sources import usaspending

# A trimmed sample of the real /api/v2/search/spending_by_award/ response.
SAMPLE = {
    "results": [
        {
            "internal_id": 111,
            "Award ID": "FA8675-26-C-0001",
            "Recipient Name": "Acme Defense Inc",
            "Award Amount": 2000000000.0,
            "Awarding Agency": "Department of Defense",
            "Start Date": "2026-06-01",
            "generated_internal_id": "CONT_AWD_FA8675",
        },
        {
            "internal_id": 222,
            "Award ID": "NNX-26-D-0042",
            "Recipient Name": "Orbital Systems LLC",
            "Award Amount": 350000000.0,
            "Awarding Agency": "NASA",
            "Start Date": None,
            "generated_internal_id": "CONT_AWD_NNX",
        },
    ],
    "page_metadata": {"page": 1, "hasNext": False},
}


def test_parse_returns_normalized_records():
    records = usaspending.parse_response(SAMPLE)
    assert len(records) == 2
    first = records[0]
    assert first.external_id == "CONT_AWD_FA8675"
    assert first.award_id == "FA8675-26-C-0001"
    assert first.recipient_name == "Acme Defense Inc"
    assert first.amount == 2000000000.0
    assert first.awarding_agency == "Department of Defense"
    assert first.start_date == "2026-06-01"
    assert first.source == "usaspending"


def test_parse_handles_missing_start_date():
    records = usaspending.parse_response(SAMPLE)
    assert records[1].start_date == ""  # None becomes empty string


def test_parse_empty_results():
    assert usaspending.parse_response({"results": []}) == []
