import { useEffect, useState } from "react";
import { getFxRates } from "../api";

const POLL_MS = 60000;

/**
 * Live FX multipliers turning each of `currencies` into `base`
 * ({ rates: { ILS: 0.27, USD: 1 }, unavailable: ["…"], asOf }).
 *
 * Rates are live Yahoo quotes from the backend, never defaults: a currency
 * the backend could not price comes back null and is listed in
 * `unavailable`, and callers leave that money out of converted totals and
 * say so. On a failed poll the last good rates stay (with their `asOf`).
 */
export function useFxRates(base, currencies) {
  const key = [...new Set(currencies.filter(Boolean))].sort().join(",");
  const [state, setState] = useState({ base: null, rates: {}, unavailable: [], asOf: null, error: null });

  useEffect(() => {
    if (!base) return undefined;
    let alive = true;
    const load = () => {
      getFxRates(base, key ? key.split(",") : [])
        .then((d) => {
          if (!alive) return;
          setState({
            base: d.base, rates: d.rates || {}, unavailable: d.unavailable || [],
            asOf: d.as_of || null, error: null,
          });
        })
        .catch((err) => {
          if (!alive) return;
          setState((s) => ({ ...s, error: err.message || "FX unavailable" }));
        });
    };
    load();
    const id = setInterval(load, POLL_MS);
    return () => { alive = false; clearInterval(id); };
  }, [base, key]);

  // Rates fetched for a previous base are not rates for this one.
  const current = state.base === base;
  return {
    rates: current ? state.rates : {},
    unavailable: current ? state.unavailable : [],
    asOf: current ? state.asOf : null,
    loading: !current,
    error: state.error,
  };
}
