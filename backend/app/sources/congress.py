"""Congressional stock trades.

House: from the House Clerk itself, the primary source.
- The yearly disclosure index:
  disclosure ZIP disclosures-clerk.house.gov/public_disc/financial-pdfs/<YEAR>FD.zip
- Each Periodic Transaction Report (PTR) PDF it lists:
  .../ptr-pdfs/<YEAR>/<DocID>.pdf
- PDFs are read with `pdftotext -layout` (poppler; STOCKS_PDFTOTEXT points at
  it when it isn't on PATH). The index and PTR parsing are ported from
  trader-pelosi/ptr.py in Jake Schincariol's trader-skill (MIT,
  github.com/Jakeschincariol/trader-skill; see THIRD_PARTY_NOTICES.md).

Each PTR is read once:
- `congress_filings` remembers it, including a scanned paper filing that has
  no text to read. Without pdftotext the filings are still listed, and the
  run says so as a warning instead of reporting them read.

Senate: Senate PTRs sit behind an agreement page on efdsearch.senate.gov, so
they still come from the Senate Stock Watcher aggregator, flagged as such.

A PTR is a public STOCK Act disclosure, filed up to 45 days after the trade,
with amounts as ranges. It is not inside information.
"""
import hashlib
import io
import os
import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta, timezone

import httpx

from app import config, db, ingest
from app.models import CongressFiling, CongressTrade

INDEX_URL = "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip"
PTR_URL = "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc}.pdf"
_SENATE_URL = "https://senatestockwatcher.com/api"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Signal Dashboard)"}
_PAUSE_SECONDS = 0.5
_TXN_TYPES = {"P": "Purchase", "S": "Sale", "S (partial)": "Sale", "E": "Exchange"}
_OWNERS = {"SP": "spouse", "JT": "joint", "DC": "dependent child", "": "self"}


def _make_hash(*parts: str) -> str:
    return hashlib.sha256("|".join(p.upper() for p in parts).encode()).hexdigest()[:16]


def _parse_date(raw: str) -> str:
    """Normalize M/D/YYYY or YYYY-MM-DD to YYYY-MM-DD; return raw on failure."""
    if not raw:
        return ""
    raw = raw.strip()
    if len(raw) == 10 and raw[4] == "-":
        return raw
    parts = raw.split("/")
    if len(parts) == 3:
        m, d_str, y = parts
        y = f"20{y}" if len(y) == 2 else y
        return f"{y}-{m.zfill(2)}-{d_str.zfill(2)}"
    return raw


def _valid(iso: str) -> bool:
    try:
        date.fromisoformat(iso)
        return True
    except ValueError:
        return False


def _normalize_type(raw: str) -> str:
    tl = raw.lower()
    if "purchase" in tl or "buy" in tl:
        return "Purchase"
    if "sale" in tl or "sell" in tl:
        return "Sale"
    if "exchange" in tl:
        return "Exchange"
    return raw or "Unknown"


# ---------- House: the Clerk's index ----------

def read_index(data: bytes, year: int) -> list[CongressFiling]:
    """Every PTR (FilingType P) in a <YEAR>FD.zip, or its XML, newest first."""
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            name = next((n for n in z.namelist() if n.lower().endswith(".xml")), None)
            if name is None:
                raise ValueError(f"no XML in the House disclosure index ({', '.join(z.namelist())})")
            data = z.read(name)
    root = ET.fromstring(data)
    out = []
    for m in root.iter("Member"):
        f = {c.tag: (c.text or "").strip() for c in m}
        if f.get("FilingType") != "P" or not f.get("DocID"):
            continue
        y = int(f.get("Year") or year)
        out.append(CongressFiling(
            doc_id=f["DocID"], chamber="house", member=f"{f.get('First', '')} {f.get('Last', '')}".strip(),
            district=f.get("StateDst", ""), filed_at=_parse_date(f.get("FilingDate", "")),
            url=PTR_URL.format(year=y, doc=f["DocID"])))
    out.sort(key=lambda r: (r.filed_at, r.doc_id), reverse=True)
    return out


# ---------- House: one PTR's text ----------

_TXN = re.compile(
    r"^\s*(?:(?P<owner>SP|JT|DC)\s+)?(?P<asset>.*?)\s+(?P<type>S \(partial\)|P|S|E)\s+"
    r"(?P<date>\d{2}/\d{2}/\d{4})\s+(?P<notified>\d{2}/\d{2}/\d{4})\s+(?P<amount>\S.*?)\s*$")
_LABEL = re.compile(r"^\s*(F(?:ILING)?\s+S(?:TATUS)?|D(?:ESCRIPTION)?|S(?:UBHOLDING)?\s+O(?:F)?|L(?:OCATION)?)"
                    r"\s*:\s*(.*)$", re.I)
