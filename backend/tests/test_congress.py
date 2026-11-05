"""Congress trades: the House Clerk's index and PTRs (fixtures cut from real
2026 filings, by way of trader-skill's tests), each PTR read once, the Senate
aggregator, and replacing the old aggregator's House rows."""
from datetime import date
from pathlib import Path

import httpx
import pytest

from app import config, db
from app.models import CongressTrade
from app.sources import congress
from app.sources.congress import _make_hash, _normalize_type, _parse_date

FIX = Path(__file__).parent / "fixtures" / "congress"
INDEX = (FIX / "2026FD.zip").read_bytes()
TEXT = {"20035143": (FIX / "ptr-20035143.txt").read_text(), "20035590": (FIX / "ptr-20035590.txt").read_text()}
TODAY = date(2026, 10, 13)
SENATE = [{"first_name": "Alice", "last_name": "Brown", "party": "D", "state": "NY", "ticker": "NVDA",
           "asset_type": "Stock", "transaction_date": "09/05/2026", "type": "Purchase",
           "amount": "$50,001 - $100,000", "disclosure_date": "09/12/2026"},
          {"first_name": "Alice", "last_name": "Brown", "ticker": "--", "transaction_date": "09/05/2026",
           "type": "Purchase"}]


def test_helpers():
    assert _make_hash("Jane Smith", "AAPL") == _make_hash("jane smith", "aapl") and len(_make_hash("a")) == 16
    assert _parse_date("05/28/2026") == "2026-05-28" and _parse_date("6/17/27") == "2027-06-17"
    assert [_normalize_type(t) for t in ("purchase", "sale_full", "exchange")] == ["Purchase", "Sale", "Exchange"]


def test_the_index_lists_ptrs_only_newest_first():
    rows = congress.read_index(INDEX, 2026)
    pelosi = [r for r in rows if r.member == "Nancy Pelosi"]
    assert [r.doc_id for r in pelosi] == ["20035553", "20035143", "20034836", "20033725"]
    assert pelosi[1].filed_at == "2026-08-21" and pelosi[1].district == "CA11"
    assert pelosi[1].url == "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/20035143.pdf"
    assert len(rows) == 13 and rows[0].member == "Rick Larsen" and rows[-1].doc_id == "20033725"


def test_a_ptr_reads_into_rows_with_amounts_and_options():
    rows = congress.parse_ptr_text(TEXT["20035143"])
    be = [r for r in rows if r["ticker"] == "BE"]
    assert [(r["asset_type"], r["date"], r["amount"]) for r in be[:2]] == [
        ("ST", "2026-07-24", "$1,000,001 - $5,000,000"), ("OP", "2026-07-24", "$1,000,001 - $5,000,000")]
    assert be[0]["asset"] == "Bloom Energy Corporation Class A Common Stock" and be[0]["owner"] == "SP"
    assert "100 call options with a strike price of $100" in be[1]["description"]
    assert any(r["ticker"] is None and "REOF XXV" in r["asset"] for r in rows)   # an LLC: no ticker


def test_trades_keep_a_stock_and_its_option_apart_and_link_the_filing():
    filing = next(f for f in congress.read_index(INDEX, 2026) if f.doc_id == "20035143")
    trades = congress.trades_from(filing, congress.parse_ptr_text(TEXT["20035143"]))
    be = [t for t in trades if t.ticker == "BE"]
    assert len({t.trade_hash for t in trades}) == len(trades) and len(be) == 4
    assert "100 call options, $100 strike, expires 2027-06-17" in be[1].asset_description
    assert be[0].transaction_type == "Purchase" and be[0].doc_id == "20035143" and be[0].source_url == filing.url
    partial = congress.trades_from(filing, congress.parse_ptr_text(TEXT["20035590"]))
    assert [(t.ticker, t.transaction_type) for t in partial] == [("AMAT", "Sale"), ("MCD", "Sale")]
    assert "partial sale" in partial[1].asset_description and "owner: joint" in partial[1].asset_description


