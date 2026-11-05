// Popup data loading.
//
// Shaped after frontend/src/hooks/useLiveQuotes.js (30s cadence, 10s floor,
// keep-last-good on failure) but WITHOUT its visibilitychange listener: a popup
// is destroyed the moment it loses focus, so the mount-time load already covers
// what that listener existed for.
//
// One background call returns all three datasets, each named. Deliberately not
// the positionally-coupled results[]/setters[] arrays in
// frontend/src/hooks/useDashboardData.js:87-129 — that pattern silently
// misroutes data the moment an endpoint is inserted.

import { useCallback, useEffect, useRef, useState } from "react";

import { ext } from "../lib/browser.js";
import { MSG } from "../lib/messages.js";

const REFRESH_MS = 30_000;

export function useSnapshot() {
  const [quotes, setQuotes] = useState(null);
  const [boom, setBoom] = useState(null);
  const [alerts, setAlerts] = useState(null);
  const [authState, setAuthState] = useState("unknown");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const alive = useRef(true);

  const load = useCallback(async () => {
    let res;
    try {
      res = await ext.runtime.sendMessage({ type: MSG.SNAPSHOT });
    } catch {
      res = { ok: false, detail: "extension background unavailable" };
    }
    if (!alive.current) return;
    setLoading(false);
    if (!res?.ok) {
      setError(res?.detail || "could not reach the background worker");
      return;
    }
    const d = res.data;
    setAuthState(d.authState);
    setError(d.error || null);
    // Keep the last good values when a single endpoint fails.
    if (d.quotes) setQuotes(d.quotes);
    if (d.boom) setBoom(d.boom);
    if (d.alerts) setAlerts(d.alerts);
  }, []);

  useEffect(() => {
    alive.current = true;
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => {
      alive.current = false;
      clearInterval(id);
    };
  }, [load]);

  const markRead = useCallback(async (payload) => {
    const res = await ext.runtime.sendMessage({ type: MSG.MARK_READ, payload });
    if (res?.ok) setAlerts(res.data);
  }, []);

  return { quotes, boom, alerts, authState, error, loading, reload: load, markRead };
}