_PAGE_HEADER = re.compile(r"^\s*(ID\s+Owner\s+Asset|Type\s+Date\b|Type$|Date$|Gains >$|\$200\?$)", re.I)
_END = re.compile(r"^\s*(\* For the complete|I CERTIFY|Digitally Signed|Filing ID #)", re.I)
_OPTION = re.compile(r"([\d,]+)\s+(call|put)\s+options?\b.*?strike price of \$([\d,.]+)"
                     r".*?expiration date of (\d{1,2}/\d{1,2}/\d{2,4})", re.I | re.S)


def parse_ptr_text(text: str) -> list[dict]:
    """Transaction rows from `pdftotext -layout` output of a House PTR:
    owner, asset, ticker, asset_type, type, date, amount, description."""
    rows, cur, mode, amount_open = [], None, None, False
    for raw in text.splitlines():
        line, s = raw.rstrip(), raw.strip()
        t = _TXN.match(line)
        if t and _valid(_parse_date(t.group("date"))):
            if cur:
                rows.append(cur)
            cur = {"owner": t.group("owner") or "", "asset": t.group("asset"), "type": t.group("type"),
                   "date": _parse_date(t.group("date")), "amount": t.group("amount"), "description": ""}
            amount_open, mode = cur["amount"].endswith("-"), "asset"
            continue
        if cur is None or _PAGE_HEADER.match(s):
            continue
        if not s:
            if mode == "description" or (mode == "asset" and "[" in cur["asset"]):
                mode = "labels"
            continue
        if _END.match(s):
            mode = "labels"
            continue
        label = _LABEL.match(line)
        if label:
            if label.group(1).upper().startswith("D"):
                cur["description"], mode = label.group(2).strip(), "description"
            else:
                mode = "labels"
            continue
        if mode == "asset":
            parts = re.split(r"\s{2,}", s)
            if amount_open and re.fullmatch(r"\$[\d,]+", parts[-1]):
                cur["amount"] += " " + parts.pop()
                amount_open = False
            if parts and parts[0]:
                cur["asset"] += " " + " ".join(parts)
        elif mode == "description":
            cur["description"] += " " + s
    if cur:
        rows.append(cur)
    for r in rows:
        asset = " ".join(r["asset"].split())
        tick = re.findall(r"\(([A-Z][A-Z0-9.\-/]{0,9})\)", asset)
        code = re.findall(r"\[([A-Z]{2})\]", asset)
        r["ticker"] = tick[-1] if tick else None
        r["asset_type"] = code[-1] if code else None
        name = re.sub(r"\s*\[[A-Z]{2}\]", "", asset)
        r["asset"] = (re.sub(r"\s*\(%s\)" % re.escape(tick[-1]), "", name) if tick else name).strip(" -")
        r["amount"] = " ".join(r["amount"].split())
        r["description"] = " ".join(r["description"].split())
    return rows


def _describe(r: dict) -> str:
    bits = [r["asset"]]
    opt = _OPTION.search(r["description"])
    if opt:
        bits.append(f"{opt.group(1)} {opt.group(2).lower()} options, ${opt.group(3)} strike, "
                    f"expires {_parse_date(opt.group(4))}")
    if r["type"] == "S (partial)":
        bits.append("partial sale")
    if r["owner"]:
        bits.append(f"owner: {_OWNERS.get(r['owner'], r['owner'])}")
    return " — ".join(bits)


def trades_from(filing: CongressFiling, rows: list[dict]) -> list[CongressTrade]:
    """The rows with a ticker, keyed by the filing and the row's place in it
    (a stock and an option on it can share a day, a member and a type)."""
    return [CongressTrade(
        trade_hash=_make_hash("house", filing.doc_id, str(i)), representative=filing.member, party="",
        state=filing.district[:2], ticker=r["ticker"].replace(".", "-"), asset_description=_describe(r),
        transaction_date=r["date"], transaction_type=_TXN_TYPES.get(r["type"], r["type"]),
        amount_range=r["amount"], filed_at=filing.filed_at, chamber="house",
        doc_id=filing.doc_id, source_url=filing.url) for i, r in enumerate(rows) if r["ticker"]]


def pdftotext_path() -> str | None:
    return config.PDFTOTEXT or shutil.which("pdftotext")


