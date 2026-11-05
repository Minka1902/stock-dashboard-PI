// The view <-> URL map. A view key IS its path segment, so adding a view means
// adding one entry here and one in App's VIEWS map — no separate route table.

export const TITLES = {
  sentiment:   "Market Sentiment",
  overview:    "Overview",
  contracts:   "Contracts",
  trades:      "Trades",
  news:        "News",
  watchlist:   "Watchlist",
  "yield-curve": "Yield Curve",
  "econ-calendar": "Economic Calendar",
  signals:     "Signals",
  "fear-greed": "Fear & Greed",
  congress:    "Congress",
  investors:   "Famous Investors",
  "boom-score": "Boom Score",
  short:       "Short Interest",
  social:      "WSB Sentiment",
  analyst:     "Analyst Ratings",
  fundamentals: "Fundamentals",
  seasonality: "Seasonality",
  suggestions: "Suggestions",
  "suggestion-history": "Suggestion History",
  portfolio:   "Portfolio",
  "trading-bot": "Trading Bot",
  x:           "X Watch",
  earnings:    "Earnings",
  // App-level utilities, grouped as they are in the account menu.
  info:        "Info",
  settings:    "Settings",
  server:      "Server",
};

/** The view shown at "/" — and the fallback for an unrecognised path. */
export const DEFAULT_VIEW = "sentiment";

export const VIEW_KEYS = Object.keys(TITLES);

export function isViewKey(key) {
  return Object.prototype.hasOwnProperty.call(TITLES, key);
}
