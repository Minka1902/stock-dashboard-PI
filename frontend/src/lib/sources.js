// Single source of truth for how each backend data source is described in the UI —
// the failed-source strip, the Settings "Data sources" guide, and the Module Guide's
// per-card source line all read from here so provider names stay in sync.
//
// Keys match the `source` field returned by GET /api/sources (see build_sources in
// backend/app/main.py). Only the external feeds are listed; internal computations
// (boom_score, alerts, regime, automute, rules, analysis, ohlc) are intentionally omitted from the guide.

export const SOURCE_META = {
  usaspending: {
    label: "Federal Contracts",
    provider: "USASpending.gov",
    blurb: "Largest recent U.S. government contract awards.",
  },
  gdelt: {
    label: "News",
    provider: "GDELT Project",
    blurb: "Macro and per-ticker news headlines.",
  },
  edgar: {
    label: "Insider Trades",
    provider: "SEC EDGAR",
    blurb: "Form 4 open-market buys and sells by insiders.",
  },
  yield_curve: {
    label: "Yield Curve",
    provider: "U.S. Treasury",
    blurb: "2y / 10y / 30y yields and the 2s10s spread.",
  },
  econ_calendar: {
    label: "Economic Calendar",
    provider: "FMP / Nasdaq",
    blurb: "Upcoming macro data releases.",
  },
  technical: {
    label: "Technical Signals",
    provider: "Alpha Vantage + Yahoo Finance",
    blurb: "Trend, momentum and volume readings per ticker.",
  },
  fear_greed: {
    label: "Fear & Greed",
    provider: "CNN",
    blurb: "CNN's 0–100 market-mood gauge.",
  },
  vix: {
    label: "VIX",
    provider: "Yahoo Finance",
    blurb: "CBOE volatility index history.",
  },
  aaii: {
    label: "AAII Sentiment",
    provider: "AAII survey",
    blurb: "Weekly retail bull / bear sentiment survey.",
  },
  put_call: {
    label: "Put/Call Ratio",
    provider: "CNN",
    blurb: "5-day options put/call ratio.",
  },
  margin_debt: {
    label: "Margin Debt",
    provider: "FINRA",
    blurb: "Aggregate margin debt and its year-over-year change.",
  },
  congress: {
    label: "Congressional Trades",
    provider: "House Clerk (PTR filings) + Senate Stock Watcher",
    blurb: "Stock transactions disclosed by members of Congress, each House trade linked to its filing.",
  },
  short_interest: {
    label: "Short Interest",
    provider: "Yahoo Finance",
    blurb: "Percent of float sold short and squeeze potential.",
  },
  social: {
    label: "WSB / Social",
    provider: "ApeWisdom (Reddit)",
    blurb: "Rising Reddit / WallStreetBets mention rank.",
  },
  analyst: {
    label: "Analyst Ratings",
    provider: "Yahoo Finance",
    blurb: "Upgrades, downgrades and upcoming earnings dates.",
  },
  fundamentals: {
    label: "Fundamentals",
    provider: "Yahoo Finance",
    blurb: "Core company health metrics and next earnings date.",
  },
  x_posts: {
    label: "X Watch",
    provider: "Nitter mirrors / X API",
    blurb: "Recent posts from monitored X accounts, with detected cashtags.",
  },
  seasonality: {
    label: "Seasonality",
    provider: "Yahoo Finance",
    blurb: "A ticker's historical tendency for this time of year.",
  },
  holdings13f: {
    label: "Famous Investors (13F)",
    provider: "SEC EDGAR",
    blurb: "Quarter-end holdings of well-known funds and what changed since the last quarter.",
  },
  universe: {
    label: "Scanner Universes",
    provider: "Wikipedia",
    blurb: "S&P 500, Nasdaq-100 and TA-125 constituents for the market scanner, monthly.",
  },
  scan_history: {
    label: "Scanner History",
    provider: "Yahoo Finance",
    blurb: "Daily bars the market scanner needs for tickers you don't hold or watch.",
  },
  scanner: {
    label: "Market Scanner",
    provider: "Yahoo Finance",
    blurb: "Today's session for the whole universe, run through the built-in scans while markets are open.",
  },
};

// Ordered list of external sources for the Settings guide (registry order).
export const SOURCE_ORDER = Object.keys(SOURCE_META);

// Maps an Info-page module card key (see GROUPS in InfoPanel.jsx) to either a backend
// source name, or a { note } sentinel for modules with no external feed.
export const MODULE_SOURCE = {
  "boom-score": { note: "Computed from every signal below" },
  trades: "edgar",
  congress: "congress",
  signals: "technical",
  short: "short_interest",
  social: "social",
  analyst: "analyst",
  news: "gdelt",
  "yield-curve": "yield_curve",
  "fear-greed": "fear_greed",
  seasonality: "seasonality",
  contracts: "usaspending",
  fundamentals: "fundamentals",
  x: "x_posts",
  watchlist: { note: "Your input" },
  portfolio: { note: "Your input" },
  suggestions: { note: "Derived" },
  alerts: { note: "Derived" },
};

/** Plain-language meaning of each source state, for status badge tooltips. */
export const STATE_TIP = {
  ok: "OK — the last refresh succeeded and delivered real data.",
  error: "Error — the last refresh failed. Nothing was invented; the previous data (if any) is kept.",
  deferred: "Deferred — the source asked to wait (usually a rate limit). It retries automatically; this is not a failure.",
  idle: "No data yet — this source has not reported a status.",
  never: "Never run — this source has not run since it was added. It runs on its schedule.",
};

// "ok" or "ok (fallback: …)" both mean the source delivered real data.
// "deferred: …" means the source asked to be retried later (e.g. a rate
// limit): not a failure, not fresh either — shown in amber, never as an error.
export function sourceState(status) {
  if (!status) return "error";
  if (status === "ok" || status.startsWith("ok (")) return "ok";
  if (status.startsWith("deferred")) return "deferred";
  return "error";
}

// The parenthetical note carried by an "ok (…)" fallback status, else null.
export function sourceNote(status) {
  if (!status || !status.startsWith("ok (")) return null;
  return status.slice(3).replace(/^\(|\)$/g, "");
}

// A source counts as "not responding" when it errored or hasn't successfully
// refreshed within `maxAgeHours`. Reads the clock, so it lives here (out of
// component render-purity checks). `status` is a GET /api/sources entry.
export function sourceStale(status, maxAgeHours = 24) {
  if (!status) return false; // unknown → don't suppress
  const state = sourceState(status.status);
  if (state === "error") return true;
  // A deferral re-stamps the attempt clock, so judge its age by the data.
  const stamp = state === "deferred"
    ? status.last_success_at || status.last_refreshed_at
    : status.last_refreshed_at;
  if (!stamp) return true;
  const ageH = (Date.now() - Date.parse(stamp)) / 3600000;
  return Number.isFinite(ageH) && ageH > maxAgeHours;
}
