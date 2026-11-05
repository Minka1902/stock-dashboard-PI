import { useEffect, useState } from "react";
import Dialog from "./Dialog";
import ExitRulesEditor from "./ExitRulesEditor";
import { deleteExitRule, getExitRule, saveExitRule } from "../api";

/**
 * ExitRulesEditor in a dialog, for any page that shows a holding (Portfolio
 * rows, the stock page). Loads the rule itself unless `item` (a row from
 * /api/portfolio/rules) is passed in.
 *
 * Props: ticker, account ("manual"), item, onSaved(row | null) after a save
 * or delete, onClose.
 */
export default function ExitRuleDialog({ ticker, account = "manual", item = null, onSaved, onClose }) {
  const [loaded, setLoaded] = useState(item);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (item) return undefined;
    let live = true;
    getExitRule(ticker, account)
      .then((d) => { if (live) setLoaded(d); })
      .catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [ticker, account, item]);

  async function save(body) {
    const row = await saveExitRule(ticker, body, account);
    onSaved?.(row);
    onClose();
  }

  async function remove() {
    await deleteExitRule(ticker, account);
    onSaved?.(null);
    onClose();
  }

  return (
    <Dialog open size="md" caption="Exit rule · alerts only" title={`${ticker} exit rule`} onClose={onClose}>
      {error ? (
        <p role="alert">{error}</p>
      ) : !loaded ? (
        <p>Loading…</p>
      ) : !loaded.held ? (
        <p>You don't hold {ticker} in this account, so there's nothing to protect.</p>
      ) : (
        <ExitRulesEditor
          ticker={ticker} price={loaded.price} currency={loaded.currency} avgCost={loaded.avg_cost}
          rule={loaded.rule} suggestedStop={loaded.suggested}
          onSave={save} onDelete={remove} onCancel={onClose}
        />
      )}
    </Dialog>
  );
}
