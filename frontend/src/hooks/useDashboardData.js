import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getContracts,
  getSources,
  getNews,
  getTrades,
  getWatchlist,
  getYieldCurve,
  getEconCalendar,
  getSignals,
  getFearGreed,
  getVix,
  getAaii,
  getPutCall,
  getMarginDebt,
  getSentiment,
  getCongressTrades,
  get13F,
  getShortInterest,
  getSocial,
  getAnalyst,
  getBoomScores,
  getFundamentals,
  getSeasonality,
  getPortfolio,
  getCash,
  getXPosts,
  getSuggestions,
  getAlerts,
  getAnalyses,
  markAlertsRead as apiMarkAlertsRead,
  refreshSource,
  addWatch as apiAddWatch,
  removeWatch as apiRemoveWatch,
  addHolding as apiAddHolding,
  updateHolding as apiUpdateHolding,
  setHoldingCategory as apiSetHoldingCategory,
  removeHolding as apiRemoveHolding,
  sellHolding as apiSellHolding,
  moveCash as apiMoveCash,
  setCash as apiSetCash,
} from "../api";

const REFRESH_MS = 180000; // 3 minutes, matches backend default
const EXTERNAL_SOURCES = [
  "usaspending", "gdelt", "edgar",
  "yield_curve", "econ_calendar", "technical", "fear_greed", "vix", "aaii", "put_call", "margin_debt", "congress",
  "short_interest", "social", "analyst", "fundamentals", "x_posts", "seasonality", "boom_score",
];

/**
 * Every endpoint the dashboard loads, declared once.
 *
 * This replaced two hand-maintained parallel arrays (a Promise.allSettled list
 * and a positional array of setters). Adding an endpoint meant editing both in
 * lockstep, and an insertion in the wrong place silently routed one panel's
 * data into another. Here a new endpoint is a single object.
 *
 * `apply` is for payloads that don't map 1:1 onto their key.
 */
const ENDPOINTS = [
  { key: "contracts", fetch: getContracts, initial: [] },
  // The connectivity probe for the "backend unreachable" banner.
  { key: "sources", fetch: getSources, initial: [], probe: true },
  { key: "news", fetch: getNews, initial: [] },
  { key: "trades", fetch: getTrades, initial: [] },
  { key: "watchlist", fetch: getWatchlist, initial: [] },
  { key: "yieldCurve", fetch: getYieldCurve, initial: [] },
  { key: "signals", fetch: getSignals, initial: [] },
  { key: "fearGreed", fetch: getFearGreed, initial: [] },
  { key: "vix", fetch: getVix, initial: [] },
  { key: "aaii", fetch: getAaii, initial: [] },
  { key: "putCall", fetch: getPutCall, initial: [] },
  { key: "marginDebt", fetch: getMarginDebt, initial: [] },
  { key: "sentiment", fetch: getSentiment, initial: null },
  { key: "congressTrades", fetch: getCongressTrades, initial: [] },
  { key: "investors", fetch: get13F, initial: null },
  { key: "shortInterest", fetch: getShortInterest, initial: [] },
  { key: "social", fetch: getSocial, initial: [] },
  { key: "analyst", fetch: getAnalyst, initial: [] },
  { key: "boomScores", fetch: getBoomScores, initial: [] },
  { key: "fundamentals", fetch: getFundamentals, initial: [] },
  { key: "seasonality", fetch: getSeasonality, initial: [] },
  { key: "portfolio", fetch: getPortfolio, initial: [] },
  { key: "cash", fetch: getCash, initial: [] },
  { key: "suggestions", fetch: getSuggestions, initial: null },
  {
    key: "alerts",
    fetch: getAlerts,
    initial: [],
    // Payload is { alerts, unread }, so it feeds two slots.
    apply: (value) => ({ alerts: value.alerts, unreadAlerts: value.unread }),
    extraInitial: { unreadAlerts: 0 },
  },
  { key: "analyses", fetch: getAnalyses, initial: [] },
  { key: "econCalendar", fetch: getEconCalendar, initial: [] },
  { key: "xPosts", fetch: getXPosts, initial: [] },
];

const INITIAL_DATA = ENDPOINTS.reduce(
  (acc, e) => ({ ...acc, [e.key]: e.initial, ...(e.extraInitial || {}) }),
  {},
);

/** How long to wait for queued server-side refreshes before giving up and reloading. */
const REFRESH_WAIT_MS = 90000;
const REFRESH_POLL_MS = 1500;

/**
 * Owns all dashboard data: contracts, sources, news, insider trades, watchlist.
 * Loads in parallel, supports manual refresh of all sources, auto-refreshes
 * every 3 minutes, and exposes watchlist add/remove.
 */
