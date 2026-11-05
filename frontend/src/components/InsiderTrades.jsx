import { formatCount, formatCurrencyCompact, formatDate } from "../lib/format";
import styles from "./InsiderTrades.module.css";
import Tooltip from "./Tooltip";

function tone(type) {
  const t = (type || "").toLowerCase();
  if (t.includes("buy")) return "pos";
  if (t.includes("sell")) return "neg";
  return "";
}

/**
 * SEC Form 4 filings for one ticker — who traded, which way, how much.
 * Rows come from db.get_trades_for(), so this is the full filing history for
 * the symbol rather than a slice of the global feed.
 */
export default function InsiderTrades({ trades = [], ticker }) {
  if (!trades.length) {
    return (
      <p className={styles.empty}>
        No Form 4 filings recorded for {ticker}. Insider trades are ingested from
        SEC EDGAR for tickers on a watchlist or in a portfolio.
      </p>
    );
  }

  return (
    <ul className={styles.list}>
      {trades.map((t) => (
        <li key={t.accession} className={styles.row}>
          <span className={styles.type} data-tone={tone(t.transaction_type)}>
            {t.transaction_type || "—"}
          </span>
          <Tooltip truncate><span className={styles.owner}>{t.owner || "—"}</span></Tooltip>
          <Tooltip truncate><span className={styles.role}>{t.role || "—"}</span></Tooltip>
          <span className={styles.shares}>
            {t.shares != null ? formatCount(t.shares) : "—"} sh
          </span>
          <span className={styles.value} data-tone={tone(t.transaction_type)}>
            {t.value != null ? formatCurrencyCompact(t.value) : "—"}
          </span>
          <span className={styles.date}>{formatDate(t.transaction_date)}</span>
          {t.filing_url ? (
            <Tooltip content="Open the Form 4 filing on SEC EDGAR (new tab)">
              <a className={styles.filing} href={t.filing_url}
                 target="_blank" rel="noreferrer noopener">filing</a>
            </Tooltip>
          ) : <span className={styles.filing}>—</span>}
        </li>
      ))}
    </ul>
  );
}
