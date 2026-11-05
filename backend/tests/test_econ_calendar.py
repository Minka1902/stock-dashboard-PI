"""Economic calendar: pure parse helpers, importance classification, storage."""
from app import db
from app.sources import econ_calendar as ec


# --- Captured sample payloads (no network) ---

FMP_SAMPLE = [
    {
        "date": "2026-07-08 12:30:00",
        "country": "US",
        "event": "Consumer Price Index (YoY)",
        "impact": "High",
        "actual": 3.2,
        "estimate": 3.1,
        "previous": 3.0,
    },
    {
        "date": "2026-07-09 14:00:00",
        "country": "US",
        "event": "FOMC Meeting Minutes",
        "impact": "Medium",
        "actual": None,
        "estimate": None,
        "previous": None,
    },
    {
        "date": "2026-07-08 06:00:00",
        "country": "DE",  # filtered out when countries=["United States"]
        "event": "Trade Balance",
        "impact": "Low",
        "actual": "",
        "estimate": "5.0B",
        "previous": "4.8B",
    },
]

NASDAQ_SAMPLE = {
    "data": {
        "rows": [
            {
                "gmt": "13:30",
                "country": "United States",
                "eventName": "Non-Farm Payrolls",
                "actual": "",
                "consensus": "180K",
                "previous": "175K",
            },
            {
                "gmt": "All Day",
                "country": "United States",
                "eventName": "Bank Holiday",
                "actual": "-",
                "consensus": "-",
                "previous": "-",
            },
        ]
    }
}


def test_parse_fmp_maps_official_impact_and_country():
    events = ec.parse_fmp(FMP_SAMPLE, "2026-07-08T00:00:00+00:00")
    assert len(events) == 3
    cpi = events[0]
    assert cpi.event == "Consumer Price Index (YoY)"
    assert cpi.date == "2026-07-08" and cpi.time == "12:30"
    assert cpi.country == "United States"           # "US" normalized
    assert cpi.importance == "high" and cpi.importance_source == "fmp"
    assert cpi.actual == "3.2" and cpi.forecast == "3.1" and cpi.previous == "3.0"
    assert cpi.source == "fmp"
    # empty-string actual becomes None
    assert events[2].actual is None and events[2].forecast == "5.0B"


def test_parse_nasdaq_uses_curated_importance_and_time():
    events = ec.parse_nasdaq(NASDAQ_SAMPLE, "2026-07-08", "2026-07-08T00:00:00+00:00")
    assert len(events) == 2
    nfp = events[0]
    assert nfp.event == "Non-Farm Payrolls"
    assert nfp.time == "13:30"
    assert nfp.importance == "high" and nfp.importance_source == "curated"
    assert nfp.actual is None and nfp.forecast == "180K"
    assert nfp.source == "nasdaq"
    # "All Day" is not a HH:MM time
    assert events[1].time == ""


def test_parse_nasdaq_decodes_html_entities():
    """Nasdaq sends "&nbsp;" for a value that isn't out yet; it must read as
    missing, not as the literal entity text."""
    payload = {"data": {"rows": [{
        "eventName": "FOMC Member Williams Speaks", "country": "United States", "gmt": "04:10",
        "actual": "&nbsp;", "consensus": "", "previous": "&nbsp;",
    }, {
        "eventName": "S&amp;P Global PMI", "country": "United States", "gmt": "13:45",
        "actual": "52.1", "consensus": "51.8&nbsp;", "previous": "51.5",
    }]}}
    speech, pmi = ec.parse_nasdaq(payload, "2026-09-25", "2026-09-25T00:00:00+00:00")
    assert speech.actual is None and speech.previous is None and speech.forecast is None
    assert pmi.event == "S&P Global PMI"
    assert pmi.forecast == "51.8"


def test_classify_importance():
    assert ec.classify_importance("FOMC Rate Decision") == "high"
    assert ec.classify_importance("Core CPI (MoM)") == "high"
    assert ec.classify_importance("Initial Jobless Claims") == "medium"
    assert ec.classify_importance("ADP Employment Change") == "medium"
    assert ec.classify_importance("Redbook Index (YoY)") == "low"


def test_country_filter_and_stable_event_id():
    events = ec.parse_fmp(FMP_SAMPLE, "t")
    kept = [e for e in events if ec._matches_country(e.country, ["United States"])]
    assert {e.country for e in kept} == {"United States"}
    assert len(kept) == 2
    # event_id is stable for the same date+country+event
    assert events[0].event_id == ec._event_id("2026-07-08", "United States",
                                               "Consumer Price Index (YoY)")


def test_storage_round_trip(conn):
    events = ec.parse_nasdaq(NASDAQ_SAMPLE, "2026-07-08", "t")
    db.upsert_econ_events(conn, events)
    # wide window so the date-relative query includes our fixed 2026-07-08 rows
    got = db.get_econ_events(conn, days_ahead=4000, days_back=4000)
    assert {e.event for e in got} == {"Non-Farm Payrolls", "Bank Holiday"}

    # importance filter
    high = db.get_econ_events(conn, days_ahead=4000, days_back=4000, importance="high")
    assert [e.event for e in high] == ["Non-Farm Payrolls"]

    # upsert is idempotent (PK = event_id) and updates in place
    db.upsert_econ_events(conn, events)
    assert len(db.get_econ_events(conn, days_ahead=4000, days_back=4000)) == 2


def test_init_schema_decodes_entities_stored_before_the_fix(conn):
    """Rows written before the parser decoded entities are fixed once, in place."""
    conn.execute(
        "INSERT INTO econ_events (event_id, date, time, country, event, importance, "
        "importance_source, actual, forecast, previous, source, fetched_at) VALUES "
        "('e1', '2026-07-07', '', 'United States', 'S&amp;P PMI', 'low', 'curated', "
        "'&nbsp;', NULL, '51.8&nbsp;', 'nasdaq', '2026-07-08T00:00:00+00:00')")
    conn.execute("DELETE FROM data_migrations WHERE name = 'econ_html_entities_2026_09'")
    conn.commit()
    db.init_schema(conn)
    row = conn.execute("SELECT event, actual, previous FROM econ_events WHERE event_id = 'e1'").fetchone()
    assert tuple(row) == ("S&P PMI", None, "51.8")
