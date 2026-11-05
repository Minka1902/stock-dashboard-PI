from app.sources import yield_curve

# Minimal Treasury XML for two days; one entry has no 30yr yield.
SAMPLE_XML = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"
      xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
  <entry>
    <content type="application/xml">
      <m:properties>
        <d:NEW_DATE>2026-06-20T00:00:00</d:NEW_DATE>
        <d:BC_2YEAR>4.85</d:BC_2YEAR>
        <d:BC_10YEAR>4.62</d:BC_10YEAR>
        <d:BC_30YEAR>4.78</d:BC_30YEAR>
      </m:properties>
    </content>
  </entry>
  <entry>
    <content type="application/xml">
      <m:properties>
        <d:NEW_DATE>2026-06-19T00:00:00</d:NEW_DATE>
        <d:BC_2YEAR>4.90</d:BC_2YEAR>
        <d:BC_10YEAR>4.60</d:BC_10YEAR>
      </m:properties>
    </content>
  </entry>
</feed>"""


def test_parse_extracts_dates_and_yields():
    points = yield_curve.parse_response(SAMPLE_XML)
    assert len(points) == 2
    jun20 = next(p for p in points if p.date == "2026-06-20")
    assert jun20.yr2 == 4.85
    assert jun20.yr10 == 4.62
    assert jun20.yr30 == 4.78


def test_parse_computes_spread():
    points = yield_curve.parse_response(SAMPLE_XML)
    jun20 = next(p for p in points if p.date == "2026-06-20")
    assert jun20.spread == round(4.62 - 4.85, 4)


def test_parse_handles_missing_30yr():
    points = yield_curve.parse_response(SAMPLE_XML)
    jun19 = next(p for p in points if p.date == "2026-06-19")
    assert jun19.yr30 is None
    assert jun19.spread == round(4.60 - 4.90, 4)  # spread still computed from yr2/yr10


def test_parse_strips_time_from_date():
    points = yield_curve.parse_response(SAMPLE_XML)
    for p in points:
        assert "T" not in p.date
        assert len(p.date) == 10
