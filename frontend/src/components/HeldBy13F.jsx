import { useEffect, useState } from "react";
import { get13FHolders } from "../api";
import { formatDate } from "../lib/format";
import styles from "./HeldBy13F.module.css";

/**
 * Which tracked famous investors hold `ticker` in their latest 13F, with
 * their weight and the quarter it's as of. Props: ticker, emptyText.
 */
export default function HeldBy13F({ ticker, emptyText = "None of the tracked funds held it at their last quarter-end." }) {
  const [holders, setHolders] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let live = true;
    get13FHolders(ticker)
      .then((d) => { if (live) setHolders(d.holders); })
      .catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [ticker]);

  if (error) return <p className={styles.muted}>{error}</p>;
  if (!holders) return <p className={styles.muted}>Loading…</p>;
  if (!holders.length) return <p className={styles.muted}>{emptyText}</p>;
  return (
    <ul className={styles.list}>
      {holders.map((h) => (
        <li key={h.cik} className={styles.row}>
          <span className={styles.filer} title={h.person}>{h.filer}</span>
          <span className={styles.pct}>{h.pct === 0 ? "<0.01" : h.pct.toFixed(2)}%</span>
          <span className={styles.muted}>
            {h.put_call ? `${h.put_call.toLowerCase()} options · ` : ""}
            {h.status === "UNCHANGED" ? "unchanged" : h.status === "NEW" ? "new this quarter"
              : `${h.status.toLowerCase()} ${h.change_pct > 0 ? "+" : ""}${h.change_pct?.toFixed(1)}%`}
            {" "}· as of {formatDate(h.period)}
          </span>
        </li>
      ))}
    </ul>
  );
}
