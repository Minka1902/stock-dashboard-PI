import { formatMoney } from "../lib/format";
import styles from "./HarvestList.module.css";

/**
 * Tax-loss harvesting candidates: each position's losing lots, the loss
 * split short / long term, and any wash-sale exposure — the shares bought
 * in the last 30 days that would disallow part of it. Props: candidates,
 * currencyOf(ticker) → code, onCheck({ticker, qty, lotIds}) (opens the sale
 * check), minLoss.
 */
export default function HarvestList({ candidates, currencyOf = () => "USD", onCheck, minLoss }) {
  if (!candidates.length) {
    return <p className={styles.empty}>No position has losing lots worth {formatMoney(minLoss, "USD")} or more.</p>;
  }
  return (
    <ul className={styles.list}>
      {candidates.map((c) => {
        const m = (v) => formatMoney(v, currencyOf(c.ticker));
        return (
          <li key={c.ticker} className={styles.row}>
            <div className={styles.head}>
              <strong>{c.ticker}</strong>
              <span className={styles.loss}>{m(c.loss)}</span>
              <span className={styles.faint}>{+c.qty.toFixed(4)} shares in {c.lots.join(", ")}</span>
              {onCheck && (
                <button type="button" className={styles.link} onClick={() => onCheck({ ticker: c.ticker, qty: c.qty, lotIds: c.lots })}>
                  Check this sale
                </button>
              )}
            </div>
            <p className={styles.faint}>short term {m(c.short)} · long term {m(c.long)}</p>
            {c.wash.length > 0 && (
              <p className={styles.wash}>
                Wash sale: {c.wash.map((w) => `${+w.qty.toFixed(4)} ${w.ticker} bought ${w.acquired}`).join(", ")} —{" "}
                {m(c.disallowed)} of the loss would be disallowed. Buying back is fine on or after {c.rebuy_on_or_after}.
              </p>
            )}
          </li>
        );
      })}
    </ul>
  );
}
