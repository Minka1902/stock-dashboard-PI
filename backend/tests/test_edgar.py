from app.sources import edgar

ATOM = """<?xml version="1.0"?>
<feed>
  <entry>
    <title>4 - Grody Howard B. (0001438277) (Reporting)</title>
    <link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/910612/000119312526276828/0001193125-26-276828-index.htm"/>
    <category term="4" label="form type"/>
    <updated>2026-06-22T09:11:58-04:00</updated>
  </entry>
  <entry>
    <title>4 - CBL ASSOCIATES (0000910612) (Issuer)</title>
    <link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/910612/000119312526276828/0001193125-26-276828-index.htm"/>
    <category term="4" label="form type"/>
    <updated>2026-06-22T09:11:58-04:00</updated>
  </entry>
  <entry>
    <title>497 - SOME FUND</title>
    <link rel="alternate" href="https://www.sec.gov/Archives/edgar/data/1/000000000000000001/x-index.htm"/>
    <category term="497" label="form type"/>
    <updated>2026-06-22T09:00:00-04:00</updated>
  </entry>
</feed>"""

FORM4_XML = """<ownershipDocument>
  <issuer>
    <issuerName>CBL &amp; ASSOCIATES PROPERTIES INC</issuerName>
    <issuerTradingSymbol>CBL</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Grody Howard B.</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>0</isDirector>
      <isOfficer>1</isOfficer>
      <officerTitle>Exec VP-Leasing</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-06-18</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>4728</value></transactionShares>
        <transactionPricePerShare><value>48.058</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


def test_parse_atom_filters_form4_and_dedupes():
    filings = edgar.parse_atom(ATOM)
    # two entries share one accession (Reporting + Issuer) -> one filing;
    # the 497 entry is excluded.
    assert len(filings) == 1
    assert filings[0]["accession"] == "0001193125-26-276828"
    assert filings[0]["index_url"].endswith("-index.htm")
    assert filings[0]["filed_at"] == "2026-06-22T09:11:58-04:00"


def test_parse_form4_xml_aggregates_transaction():
    t = edgar.parse_form4_xml(
        FORM4_XML, "ACC-1", "https://sec.gov/x-index.htm", "2026-06-22T09:11:58-04:00"
    )
    assert t is not None
    assert t.ticker == "CBL"
    assert t.company.startswith("CBL")
    assert t.owner == "Grody Howard B."
    assert t.role == "Exec VP-Leasing"
    assert t.transaction_type == "Sell"
    assert t.shares == 4728.0
    assert round(t.value) == round(4728 * 48.058)
    assert t.transaction_date == "2026-06-18"


def test_parse_form4_xml_returns_none_without_transactions():
    xml = "<ownershipDocument><issuer><issuerTradingSymbol>X</issuerTradingSymbol></issuer></ownershipDocument>"
    assert edgar.parse_form4_xml(xml, "A", "u", "t") is None


# ---- 10b5-1 filtering ----

FORM4_10B51 = """<ownershipDocument>
  <issuer>
    <issuerName>ACME CORP</issuerName>
    <issuerTradingSymbol>ACME</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>John Doe</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector></reportingOwnerRelationship>
  </reportingOwner>
  <footnotes>
    <footnote id="F1">Transaction made pursuant to a Rule 10b5-1 trading plan.</footnote>
  </footnotes>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-06-18</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>50.0</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


def test_parse_form4_xml_skips_10b51_filings():
    result = edgar.parse_form4_xml(FORM4_10B51, "ACC-2", "https://sec.gov/y", "2026-06-22T00:00:00")
    assert result is None, "10b5-1 filings should be filtered out"


# ---- open-market-only filtering ----

FORM4_NON_OPEN_MARKET = """<ownershipDocument>
  <issuer>
    <issuerName>ACME CORP</issuerName>
    <issuerTradingSymbol>ACME</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Jane Smith</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isOfficer>1</isOfficer><officerTitle>CFO</officerTitle></reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-06-18</value></transactionDate>
      <transactionCoding><transactionCode>A</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>500</value></transactionShares>
        <transactionPricePerShare><value>0.0</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-06-19</value></transactionDate>
      <transactionCoding><transactionCode>F</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>100</value></transactionShares>
        <transactionPricePerShare><value>50.0</value></transactionPricePerShare>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""


def test_parse_form4_xml_skips_non_open_market_codes():
    result = edgar.parse_form4_xml(FORM4_NON_OPEN_MARKET, "ACC-3", "https://sec.gov/z", "2026-06-22T00:00:00")
    assert result is None, "Award (A) and tax-withholding (F) transactions should be filtered out"


def test_parse_form4_xml_keeps_open_market_purchase():
    xml = FORM4_XML  # contains transactionCode S (sell) which is open-market
    result = edgar.parse_form4_xml(xml, "ACC-1", "https://sec.gov/x", "2026-06-22T09:11:58-04:00")
    assert result is not None
    assert result.transaction_type == "Sell"
