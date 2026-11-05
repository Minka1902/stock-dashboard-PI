// Boom Score presentation metadata.
//
// COPIED from frontend/src/components/BoomScorePanel.jsx:15-48, where these are
// module-local and not exported. The extension is a separate app and must not
// reach across into frontend/src, so this is a deliberate duplicate.
//
// KEEP IN SYNC: if the tier thresholds or the chip labels change there, change
// them here too. tests/boom.test.js pins the tier boundaries so a silent drift in
// one direction at least fails a test.

export const CHIP_META = {
  // bullish
  golden_cross: { label: "Golden ✕", tone: "bull", horizon: "M", tip: "MA50 crossed above MA200 — medium-term uptrend" },
  insider_cluster_buy: { label: "Insider Buy", tone: "bull", horizon: "M", tip: "≥2 open-market insider purchases in last 30 days" },
  congress_buy: { label: "Congress Buy", tone: "bull", horizon: "L", tip: "Congressional purchase — weighted by amount & recency" },
  analyst_upgrade: { label: "Analyst Up", tone: "bull", horizon: "M", tip: "Recent analyst upgrade or initiation" },
  near_52w_high: { label: "52W Break", tone: "bull", horizon: "S", tip: "Price within 3% of 52-week high — breakout territory" },
  macd_crossover: { label: "MACD ✕", tone: "bull", horizon: "S", tip: "MACD line crossed above signal line — momentum shift" },
  volume_confirmed: { label: "Vol Confirm", tone: "bull", horizon: "S", tip: "Price rising on 1.5× average volume — institutional participation" },
  short_squeeze: { label: "Squeeze Risk", tone: "bull", horizon: "S", tip: "Short float > 15% — squeeze potential if price rises" },
  wsb_rising: { label: "WSB↑", tone: "bull", horizon: "S", tip: "Rising Reddit/WSB mention rank in last 24 hours" },
  rsi_recovery: { label: "RSI Zone", tone: "bull", horizon: "S", tip: "RSI 30–50 — oversold recovery zone" },
  fear_greed_contrarian: { label: "Fear Extreme", tone: "bull", horizon: "M", tip: "Fear & Greed < 25 — extreme fear historically marks entry points" },
  yield_uninversion: { label: "Curve Norm", tone: "bull", horizon: "L", tip: "Yield curve un-inverted in last 30 days — historically bullish 6–18 months out" },
  contracts_catalyst: { label: "Gov Contract", tone: "bull", horizon: "M", tip: "Major federal contract (>$100M) awarded in last 30 days" },
  seasonal_tailwind: { label: "Seasonal ↑", tone: "bull", horizon: "M", tip: "Strong historical edge for the coming week (avg ≥ +2%, win-rate ≥ 60% over 10y)" },
  // bearish
  death_cross: { label: "Death ✕", tone: "bear", horizon: "M", tip: "MA50 dropped below MA200 — medium-term downtrend" },
  insider_cluster_sell: { label: "Insider Dump", tone: "bear", horizon: "M", tip: "≥2 open-market insider sales in last 30 days" },
  overbought_rsi: { label: "Overbought", tone: "bear", horizon: "S", tip: "RSI > 70 — overbought, pullback risk" },
  congress_sale: { label: "Congress Sell", tone: "bear", horizon: "L", tip: "Congressional sale — legislators reducing position" },
  analyst_downgrade_cluster: { label: "Downgrades", tone: "bear", horizon: "M", tip: "≥2 analyst downgrades in last 30 days" },
  extreme_greed: { label: "Greed Extreme", tone: "bear", horizon: "S", tip: "Fear & Greed > 78 — euphoria precedes distribution" },
};

export const HORIZON_TIP = { S: "Short (days–weeks)", M: "Medium (weeks–months)", L: "Long (months+)" };

export function convictionTier(score) {
  if (score >= 76) return { label: "Strong Setup", tone: "high" };
  if (score >= 51) return { label: "High Conviction", tone: "mid" };
  if (score >= 26) return { label: "Interesting", tone: "low" };
  if (score >= 0) return { label: "Watching", tone: "faint" };
  return { label: "Bearish Signals", tone: "neg" };
}

/**
 * Active signal chips for a BoomScore row, most meaningful first.
 * The row carries one boolean per component plus a `components` JSON blob; we
 * read the booleans, which are the persisted, explainable part.
 */
export function activeChips(row, limit = 4) {
  if (!row) return [];
  const out = [];
  for (const [key, meta] of Object.entries(CHIP_META)) {
    if (row[key]) out.push({ key, ...meta });
  }
  // Bullish first, then bearish — matches how the dashboard panel reads.
  out.sort((a, b) => (a.tone === b.tone ? 0 : a.tone === "bull" ? -1 : 1));
  return limit ? out.slice(0, limit) : out;
}
