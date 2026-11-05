import { useCallback, useEffect, useState } from "react";
import { cancelPaperOrder, getPaper, resetPaper } from "../api";
import { useEventStream, useStreamConnected } from "./useEventStream";

const POLL_MS = 30_000;
const LIVE_POLL_MS = 120_000;

/**
 * The paper account: summary and its actions. A fill reloads it at once over
 * the event stream; it also polls for fresh marks, every 2 min while the
 * stream is up and every 30s when it isn't.
 */
export function usePaper() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const live = useStreamConnected();

  const reload = useCallback(() => getPaper()
    .then((d) => { setData(d); setError(null); })
    .catch((e) => setError(e.message)), []);

  useEventStream("order", reload);

  useEffect(() => {
    reload();
  }, [reload]);

  useEffect(() => {
    const id = setInterval(() => { if (!document.hidden) reload(); }, live ? LIVE_POLL_MS : POLL_MS);
    return () => clearInterval(id);
  }, [live, reload]);

  const reset = useCallback(async (body) => setData(await resetPaper(body)), []);
  const cancel = useCallback(async (id) => { await cancelPaperOrder(id); await reload(); }, [reload]);

  return { data, error, reload, reset, cancel };
}
