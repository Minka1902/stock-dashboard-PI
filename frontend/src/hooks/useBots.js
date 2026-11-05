import { useCallback, useEffect, useState } from "react";
import {
  createBot, getBotStrategies, getBots, pauseBot, resumeBot, stopAllBots, stopBot,
} from "../api";
import { useEventStream, useStreamConnected } from "./useEventStream";

const POLL_MS = 30_000;
const LIVE_POLL_MS = 120_000;

/**
 * The Trading Bot page's data: the user's bots (with positions, P&L and
 * recent events), the strategy catalog, and the actions. A fill reloads it
 * at once over the event stream; it also polls for fresh marks, every 2 min
 * while the stream is up and every 30s when it isn't. Every action returns
 * the fresh list. `onCashChanged` runs after anything that moves Portfolio
 * cash (create, stop).
 */
export function useBots(onCashChanged) {
  const [bots, setBots] = useState([]);
  const [strategies, setStrategies] = useState([]);
  const [costBps, setCostBps] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const live = useStreamConnected();

  const load = useCallback(async () => {
    try {
      const out = await getBots();
      setBots(out.bots);
      setCostBps(out.cost_bps);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEventStream("order", load);

  useEffect(() => {
    // Intentional: initial load on mount.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
    getBotStrategies().then((s) => setStrategies(s.strategies)).catch(() => {});
  }, [load]);

  useEffect(() => {
    const id = setInterval(() => { if (!document.hidden) load(); }, live ? LIVE_POLL_MS : POLL_MS);
    return () => clearInterval(id);
  }, [live, load]);

  const act = useCallback(async (fn, movesCash = false) => {
    const out = await fn();
    setBots(out.bots);
    if (movesCash) onCashChanged?.();
    return out;
  }, [onCashChanged]);

  return {
    bots, strategies, costBps, loading, error, reload: load,
    create: (body) => act(() => createBot(body), true),
    pause: (id) => act(() => pauseBot(id)),
    resume: (id) => act(() => resumeBot(id)),
    stop: (id) => act(() => stopBot(id), true),
    stopAll: () => act(stopAllBots, true),
  };
}
