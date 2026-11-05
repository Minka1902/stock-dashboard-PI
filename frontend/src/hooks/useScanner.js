import { useCallback, useEffect, useState } from "react";
import { getScanner, saveScannerPrefs } from "../api";
import { useEventStream, useStreamConnected } from "./useEventStream";

const FALLBACK_POLL_MS = 60_000;

/**
 * The scanner's view, reloaded whenever a scan run finishes (pushed over the
 * event stream) and polled only while the stream is down.
 */
export function useScanner() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const live = useStreamConnected();

  const reload = useCallback(() => getScanner()
    .then((d) => { setData(d); setError(null); })
    .catch((e) => setError(e.message)), []);

  useEventStream("scanner", reload);

  useEffect(() => {
    reload();
  }, [reload]);

  useEffect(() => {
    if (live) return undefined;
    const id = setInterval(() => { if (!document.hidden) reload(); }, FALLBACK_POLL_MS);
    return () => clearInterval(id);
  }, [live, reload]);

  const savePrefs = useCallback(async (prefs) => {
    const saved = await saveScannerPrefs(prefs);
    setData((d) => ({ ...d, prefs: saved }));
    return saved;
  }, []);

  return { data, error, live, reload, savePrefs };
}
