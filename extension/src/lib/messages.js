// Message types for content-script / popup -> background RPC.
//
// The background is the only context that touches the network, so every data
// request funnels through here.

export const MSG = {
  // Content script
  SCORES: "scores", // { tickers: [...] } -> { scores: { TICKER: BoomScore } }
  SYMBOLS: "symbols", // -> { symbols: [...] } | { symbols: null } when unavailable
  ANALYZE: "analyze", // { ticker } -> analyze payload (explicit user action only)
  ADD_WATCH: "addWatch", // { ticker }
  OPEN_TICKER: "openTicker", // { ticker }
  SITE_ENABLED: "siteEnabled", // { host } -> { enabled }

  // Popup
  SNAPSHOT: "snapshot", // -> { quotes, boom, alerts, auth }
  MARK_READ: "markRead", // { keys } | { all: true }
  POLL_NOW: "pollNow",
};

/** Uniform reply envelope so every caller handles failure the same way. */
export function ok(data) {
  return { ok: true, data };
}

export function fail(error) {
  return {
    ok: false,
    status: error?.status ?? 0,
    detail: error?.message ?? String(error),
  };
}
