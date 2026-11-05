import { useState } from "react";
import Button from "./Button";
import { addWatch } from "../api";

/**
 * "+ Watch": adds a ticker to a watchlist (the default one unless `listId`)
 * and then says so. Props: ticker, listId, label ("+ Watch"), size ("sm"),
 * variant ("quiet"), onAdded(ticker).
 */
export default function WatchButton({ ticker, listId = null, label = "+ Watch", size = "sm", variant = "quiet", onAdded }) {
  const [state, setState] = useState("idle");
  const [error, setError] = useState(null);

  async function add() {
    setState("busy");
    try {
      await addWatch(ticker, "", listId);
      setState("done");
      onAdded?.(ticker);
    } catch (e) {
      setError(e.message);
      setState("idle");
    }
  }

  if (state === "done") return <span role="status" aria-live="polite">Watching</span>;
  return (
    <Button size={size} variant={variant} onClick={add} disabled={state === "busy"}
            title={error || `Add ${ticker} to your watchlist`} aria-label={`Add ${ticker} to your watchlist`}>
      {label}
    </Button>
  );
}
