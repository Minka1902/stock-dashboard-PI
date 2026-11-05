"""Famous investors' 13F holdings, from SEC EDGAR.

Ported from trader-copycat/edgar13f.py in Jake Schincariol's trader-skill
(MIT, github.com/Jakeschincariol/trader-skill; THIRD_PARTY_NOTICES.md).

Per filer in data/filers13f.py, each run makes one request: its submissions
list. Only when a new original 13F-HR appears does it read that quarter's
information table and the previous quarter's. Then:
- positions are summed per CUSIP (per put/call),
- each one is diffed (new, added, trimmed, unchanged, sold out),
- tickers are matched: the verified share-class table first, then the
  issuer's name against SEC's own company list.

What a 13F is and is not:
- A snapshot of the quarter's last day, filed up to 45 days later.
- Long US-listed positions only: no shorts, cash or foreign shares.
- Amendments (13F-HR/A) aren't read; the original is.
- Values are in dollars from 2023-01-03 on, thousands before.
"""
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx

from app import config, db, ingest
from app.data.filers13f import FILERS, SHARE_CLASSES
from app.models import Alert, Filing13F, Holding13F

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_PAUSE_SECONDS = 0.15   # SEC asks for at most 10 requests a second
_DOLLARS_FROM = "2023-01-03"
_RANK = {"NEW": 0, "ADDED": 1, "SOLD OUT": 2, "TRIMMED": 3, "UNCHANGED": 4}


def filers() -> list[tuple[str, str, str]]:
    wanted = {c.zfill(10) for c in config.FILERS_13F}
    return [f for f in FILERS if not wanted or f[0] in wanted]


def originals(submissions: dict) -> list[dict]:
    """The original 13F-HR filings, newest period first."""
    rec = (submissions.get("filings") or {}).get("recent") or {}
    out = [{"accession": a, "filed": f, "period": p}
           for form, a, f, p in zip(rec.get("form", []), rec.get("accessionNumber", []),
                                    rec.get("filingDate", []), rec.get("reportDate", []))
           if form == "13F-HR"]
    return sorted(out, key=lambda x: (x["period"], x["filed"]), reverse=True)


def info_table_name(index_json: dict) -> str:
    names = [i["name"] for i in (index_json.get("directory") or {}).get("item", [])]
    xmls = [n for n in names if n.lower().endswith(".xml") and n.lower() not in ("primary_doc.xml",
                                                                                 "filingsummary.xml")]
    if not xmls:
        raise ValueError("no information table XML in this filing")
    return next((n for n in xmls if re.search(r"info|table|13f", n, re.I)), xmls[0])


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_info_table(data: bytes, filed: str) -> list[dict]:
    """Positions summed per (CUSIP, put/call), with value in dollars."""
    mult = 1 if filed >= _DOLLARS_FROM else 1000
    groups: dict[tuple, dict] = {}
    for el in ET.fromstring(data).iter():
        if _local(el.tag) != "infoTable":
            continue
        f = {}
        for child in el.iter():
            name = _local(child.tag)
            if name not in f and child.text and child.text.strip():
                f[name] = child.text.strip()
        key = (f.get("cusip", "").upper(), f.get("putCall", "").upper())
        g = groups.setdefault(key, {"issuer": f.get("nameOfIssuer", ""), "title": f.get("titleOfClass", ""),
                                    "cusip": key[0], "put_call": key[1], "value": 0.0, "shares": 0.0})
        g["value"] += float(f.get("value", "0").replace(",", "")) * mult
        g["shares"] += float(f.get("sshPrnamt", "0").replace(",", ""))
    if not groups:
        raise ValueError("no infoTable rows in this information table")
    return sorted(groups.values(), key=lambda p: (-p["value"], p["cusip"]))


# ---------- tickers ----------

_SUFFIX_STATE = re.compile(r"\s*/\s*[A-Z]{2,5}\s*/?\s*$")
_ABBREV = {"AMER": "AMERICA", "AMERN": "AMERICAN", "FINL": "FINANCIAL", "FIN": "FINANCIAL",
           "PETE": "PETROLEUM", "HLDGS": "HOLDINGS", "HLDG": "HOLDING", "HOLDG": "HOLDING",
           "INTL": "INTERNATIONAL", "GRP": "GROUP", "SVCS": "SERVICES", "SVC": "SERVICE",
           "SYS": "SYSTEMS", "MGMT": "MANAGEMENT", "INDS": "INDUSTRIES", "NATL": "NATIONAL",
           "ENTMT": "ENTERTAINMENT", "PAC": "PACIFIC", "LABS": "LABORATORIES", "PPTYS": "PROPERTIES",
           "RLTY": "REALTY", "TECHS": "TECHNOLOGIES", "TECH": "TECHNOLOGY", "COMMNS": "COMMUNICATIONS",
           "COMMUNICATN": "COMMUNICATIONS", "MFG": "MANUFACTURING", "ELEC": "ELECTRIC",
           "PHARMS": "PHARMACEUTICALS", "BANCORPORATION": "BANCORP", "RES": "RESOURCES", "WTR": "WATER",
           "MTRS": "MOTORS", "AIRLS": "AIRLINES", "CHEM": "CHEMICAL"}
