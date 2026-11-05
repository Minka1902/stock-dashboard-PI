import { useCallback, useEffect, useState } from "react";
import { cancelBrokerOrder, getBroker, syncBroker } from "../api";
import { useEventStream } from "./useEventStream";

/** The Alpaca account: status, mirror and actions; reloads on every fill. */
export function useBroker() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const reload = useCallback(() => getBroker()
    .then((d) => { setData(d); setError(null); })
    .catch((e) => setError(e.message)), []);

  useEventStream("order", reload);

  useEffect(() => {
    reload();
  }, [reload]);

  const cancel = useCallback(async (id) => { await cancelBrokerOrder(id); await reload(); }, [reload]);
  const sync = useCallback(async () => { const out = await syncBroker(); await reload(); return out; }, [reload]);

  return { data, error, reload, cancel, sync };
}
