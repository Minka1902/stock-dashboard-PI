"""Theme classifier (app/themes.py, Task 11)."""
from app import themes


def test_curated_ticker_map_wins():
    assert themes.classify("NVDA", "Technology", "Semiconductors") == "AI"
    assert themes.classify("RKLB", None, None) == "Space"
    assert themes.classify("LMT", None, None) == "Defense"
    assert themes.classify("COIN", "Financial Services", None) == "Crypto"


def test_keyword_industry_rules():
    assert themes.classify("XYZ", "Technology", "Semiconductors") == "Semiconductors"
    assert themes.classify("XYZ", "Healthcare", "Biotechnology") == "Medicine"
    assert themes.classify("XYZ", "Industrials", "Aerospace & Defense") == "Defense"
    assert themes.classify("XYZ", "Energy", "Oil & Gas E&P") == "Energy"
    assert themes.classify("XYZ", "Financial Services", "Banks—Regional") == "Finance"
    assert themes.classify("XYZ", "Technology", "Software—Application") == "Tech"


def test_case_insensitive():
    assert themes.classify("XYZ", "TECHNOLOGY", "SEMICONDUCTORS") == "Semiconductors"


def test_fallback_other():
    assert themes.classify("XYZ", "Basic Materials", "Gold") == "Other"
    assert themes.classify("XYZ", None, None) == "Other"
    assert themes.classify("", None, None) == "Other"


def test_all_returned_themes_are_valid():
    for tkr, sector, industry in [
        ("NVDA", None, None), ("XYZ", "Healthcare", "Biotech"),
        ("XYZ", None, None),
    ]:
        assert themes.classify(tkr, sector, industry) in themes.THEMES