_DROP = {"INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PLC", "LLC",
         "LP", "SA", "NV", "AG", "SE", "THE", "AND", "OF"}
_NONCOMMON = re.compile(r"-P[A-Z]*$|-WT[A-Z]*$|-WS$|-U$|-UN$|-R$|-RT$")


def _keys(name: str) -> tuple[str, str]:
    s = (name or "").upper().replace("&", " AND ")
    for _ in range(2):
        s = _SUFFIX_STATE.sub("", s)
    s = re.sub(r"[^A-Z0-9 ]+", " ", s.replace("'", "").replace("’", ""))
    words = [w for w in (_ABBREV.get(w, w) for w in s.split()) if w not in _DROP]
    return "".join(words), " ".join(sorted(words))


class TickerIndex:
    """Issuer name -> ticker over SEC's company_tickers.json."""

    def __init__(self, company_tickers: dict):
        self.by_cik: dict[int, list[str]] = {}
        self.spaceless: dict[str, set[int]] = {}
        self.sorted_words: dict[str, set[int]] = {}
        for v in company_tickers.values():
            cik = int(v["cik_str"])
            first = cik not in self.by_cik
            self.by_cik.setdefault(cik, []).append(v["ticker"].upper())
            if first:
                a, b = _keys(v["title"])
                if a:
                    self.spaceless.setdefault(a, set()).add(cik)
                    self.sorted_words.setdefault(b, set()).add(cik)

    def _cik(self, issuer: str) -> tuple[int | None, bool]:
        a, b = _keys(issuer)
        if not a:
            return None, False
        for hits in (self.spaceless.get(a), self.sorted_words.get(b)):
            if hits and len(hits) == 1:
                return next(iter(hits)), False
        best = sorted(((len(k), next(iter(c))) for k, c in self.spaceless.items()
                       if len(k) >= 8 and len(c) == 1 and (a.startswith(k) or k.startswith(a))), reverse=True)
        if best and (len(best) == 1 or best[0][0] > best[1][0]):
            return best[0][1], True
        return None, False

    def ticker(self, issuer: str, title: str) -> tuple[str | None, str]:
        """(ticker, how it was matched)."""
        cik, by_prefix = self._cik(issuer)
        if cik is None:
            return None, "unmatched"
        tickers = self.by_cik[cik]
        cands = [t for t in tickers if not _NONCOMMON.search(t)] or tickers
        how = "name (prefix)" if by_prefix else "name"
        if len(cands) == 1 or all(t.startswith(cands[0]) for t in cands[1:]):
            return cands[0], how
        letter = re.search(r"\b(?:CL|CLASS|SER|SERIES)\s*([A-Z])\b", (title or "").upper())
        if letter:
            hit = next((t for t in cands if t.endswith(("-" + letter.group(1), "." + letter.group(1)))), None)
            if hit:
                return hit, how
        return cands[0], how + ", class unsure"


def resolve(positions: list[dict], index: TickerIndex | None) -> None:
    for p in positions:
        if p["cusip"] in SHARE_CLASSES:
            p["ticker"], p["ticker_source"] = SHARE_CLASSES[p["cusip"]], "share-class table"
        elif index is not None:
            p["ticker"], p["ticker_source"] = index.ticker(p["issuer"], p["title"])
        else:
            p["ticker"], p["ticker_source"] = None, "unmatched"


def diff(current: list[dict], prior: list[dict]) -> list[dict]:
    """Every current position with its change since the prior quarter, plus
    what was sold out."""
    before = {(p["cusip"], p["put_call"]): p for p in prior}
    now = {(p["cusip"], p["put_call"]) for p in current}
    out = []
    for p in current:
        q = before.get((p["cusip"], p["put_call"]))
        prev = q["shares"] if q else 0.0
        status = "NEW" if q is None else "ADDED" if p["shares"] > prev else "TRIMMED" if p["shares"] < prev \
            else "UNCHANGED"
        out.append({**p, "prev_shares": prev, "prev_value": q["value"] if q else 0.0,
                    "change_pct": round((p["shares"] - prev) / prev * 100, 1) if prev else None, "status": status})
    out += [{**q, "value": 0.0, "shares": 0.0, "prev_shares": q["shares"], "prev_value": q["value"],
             "change_pct": -100.0, "status": "SOLD OUT"} for k, q in before.items() if k not in now]
    return out


# ---------- the run ----------

class _Sec:
    def __init__(self, client: httpx.Client):
        self.client, self.calls = client, 0

    def get(self, url: str) -> httpx.Response:
        if self.calls:
            time.sleep(_PAUSE_SECONDS)
        self.calls += 1
        r = self.client.get(url)
        r.raise_for_status()
        return r


def _quarter(sec: _Sec, cik: str, filing: dict) -> list[dict]:
    base = ARCHIVE.format(cik=int(cik), folder=filing["accession"].replace("-", ""))
    table = info_table_name(sec.get(base + "index.json").json())
    return parse_info_table(sec.get(base + table).content, filing["filed"])


