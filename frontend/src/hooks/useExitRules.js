import { useCallback, useEffect, useState } from "react";
import { deleteExitRule, getExitRules, saveExitRule } from "../api";

/**
 * One book's holdings with their exit rules and suggested stops. `save` and
 * `remove` replace the one row they touch with the server's answer, so the
 * table never shows a level the backend didn't accept.
 */
export function useExitRules(account = "manual") {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const reload = useCallback(() => getExitRules(account)
    .then((d) => { setItems(d.items); setError(null); })
    .catch((e) => setError(e.message))
    .finally(() => setLoading(false)), [account]);

  useEffect(() => {
    reload();
  }, [reload]);

  const save = useCallback(async (ticker, body) => {
    const row = await saveExitRule(ticker, body, account);
    setItems((prev) => prev.map((i) => (i.ticker === ticker ? row : i)));
    return row;
  }, [account]);

  const remove = useCallback(async (ticker) => {
    await deleteExitRule(ticker, account);
    await reload();
  }, [account, reload]);

  return { items, loading, error, reload, save, remove };
}
