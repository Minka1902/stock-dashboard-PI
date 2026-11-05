"""SEC EDGAR insider-trades source (Form 4).

Flow:
  1. fetch the "latest filings" atom feed for form type 4
  2. parse it into a deduped list of Form 4 filings (one per accession)
  3. for each, locate and fetch the ownership XML, parse the share transactions

`parse_atom` and `parse_form4_xml` are pure (no network) and unit-tested.
`fetch` does the throttled HTTP orchestration.

SEC asks clients to send a descriptive User-Agent and stay under ~10 req/sec.
"""
import re
import time

import httpx

from app.models import InsiderTrade

ATOM_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
THROTTLE_SECONDS = 0.15  # stay well under SEC's 10 req/sec guidance

_ACCESSION_RE = re.compile(r"(\d{10}-\d{2}-\d{6})")

# transactionCode -> human label. P/S are the meaningful open-market signals.
_CODE_LABEL = {
    "P": "Buy",
    "S": "Sell",
    "A": "Grant",
    "M": "Exercise",
    "G": "Gift",
    "F": "Tax",
    "X": "Exercise",
    "C": "Conversion",
}


def _tag(text: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.S)
    return m.group(1).strip() if m else None


def _value(text: str, tag: str) -> str | None:
    """Extract <tag><value>X</value></tag> (EDGAR's nested value form)."""
    m = re.search(rf"<{tag}>\s*<value>(.*?)</value>", text, re.S)
    return m.group(1).strip() if m else None


def parse_atom(atom_text: str) -> list[dict]:
    """Return deduped Form 4 filings: [{accession, index_url, filed_at}]."""
    out: list[dict] = []
    seen: set[str] = set()
    for entry in re.findall(r"<entry>(.*?)</entry>", atom_text, re.S):
        if 'term="4"' not in entry:
            continue
        href_m = re.search(r'href="([^"]+)"', entry)
        if not href_m:
            continue
        href = href_m.group(1)
        acc_m = _ACCESSION_RE.search(href)
        if not acc_m:
            continue
        accession = acc_m.group(1)
        if accession in seen:
            continue
        seen.add(accession)
        filed_at = _tag(entry, "updated") or ""
        out.append({"accession": accession, "index_url": href, "filed_at": filed_at})
    return out


def parse_form4_xml(
    xml_text: str, accession: str, index_url: str, filed_at: str
) -> InsiderTrade | None:
    """Parse one Form 4 ownership XML into an aggregated InsiderTrade.

    Returns None if the filing has no non-derivative share transactions.
    """
    company = _tag(xml_text, "issuerName") or ""
    ticker = (_tag(xml_text, "issuerTradingSymbol") or "").upper()
    owner = _tag(xml_text, "rptOwnerName") or ""

    officer_title = _tag(xml_text, "officerTitle")
    is_director = (_value(xml_text, "isDirector") or _tag(xml_text, "isDirector") or "").lower()
    is_ten = (_value(xml_text, "isTenPercentOwner") or _tag(xml_text, "isTenPercentOwner") or "").lower()
    if officer_title:
        role = officer_title
    elif is_director in ("1", "true"):
        role = "Director"
    elif is_ten in ("1", "true"):
        role = "10% Owner"
    else:
        role = "Insider"

    # Skip entire filing if it's a pre-arranged 10b5-1 automatic plan.
    footnotes = " ".join(re.findall(r"<footnote[^>]*>(.*?)</footnote>", xml_text, re.S))
    if "10b5-1" in footnotes.lower():
        return None

    blocks = re.findall(
        r"<nonDerivativeTransaction>(.*?)</nonDerivativeTransaction>", xml_text, re.S
    )
    # Only open-market purchases (P) and sales (S) carry informational content.
    open_market_blocks = [b for b in blocks
                          if (_tag(b, "transactionCode") or "").strip().upper() in ("P", "S")]

    if not open_market_blocks:
        return None

    total_shares = 0.0
    total_value = 0.0
    latest_date = ""
    best_value = -1.0
    best_code = ""
    for b in open_market_blocks:
        try:
            shares = float(_value(b, "transactionShares") or 0)
        except ValueError:
            shares = 0.0
        try:
            price = float(_value(b, "transactionPricePerShare") or 0)
        except ValueError:
            price = 0.0
        code = (_tag(b, "transactionCode") or "").strip().upper()
        date = _value(b, "transactionDate") or ""
        line_value = shares * price
        total_shares += shares
        total_value += line_value
        if date > latest_date:
            latest_date = date
        if line_value >= best_value:
            best_value = line_value
            best_code = code

    return InsiderTrade(
        accession=accession,
        ticker=ticker,
        company=company,
        owner=owner,
        role=role,
        transaction_date=latest_date,
        transaction_type=_CODE_LABEL.get(best_code, "Other"),
        shares=round(total_shares, 2),
        value=round(total_value, 2),
        filing_url=index_url,
        filed_at=filed_at,
    )


def _find_xml_url(client: httpx.Client, index_url: str) -> str | None:
    folder = index_url.rsplit("/", 1)[0]
    data = client.get(folder + "/index.json").raise_for_status().json()
    names = [item["name"] for item in data["directory"]["item"]]
    candidates = [
        n for n in names if n.endswith(".xml") and not re.fullmatch(r"R\d+\.xml", n)
    ]
    if not candidates:
        return None
    return folder + "/" + candidates[0]


def fetch(limit: int, user_agent: str) -> list[InsiderTrade]:
    """Fetch and parse the most recent Form 4 filings into InsiderTrade rows."""
    headers = {"User-Agent": user_agent}
    trades: list[InsiderTrade] = []
    with httpx.Client(headers=headers, timeout=30.0) as client:
        atom = client.get(
            ATOM_URL,
            params={
                "action": "getcurrent",
                "type": "4",
                "owner": "include",
                "count": "100",
                "output": "atom",
            },
        ).raise_for_status().text
        filings = parse_atom(atom)[:limit]
        for f in filings:
            time.sleep(THROTTLE_SECONDS)
            try:
                xml_url = _find_xml_url(client, f["index_url"])
                if not xml_url:
                    continue
                time.sleep(THROTTLE_SECONDS)
                xml = client.get(xml_url).raise_for_status().text
                trade = parse_form4_xml(
                    xml, f["accession"], f["index_url"], f["filed_at"]
                )
                if trade and trade.ticker:
                    trades.append(trade)
            except (httpx.HTTPError, KeyError, ValueError):
                # Skip individual unparseable filings; keep the rest.
                continue
    return trades
