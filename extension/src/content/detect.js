// Pure ticker detection. No DOM, no network — unit tested in tests/detect.test.js.
//
// Two matchers with very different confidence levels:
//
//   $TSLA  cashtag   - unambiguous, always annotated, no symbol set needed.
//   TSLA   bare word - annotated ONLY if it's a known symbol AND not a stopword.
//
// The bare-word path is where a ticker overlay goes wrong: ALL, IT, ON, NEW, KEY,
// DD and AI are all real listed symbols, and annotating them turns ordinary prose
// into confetti. Requiring membership in the backend's own symbol list and then
// subtracting an explicit stopword list is the whole false-positive control.

const CASHTAG_RE = /\$([A-Za-z]{1,5})(?:\.([A-Za-z]{1,2}))?\b/g;
const BARE_RE = /\b([A-Z]{2,5})(?:\.([A-Z]{1,2}))?\b/g;

/**
 * Words that are valid tickers but overwhelmingly appear as English or as
 * finance/forum jargon. Never annotated in bare form; `$ALL` still works.
 */
export const STOPWORDS = new Set([
  // English
  "ALL", "AN", "AND", "ANY", "ARE", "AS", "AT", "BE", "BUT", "BY", "CAN", "DO",
  "FOR", "GO", "HAS", "HE", "IF", "IN", "IS", "IT", "ITS", "MY", "NEW", "NO",
  "NOT", "NOW", "OF", "OK", "ON", "ONE", "OR", "OUT", "SO", "THE", "TO", "TWO",
  "UP", "US", "WAS", "WE", "WHO", "YOU", "KEY", "OPEN", "REAL", "WELL", "BIG",
  "LOW", "NEXT", "PLUS", "POST", "SAFE", "SEE", "TRUE", "TURN", "WING", "HUGE",
  // Finance / forum jargon
  "AI", "AH", "API", "ATH", "ATR", "AUM", "BS", "CAGR", "CEO", "CFO", "COO",
  "CPI", "CTO", "DCA", "DD", "EOD", "EPS", "ESG", "ETF", "EV", "FAQ", "FED",
  "FOMC", "FUD", "FY", "GDP", "IMO", "IPO", "IRA", "IRS", "ITM", "LEAP", "LLC",
  "LOL", "LTM", "MA", "NAV", "NYSE", "OP", "OTC", "OTM", "PE", "PM", "PT", "QE",
  "REIT", "ROE", "ROI", "RSI", "SEC", "SP", "TA", "TLDR", "TTM", "USA", "USD",
  "WSB", "YOLO", "YOY", "YTD",
  // Days / units that read as symbols
  "AM", "EDT", "EST", "GMT", "PST", "UTC",
]);

/**
 * Build a matcher over the backend's known symbols.
 *
 * @param {string[]|null} symbols - keys of GET /api/company-names, or null when
 *   unavailable (signed out, cache cold). Null degrades to cashtag-only mode,
 *   which is still useful and never blocks on auth.
 */
export function buildMatcher(symbols) {
  const known = symbols ? new Set(symbols.map((s) => String(s).toUpperCase())) : null;
  return {
    cashtagOnly: known === null,
    knows: (t) => known !== null && known.has(t),
  };
}

/** Cheap pre-filter so the TreeWalker can skip most text nodes without a regex run. */
export function mayContainTicker(text) {
  if (!text || text.length < 2) return false;
  return text.includes("$") || /[A-Z]{2}/.test(text);
}

/**
 * Find ticker mentions in a string.
 *
 * @returns {{ticker: string, start: number, end: number, cashtag: boolean}[]}
 *   Non-overlapping, in document order. `end` is exclusive.
 */
export function scanText(text, matcher) {
  if (!mayContainTicker(text)) return [];
  const out = [];
  const taken = [];

  const overlaps = (s, e) => taken.some(([ts, te]) => s < te && ts < e);
  const push = (ticker, start, end, cashtag) => {
    if (overlaps(start, end)) return;
    taken.push([start, end]);
    out.push({ ticker, start, end, cashtag });
  };

  CASHTAG_RE.lastIndex = 0;
  for (let m; (m = CASHTAG_RE.exec(text)); ) {
    const ticker = (m[2] ? `${m[1]}.${m[2]}` : m[1]).toUpperCase();
    push(ticker, m.index, m.index + m[0].length, true);
  }

  if (!matcher.cashtagOnly) {
    BARE_RE.lastIndex = 0;
    for (let m; (m = BARE_RE.exec(text)); ) {
      const root = m[1];
      if (STOPWORDS.has(root)) continue;
      const ticker = m[2] ? `${root}.${m[2]}` : root;
      if (!matcher.knows(ticker)) continue;
      push(ticker, m.index, m.index + m[0].length, false);
    }
  }

  out.sort((a, b) => a.start - b.start);
  return out;
}
