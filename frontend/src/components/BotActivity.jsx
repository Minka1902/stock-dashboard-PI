import ScrollHint from "./ScrollHint";
import SortHeader from "./SortHeader";
import { useSortableRows } from "../hooks/useSortableRows";
import { formatMoney, formatRelativeTime } from "../lib/format";
import styles from "./BotActivity.module.css";

const COLUMNS = {
  at: (e) => e.at,
  kind: (e) => e.kind,
  ticker: (e) => e.ticker,
};
const KIND_LABEL = { buy: "Buy", sell: "Sell", skip: "Skip", error: "Error", info: "Info" };

/**
 * A bot's decision log: every fill and every skip worth explaining, with the
 * reason in words. Newest first, sortable; rows stack into cards whenever
 * the log itself is narrow (a container query: a phone, or a bot card).
 * Props: events [{at, kind, ticker, qty, price, reason}], currency, empty text.
 */
export default function BotActivity({ events, currency, emptyText = "Nothing yet — the bot acts while its market is open." }) {
  const { sorted, sortProps } = useSortableRows(events, COLUMNS);

  if (!events.length) return <p className={styles.empty}>{emptyText}</p>;
  return (
    <div className={styles.root}>
      <ScrollHint className={styles.wrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <SortHeader label="When" {...sortProps("at")} />
              <SortHeader label="Action" {...sortProps("kind")} />
              <SortHeader label="Ticker" {...sortProps("ticker")} />
              <th className={styles.num}>Qty</th>
              <th className={styles.num}>Price</th>
              <th>Why</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((e) => (
              <tr key={e.id}>
                <td data-col="at" title={e.at}>{formatRelativeTime(e.at)}</td>
                <td data-col="kind"><span className={styles.kind} data-kind={e.kind}>{KIND_LABEL[e.kind] || e.kind}</span></td>
                <td data-col="ticker">{e.ticker || "—"}</td>
                <td data-col="qty" className={styles.num}>{e.qty != null ? e.qty : "—"}</td>
                <td data-col="price" className={styles.num}>{e.price != null ? formatMoney(e.price, currency) : "—"}</td>
                <td data-col="reason" className={styles.reason}>{e.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </ScrollHint>
    </div>
  );
}