def fetch(conn, client: httpx.Client | None = None, now: datetime | None = None):
    at = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    own = client is None
    client = client or httpx.Client(timeout=60.0, headers={"User-Agent": config.SEC_USER_AGENT},
                                    follow_redirects=True)
    sec, index = _Sec(client), None
    known = {f.cik: f for f in db.get_filings_13f(conn)}
    out: list[Holding13F] = []
    filings, new, problems = [], [], []
    try:
        for cik, name, person in filers():
            try:
                latest = originals(sec.get(SUBMISSIONS.format(cik=cik)).json())
                if not latest:
                    problems.append(f"{name}: no 13F-HR filings")
                    continue
                cur, prior = latest[0], next((f for f in latest[1:] if f["period"] < latest[0]["period"]), None)
                if cik in known and known[cik].accession == cur["accession"]:
                    continue
                positions = diff(_quarter(sec, cik, cur), _quarter(sec, cik, prior) if prior else [])
                if index is None:
                    index = TickerIndex(sec.get(TICKERS_URL).json())
                resolve(positions, index)
            except (httpx.HTTPError, ValueError, ET.ParseError) as exc:
                problems.append(f"{name}: {type(exc).__name__}")
                continue
            total = sum(p["value"] for p in positions)
            filings.append(Filing13F(
                cik=cik, filer=name, person=person, period=cur["period"], filed=cur["filed"],
                accession=cur["accession"], prior_period=prior["period"] if prior else None,
                positions=sum(p["status"] != "SOLD OUT" for p in positions), total_value=total, checked_at=at))
            out += [Holding13F(cik=cik, cusip=p["cusip"], put_call=p["put_call"], issuer=p["issuer"],
                               title=p["title"], ticker=p["ticker"], ticker_source=p["ticker_source"],
                               value=p["value"], shares=p["shares"], prev_shares=p["prev_shares"],
                               prev_value=p["prev_value"],
                               pct=round(p["value"] / total * 100, 2) if total else 0.0,
                               change_pct=p["change_pct"], status=p["status"]) for p in positions]
            new.append(f"{name} {cur['period']}")
    finally:
        if own:
            client.close()
    if problems and not filings and len(problems) == len(filers()):
        raise RuntimeError("; ".join(problems))
    note = f"{len(filers())} filers checked, " + (f"new: {', '.join(new)}" if new else "no new 13F")
    result = ingest.FetchResult(out, note=note, warning="; ".join(problems))
    result.filings = filings
    return result


def store(conn, holdings: list[Holding13F]) -> None:
    """Each new quarter replaces its filer's holdings. A new or added stock
    position in a ticker someone holds or watches is a (market-wide) alert."""
    filings = getattr(holdings, "filings", [])
    db.replace_holdings_13f(conn, filings, holdings)
    names = {f.cik: f for f in filings}
    followed = set(db.get_all_watched_tickers(conn)) | set(db.get_all_held_tickers(conn))
    alerts = []
    for h in holdings:
        f = names[h.cik]
        if h.status not in ("NEW", "ADDED") or h.put_call or h.ticker not in followed:
            continue
        verb = "opened a position in" if h.status == "NEW" else f"added {h.change_pct:+.0f}% to"
        alerts.append(Alert(
            dedup_key=f"13f|{h.cik}|{f.accession}|{h.cusip}", created_at=f.checked_at, ticker=h.ticker,
            type="investor_13f", severity="medium",
            title=f"{f.filer} {verb} {h.ticker}",
            message=(f"{f.filer} ({f.person}) {verb} {h.ticker}: {h.shares:,.0f} shares, {h.pct:.2f}% of its "
                     f"13F, as of {f.period} (filed {f.filed}). A 13F is up to 45 days old when filed.")))
    db.upsert_alerts(conn, alerts)


def holders_of(conn, ticker: str) -> list[dict]:
    """Who among the tracked filers holds `ticker`, biggest weight first."""
    filings = {f.cik: f for f in db.get_filings_13f(conn)}
    return [{**h.model_dump(), "filer": filings[h.cik].filer, "person": filings[h.cik].person,
             "period": filings[h.cik].period, "filed": filings[h.cik].filed}
            for h in db.get_holdings_13f(conn, ticker=ticker) if h.cik in filings]


def overview(conn, limit: int = 40) -> dict:
    """Each filer's latest quarter and the biggest moves across all of them."""
    filings = db.get_filings_13f(conn)
    by = {f.cik: f for f in filings}
    moves = [h for h in db.get_holdings_13f(conn) if h.status in ("NEW", "ADDED", "SOLD OUT", "TRIMMED")
             and not h.put_call and h.cik in by]
    moves.sort(key=lambda h: (_RANK[h.status], -max(h.value, h.prev_value)))
    return {"filers": [f.model_dump() for f in filings],
            "moves": [{**h.model_dump(), "filer": by[h.cik].filer, "person": by[h.cik].person,
                       "period": by[h.cik].period} for h in moves[:limit]]}
