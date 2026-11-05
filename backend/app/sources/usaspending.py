"""USASpending.gov federal contracts source.

`parse_response` is pure (no network) so it can be tested directly.
`fetch` is a thin HTTP wrapper around it.
"""
import httpx

from app.models import ContractRecord

API_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
# Contract award type codes: A, B, C, D (definitive/PO/DO/BPA call).
CONTRACT_TYPE_CODES = ["A", "B", "C", "D"]
FIELDS = [
    "Award ID",
    "Recipient Name",
    "Award Amount",
    "Awarding Agency",
    "Start Date",
]


def parse_response(payload: dict) -> list[ContractRecord]:
    records = []
    for row in payload.get("results", []):
        records.append(
            ContractRecord(
                external_id=str(row.get("generated_internal_id") or row.get("internal_id")),
                award_id=row.get("Award ID") or "",
                recipient_name=row.get("Recipient Name") or "",
                amount=float(row.get("Award Amount") or 0.0),
                awarding_agency=row.get("Awarding Agency") or "",
                start_date=row.get("Start Date") or "",
                source="usaspending",
            )
        )
    return records


def fetch(start_date: str, end_date: str, limit: int) -> list[ContractRecord]:
    """Call the live API and return normalized records. Raises on HTTP error."""
    body = {
        "filters": {
            "award_type_codes": CONTRACT_TYPE_CODES,
            "time_period": [{"start_date": start_date, "end_date": end_date}],
        },
        "fields": FIELDS,
        "sort": "Award Amount",
        "order": "desc",
        "limit": limit,
        "page": 1,
    }
    resp = httpx.post(API_URL, json=body, timeout=60.0)
    resp.raise_for_status()
    return parse_response(resp.json())
