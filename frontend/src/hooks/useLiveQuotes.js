import { useCallback, useEffect, useMemo, useState } from "react";
import { getQuotes } from "../api";

// Faster than the 180s dashboard cycle; the backend caches just under the
// poll cadence so this costs at most one Yahoo fetch per ticker per poll
// across all clients. Cadence is configurable in Settings (10–300s).
const DEFAULT_REFRESH_MS = 30000;

/**
 * Live quotes (incl. pre/post-market) for watchlist + portfolio tickers.
 * Polls independently of useDashboardData; on failure it keeps the last
 * good quotes and retries on the next tick.
 */
export function useLiveQuotes(refreshMs = DEFAULT_REFRESH_MS) {
  const [quotes, setQuotes] = useState([]);
  const [asOf, setAsOf] = useState(null);
  const [marketStatus, setMarketStatus] = useState(null);
  // {NYSE, TASE} clock-based sessions; market_status stays the US one.
  const [marketStatuses, setMarketStatuses] = useState(null);

  const load = useCallback(async () => {
    try {
      const data = await getQuotes();
      setQuotes(data.quotes);
      setAsOf(data.as_of);
      setMarketStatus(data.market_status ?? null);
      setMarketStatuses(data.market_statuses ?? null);
    } catch {
      // keep showing the last good quotes; next tick retries
    }
  }, []);

  useEffect(() => {
    // Intentional: kick off the initial load on mount, then poll on an interval.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
    const id = setInterval(load, Math.max(10000, refreshMs || DEFAULT_REFRESH_MS));
    // Refetch immediately when the tab regains focus so a backgrounded tab
    // isn't showing minutes-old prices / a stale session badge.
    const onVisible = () => { if (document.visibilityState === "visible") load(); };
    document.addEventListener("visibilitychange", onVisible);
    // Settings → "Currencies in the ticker tape" saved: show the new pairs now.
    window.addEventListener("fxwatch:changed", load);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("fxwatch:changed", load);
    };
  }, [load, refreshMs]);

  const quotesByTicker = useMemo(
    () => Object.fromEntries(quotes.map((q) => [q.ticker, q])),
    [quotes],
  );

  return { quotes, quotesByTicker, asOf, marketStatus, marketStatuses };
}
