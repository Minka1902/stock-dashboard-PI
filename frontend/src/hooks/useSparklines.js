import { useEffect, useState } from "react";
import { getSparklines } from "../api";

/**
 * Fetch trailing close-series for a set of tickers at a given range
 * ("1d"/"3d"/"1w"/"1m"), re-fetching when the ticker set or range changes.
 * Returns `{ series, loading }` where series maps ticker -> { closes,
 * change_pct, error }.
 */
export function useSparklines(tickers, range) {
  const [series, setSeries] = useState({});
  const [loading, setLoading] = useState(false);
  // Stable key so the effect only re-runs when the *set* of tickers changes.
  const key = [...tickers].sort().join(",");

  useEffect(() => {
    // Intentional: reset/flag loading, then fetch (same pattern as useLiveQuotes).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!key) { setSeries({}); return undefined; }
    let alive = true;
    setLoading(true);
    getSparklines(key.split(","), range)
      .then((d) => { if (alive) setSeries(d.series || {}); })
      .catch(() => { if (alive) setSeries({}); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [key, range]);

  return { series, loading };
}
