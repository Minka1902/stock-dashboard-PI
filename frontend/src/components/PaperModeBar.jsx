import { useState } from "react";
import Button from "./Button";
import Dialog from "./Dialog";
import styles from "./PaperModeBar.module.css";

/**
 * The unmistakable trading-mode indicator (STC-44): a persistent PAPER band
 * and the switch to live, which asks for confirmation — and, with no broker
 * connected, explains why it can't go live instead of switching.
 * Props: modes ([{ value, available, reason }]).
 */
export default function PaperModeBar({ modes = [] }) {
  const [asking, setAsking] = useState(false);
  const live = modes.find((m) => m.value === "live");

  return (
    <div className={styles.bar} role="note" aria-label="Trading mode: paper">
      <span className={styles.badge}>PAPER</span>
      <span className={styles.text}>Simulated money. Fills use live quotes ± the cost model; no order reaches a market.</span>
      <Button size="sm" variant="quiet" onClick={() => setAsking(true)}>Switch to live…</Button>
      {asking && (
        <Dialog open size="sm" tone="negative" caption="Trading mode" title="Switch to live trading?"
                onClose={() => setAsking(false)}
                footer={<Button variant="primary" onClick={() => setAsking(false)}>Stay on paper</Button>}>
          {live?.available ? (
            <p>Live orders would reach your broker with real money.</p>
          ) : (
            <p>
              Live trading isn't available: {live?.reason || "no broker connected"}. It needs a broker
              connection (IBKR first), key security and order guardrails before any real order can be sent.
            </p>
          )}
        </Dialog>
      )}
    </div>
  );
}