export function useDashboardData() {
  const [data, setData] = useState(INITIAL_DATA);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    // Load every source independently: one failing endpoint (e.g. an upstream
    // 429/500) must not blank the whole dashboard. Each result is applied on
    // its own; failures leave that panel's previous state untouched.
    const results = await Promise.allSettled(ENDPOINTS.map((e) => e.fetch()));

    const patch = {};
    results.forEach((r, i) => {
      if (r.status !== "fulfilled") return;
      const endpoint = ENDPOINTS[i];
      Object.assign(
        patch,
        endpoint.apply ? endpoint.apply(r.value) : { [endpoint.key]: r.value },
      );
    });
    if (Object.keys(patch).length) setData((prev) => ({ ...prev, ...patch }));

    // Only banner a genuine outage: the sources fetch is the connectivity probe.
    const probeIndex = ENDPOINTS.findIndex((e) => e.probe);
    const probe = results[probeIndex];
    setError(
      probe.status === "fulfilled"
        ? null
        : probe.reason?.message || "backend unreachable",
    );
    setLoading(false);
  }, []);

  useEffect(() => {
    // Intentional: kick off the initial load on mount, then poll on an interval.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => clearInterval(id);
  }, [load]);

  /**
   * Refresh named sources and wait for the server to actually finish them.
   *
   * POST /api/refresh now returns 202 and does the work on a background
   * executor, so the response no longer means "done". We snapshot each source's
   * last_refreshed_at, then poll /api/sources until every requested source has
   * moved on (or the deadline passes) before reloading the panels.
   */
  const refreshOne = useCallback(async (names, { force = false } = {}) => {
    const wanted = Array.isArray(names) ? names : [names];
    setBusy(true);
    try {
      let before = {};
      try {
        before = Object.fromEntries(
          (await getSources()).map((s) => [s.source, s.last_refreshed_at]),
        );
      } catch { /* first load may not have statuses yet; treat as all-stale */ }

      // Bounded concurrency: the backend runs these on a single worker anyway,
      // and the rate limiter counts every call.
      const queue = [...wanted];
      const worker = async () => {
        while (queue.length) {
          const s = queue.shift();
          try { await refreshSource(s, { force }); } catch { /* status recorded server-side */ }
        }
      };
      await Promise.all(Array.from({ length: 4 }, worker));

      const deadline = Date.now() + REFRESH_WAIT_MS;
      let pending = new Set(wanted);
      while (pending.size && Date.now() < deadline) {
        await new Promise((r) => setTimeout(r, REFRESH_POLL_MS));
        let statuses;
        try { statuses = await getSources(); } catch { break; }
        for (const s of statuses) {
          if (pending.has(s.source) && s.last_refreshed_at !== before[s.source]) {
            pending.delete(s.source);
          }
        }
      }
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }, [load]);

  const refresh = useCallback(() => refreshOne(EXTERNAL_SOURCES), [refreshOne]);

  const patch = useCallback((next) => setData((prev) => ({ ...prev, ...next })), []);

  const addWatch = useCallback(async (ticker, note) => {
    patch({ watchlist: await apiAddWatch(ticker, note) });
  }, [patch]);

  const removeWatch = useCallback(async (ticker) => {
    patch({ watchlist: await apiRemoveWatch(ticker) });
  }, [patch]);

  const addHolding = useCallback(async (ticker, shares, avgCost, currency = null, opts = {}) => {
    const list = await apiAddHolding(ticker, shares, avgCost, currency, opts);
    patch(opts.payFromCash ? { portfolio: list, cash: await getCash() } : { portfolio: list });
    return list;
  }, [patch]);

  // Resolves to { portfolio, realized_pnl, currency } so the caller can say
  // what the sale realized.
  const sellHolding = useCallback(async (ticker, order) => {
    const out = await apiSellHolding(ticker, order);
    patch(order.toCash ? { portfolio: out.portfolio, cash: await getCash() } : { portfolio: out.portfolio });
    return out;
  }, [patch]);

  const moveCash = useCallback(async (currency, amount, side) => {
    patch({ cash: await apiMoveCash(currency, amount, side) });
  }, [patch]);

  const setCash = useCallback(async (currency, balance) => {
    patch({ cash: await apiSetCash(currency, balance) });
  }, [patch]);

  // For changes made elsewhere (a bot allocating or returning cash).
  const reloadCash = useCallback(async () => {
    patch({ cash: await getCash() });
  }, [patch]);

  const updateHolding = useCallback(async (ticker, shares, avgCost, currency = null) => {
    patch({ portfolio: await apiUpdateHolding(ticker, shares, avgCost, currency) });
  }, [patch]);

  const setHoldingCategory = useCallback(async (ticker, category) => {
    patch({ portfolio: await apiSetHoldingCategory(ticker, category) });
  }, [patch]);

  const removeHolding = useCallback(async (ticker) => {
    patch({ portfolio: await apiRemoveHolding(ticker) });
  }, [patch]);

  /**
   * Mark alerts read. No argument marks everything; an array of dedup_keys
   * marks exactly those. The backend already accepted per-key marking — only
   * the "all" path was ever wired up here.
   */
  const markAlertsRead = useCallback(async (keys) => {
    const payload = Array.isArray(keys) && keys.length ? { keys } : { all: true };
    const { alerts: a, unread } = await apiMarkAlertsRead(payload);
    patch({ alerts: a, unreadAlerts: unread });
  }, [patch]);

  return useMemo(() => ({
    ...data,
    loading,
    busy,
    error,
    refresh,
    refreshOne,
    addWatch,
    removeWatch,
    addHolding,
    updateHolding,
    setHoldingCategory,
    removeHolding,
    sellHolding,
    moveCash,
    setCash,
    reloadCash,
    markAlertsRead,
  }), [
    data, loading, busy, error, refresh, refreshOne, addWatch, removeWatch,
    addHolding, updateHolding, setHoldingCategory, removeHolding, sellHolding,
    moveCash, setCash, reloadCash, markAlertsRead,
  ]);
}