def pdf_to_text(exe: str, pdf: bytes) -> str:
    """Closed before pdftotext opens it: Windows won't share an open file."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
        fh.write(pdf)
    try:
        out = subprocess.run([exe, "-layout", fh.name, "-"], capture_output=True, timeout=60, check=True)
    finally:
        os.unlink(fh.name)
    return out.stdout.decode("utf-8", "replace")


# ---------- Senate: the aggregator ----------

def parse_senate(payload: list[dict]) -> list[CongressTrade]:
    out, seen = [], set()
    for row in payload:
        if not isinstance(row, dict):
            continue
        rep = f"{row.get('first_name') or ''} {row.get('last_name') or ''}".strip()
        ticker = (row.get("ticker") or "").strip().upper()
        if not ticker or ticker == "--" or not rep:
            continue
        txn_date = _parse_date(row.get("transaction_date") or "")
        kind = _normalize_type(row.get("type") or "")
        h = _make_hash(rep, ticker, txn_date, kind)
        if h in seen:
            continue
        seen.add(h)
        out.append(CongressTrade(
            trade_hash=h, representative=rep, party=(row.get("party") or "").strip(),
            state=(row.get("state") or "").strip(), ticker=ticker,
            asset_description=(row.get("asset_description") or row.get("asset_type") or "").strip(),
            transaction_date=txn_date, transaction_type=kind, amount_range=(row.get("amount") or "").strip(),
            filed_at=_parse_date(row.get("disclosure_date") or ""), chamber="senate",
            source_url=row.get("ptr_link") or None))
    return out


# ---------- the run ----------

def _house(conn, client: httpx.Client, cutoff: str, today: date, exe: str | None, now: str):
    """(filings recorded, trades, how many listed, notes, warnings)."""
    listed = []
    for year in range(int(cutoff[:4]), today.year + 1):
        r = client.get(INDEX_URL.format(year=year))
        r.raise_for_status()
        listed += [f for f in read_index(r.content, year) if f.filed_at >= cutoff]
    done = db.get_congress_filing_status(conn, [f.doc_id for f in listed])
    todo = [f for f in listed if done.get(f.doc_id) not in ("read", "no rows")]
    if exe is None:
        warn = [f"pdftotext isn't installed, so {len(todo)} House PTRs are listed but unread"] if todo else []
        fresh = [f.model_copy(update={"status": "unread", "checked_at": now}) for f in todo if f.doc_id not in done]
        return fresh, [], len(listed), [], warn
    filings, trades, warn = [], [], []
    batch = todo[: config.CONGRESS_PTR_PER_RUN]
    for i, f in enumerate(batch):
        if i:
            time.sleep(_PAUSE_SECONDS)
        try:
            pdf = client.get(f.url)
            pdf.raise_for_status()
            rows = parse_ptr_text(pdf_to_text(exe, pdf.content))
        except (httpx.HTTPError, subprocess.SubprocessError, OSError) as exc:
            filings.append(f.model_copy(update={"status": "unread", "checked_at": now}))
            warn.append(f"PTR {f.doc_id} unread ({type(exc).__name__})")
            continue
        found = trades_from(f, rows)
        trades += found
        filings.append(f.model_copy(update={"status": "read" if rows else "no rows", "rows": len(found),
                                             "checked_at": now}))
    notes = [f"{len(todo) - len(batch)} more House PTRs wait for the next run"] if len(todo) > len(batch) else []
    return filings, trades, len(listed), notes, warn


def fetch(conn, lookback_days: int = 90, today: date | None = None, client: httpx.Client | None = None,
          exe: str | None = None):
    today = today or date.today()
    cutoff = (today - timedelta(days=lookback_days)).isoformat()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    own = client is None
    client = client or httpx.Client(timeout=60.0, headers=_HEADERS, follow_redirects=True)
    filings, trades, notes, warn, house_ok = [], [], [], [], False
    try:
        try:
            filings, trades, listed, house_notes, house_warn = _house(
                conn, client, cutoff, today, exe or pdftotext_path(), now)
            house_ok = True
            notes += [f"{listed} House PTRs since {cutoff}, {sum(f.status == 'read' for f in filings)} read now",
                      *house_notes]
            warn += house_warn
        except (httpx.HTTPError, ValueError, ET.ParseError, zipfile.BadZipFile) as exc:
            warn.append(f"House Clerk index unavailable ({type(exc).__name__})")
        try:
            r = client.get(_SENATE_URL)
            r.raise_for_status()
            trades += [t for t in parse_senate(r.json()) if t.transaction_date >= cutoff]
        except (httpx.HTTPError, ValueError) as exc:
            warn.append(f"Senate Stock Watcher unavailable ({type(exc).__name__})")
    finally:
        if own:
            client.close()
    if not house_ok and not trades:
        raise RuntimeError("; ".join(warn))
    result = ingest.FetchResult(trades, note="; ".join(notes), warning="; ".join(warn))
    result.filings = filings
    result.house_index_read = house_ok
    return result


def store(conn, trades: list[CongressTrade]) -> None:
    """Filings and trades. Once the Clerk's index has been read, House rows
    from the old aggregator (no doc_id) go, so a trade isn't counted twice
    under two spellings of a name."""
    if getattr(trades, "house_index_read", False):
        db.delete_legacy_house_trades(conn)
    db.upsert_congress_filings(conn, getattr(trades, "filings", []))
    db.upsert_congress_trades(conn, trades)
