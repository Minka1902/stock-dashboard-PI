"""Index constituents for the scanner's universes (STC-70), from Wikipedia.

Keyless and public. Each page keeps its members in a table with a symbol
column; `parse_constituents` is pure and picks that table by its id
("constituents") or, failing that, by its header. A list much shorter than
the index means the page layout changed: that raises, so the source shows an
error and the previous list stays, rather than scanning a truncated universe.
"""
from datetime import datetime, timezone
from html.parser import HTMLParser

import httpx

from app import config
from app.models import UniverseMember
from app.validation import TICKER_RE

# name -> (page, Yahoo suffix, fewest members a correct parse can return)
PAGES = {
    "sp500": ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "", 450),
    "ndx100": ("https://en.wikipedia.org/wiki/Nasdaq-100", "", 90),
    "ta125": ("https://en.wikipedia.org/wiki/TA-125_Index", ".TA", 100),
}
_SYMBOL_HEADERS = ("symbol", "ticker", "ticker symbol")
_NAME_HEADERS = ("security", "company", "name")


class _Tables(HTMLParser):
    """Every <table> as (id, rows of cell texts)."""

    def __init__(self):
        super().__init__()
        self.tables: list[tuple[str, list[list[str]]]] = []
        self._depth = 0
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._depth += 1
            if self._depth == 1:
                self.tables.append((dict(attrs).get("id") or "", []))
        elif self._depth == 1 and tag == "tr":
            self.tables[-1][1].append([])
        elif self._depth == 1 and tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "table":
            self._depth -= 1
        elif self._depth == 1 and tag in ("td", "th") and self._cell is not None:
            if self.tables[-1][1]:
                self.tables[-1][1][-1].append(" ".join("".join(self._cell).split()))
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _column(header: list[str], names: tuple[str, ...]) -> int | None:
    low = [h.lower() for h in header]
    return next((low.index(n) for n in names if n in low), None)


def to_yahoo(symbol: str, suffix: str) -> str | None:
    """"BRK.B" -> "BRK-B" (Yahoo's spelling); TASE symbols get ".TA"."""
    s = symbol.strip().upper().replace(".", "-")
    if suffix:
        s = s.removesuffix(suffix.replace(".", "-")) + suffix
    return s if TICKER_RE.fullmatch(s) else None


def parse_constituents(html: str, suffix: str = "") -> list[tuple[str, str]]:
    """[(Yahoo ticker, name)] from the page's constituents table."""
    p = _Tables()
    p.feed(html)
    tables = sorted(p.tables, key=lambda t: t[0] != "constituents")
    for _, rows in tables:
        if not rows:
            continue
        sym = _column(rows[0], _SYMBOL_HEADERS)
        if sym is None:
            continue
        name = _column(rows[0], _NAME_HEADERS)
        out = {}
        for r in rows[1:]:
            if len(r) > sym and (t := to_yahoo(r[sym], suffix)):
                out.setdefault(t, r[name] if name is not None and len(r) > name else "")
        if out:
            return list(out.items())
    return []


def fetch(names: list[str]) -> list[UniverseMember]:
    """Fetch each requested universe. Any one that fails raises, naming it."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out: list[UniverseMember] = []
    with httpx.Client(timeout=30.0, headers={"User-Agent": config.SEC_USER_AGENT}, follow_redirects=True) as client:
        for name in names:
            url, suffix, minimum = PAGES[name]
            r = client.get(url)
            r.raise_for_status()
            members = parse_constituents(r.text, suffix)
            if len(members) < minimum:
                raise ValueError(f"{name}: found {len(members)} constituents, expected at least {minimum} "
                                 f"(the page layout may have changed)")
            out += [UniverseMember(universe=name, ticker=t, name=n, refreshed_at=now) for t, n in members]
    return out
