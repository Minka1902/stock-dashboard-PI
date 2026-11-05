import { useCallback, useEffect, useState } from "react";
import { getLots } from "../api";

/** One account's tax lots, realized gains and harvest candidates. */
export function useLots(account, minLoss) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  const reload = useCallback(() => getLots(account, minLoss)
    .then((d) => { setData(d); setError(null); })
    .catch((e) => setError(e.message)), [account, minLoss]);

  useEffect(() => {
    reload();
  }, [reload]);

  return { data, error, reload };
}