class Clerk:
    """The Clerk and the Senate aggregator, counting PDF downloads."""

    def __init__(self, senate=SENATE, index=INDEX):
        self.pdfs, self.senate, self.index = [], senate, index

    def __call__(self, req):
        url = str(req.url)
        if url.endswith("FD.zip"):
            return httpx.Response(200, content=self.index) if self.index else httpx.Response(503)
        if url.endswith(".pdf"):
            self.pdfs.append(url.rsplit("/", 1)[1][:-4])
            return httpx.Response(200, content=url.encode())
        return httpx.Response(200, json=self.senate) if self.senate is not None else httpx.Response(500)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


@pytest.fixture
def fake_pdftotext(monkeypatch):
    monkeypatch.setattr(congress, "pdf_to_text", lambda exe, pdf: TEXT.get(pdf.decode().rsplit("/", 1)[1][:-4], ""))
    monkeypatch.setattr(congress, "_PAUSE_SECONDS", 0)


def _run(conn, clerk, **kw):
    out = congress.fetch(conn, 90, TODAY, clerk.client(), **kw)
    congress.store(conn, out)
    return out


def test_each_ptr_is_downloaded_once_and_scanned_ones_are_not_retried(conn, fake_pdftotext):
    clerk = Clerk()
    out = _run(conn, clerk, exe="pdftotext")
    assert sorted(clerk.pdfs) == ["20035143", "20035464", "20035549", "20035553", "20035558", "20035562",
                                  "20035580", "20035585", "20035590", "20035622", "20035635"]
    assert {t.ticker for t in out} >= {"BE", "INTC", "AMAT", "MCD", "NVDA"}
    assert "11 House PTRs since 2026-07-15, 2 read now" in out.note and not out.warning
    assert db.get_congress_filing_status(conn, ["20035143", "20035464"]) == {"20035143": "read",
                                                                            "20035464": "no rows"}
    again = Clerk()
    _run(conn, again, exe="pdftotext")
    assert again.pdfs == []
    assert any(t.ticker == "BE" and t.chamber == "house" for t in db.get_congress_trades(conn, 500))


def test_without_pdftotext_filings_are_listed_unread_and_the_run_says_so(conn, monkeypatch):
    monkeypatch.setattr(congress, "pdftotext_path", lambda: None)
    clerk = Clerk()
    out = _run(conn, clerk)
    assert clerk.pdfs == [] and "pdftotext isn't installed, so 11 House PTRs are listed but unread" in out.warning
    assert set(db.get_congress_filing_status(conn, ["20035143"]).values()) == {"unread"}


def test_the_ptr_batch_is_capped_and_the_rest_wait(conn, fake_pdftotext, monkeypatch):
    monkeypatch.setattr(config, "CONGRESS_PTR_PER_RUN", 2)
    clerk = Clerk()
    out = _run(conn, clerk, exe="pdftotext")
    assert clerk.pdfs == ["20035635", "20035622"] and "9 more House PTRs wait for the next run" in out.note


def test_old_aggregator_house_rows_go_once_the_clerk_is_read(conn, fake_pdftotext):
    db.upsert_congress_trades(conn, [CongressTrade(
        trade_hash="old", representative="Nancy Pelosi", party="D", state="CA", ticker="BE",
        asset_description="", transaction_date="2026-07-24", transaction_type="Purchase",
        amount_range="$1,000,001 - $5,000,000", filed_at="2026-08-21", chamber="house")])
    _run(conn, Clerk(), exe="pdftotext")
    assert "old" not in {t.trade_hash for t in db.get_congress_trades(conn, 500)}


def test_one_chamber_failing_keeps_the_other(conn, fake_pdftotext):
    out = _run(conn, Clerk(senate=None), exe="pdftotext")
    assert "Senate Stock Watcher unavailable" in out.warning and any(t.chamber == "house" for t in out)
    out = _run(conn, Clerk(index=None), exe="pdftotext")
    assert "House Clerk index unavailable" in out.warning and [t.ticker for t in out] == ["NVDA"]
    with pytest.raises(RuntimeError, match="House Clerk index unavailable.*Senate"):
        congress.fetch(conn, 90, TODAY, Clerk(index=None, senate=None).client(), exe="pdftotext")


def test_senate_rows_skip_blank_tickers():
    trades = congress.parse_senate(SENATE)
    assert [(t.representative, t.ticker, t.chamber, t.transaction_date) for t in trades] == [
        ("Alice Brown", "NVDA", "senate", "2026-09-05")]
