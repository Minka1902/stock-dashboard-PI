import { motion } from "motion/react";
import Skeleton from "./Skeleton";
import ViewAll from "./ViewAll";
import CollapseToggle from "./CollapseToggle";
import EmptyState from "./EmptyState";
import SortHeader from "./SortHeader";
import { useSortableRows } from "../hooks/useSortableRows";
import { prefersReducedMotion, staggerContainer, staggerItem } from "../lib/motionConfig";
import {
  formatCurrencyCompact,
  formatCurrencyFull,
  formatCount,
  formatDate,
} from "../lib/format";
import styles from "./TradesPanel.module.css";
import Tooltip from "./Tooltip";
import InfoTip from "./InfoTip";
import ScrollHint from "./ScrollHint";

const COMPACT_LIMIT = 5;

// Stable accessor map for the sortable headers (numbers/dates sort numerically).
const COLUMNS = {
  ticker: (t) => t.ticker,
  action: (t) => t.transaction_type,
  insider: (t) => t.owner,
  shares: (t) => t.shares,
  value: (t) => t.value,
  date: (t) => Date.parse(t.transaction_date) || 0,
};

function typeTone(type) {
  if (type === "Buy") return "buy";
  if (type === "Sell") return "sell";
  return "neutral";
}

function SkeletonRows({ rows = 8 }) {
  return Array.from({ length: rows }).map((_, i) => (
    <tr key={i}>
      <td><Skeleton w="46px" /></td>
      <td><Skeleton w="64px" /></td>
      <td><Skeleton w="70%" /></td>
      <td className={styles.num}><Skeleton w="60px" /></td>
      <td className={styles.num}><Skeleton w="70px" /></td>
      <td><Skeleton w="64px" /></td>
    </tr>
  ));
}

export default function TradesPanel({ trades, loading, busy, onRefresh, compact = false, onViewAll, collapsible = false, collapsed = false, onToggleCollapse }) {
  const showEmpty = !loading && trades.length === 0;
  const { sorted, sortProps } = useSortableRows(trades, COLUMNS);
  const rows = compact ? sorted.slice(0, COMPACT_LIMIT) : sorted;

  return (
    <section className={styles.panel} id="trades">
      <header className={styles.head}>
        {collapsible && <CollapseToggle collapsed={collapsed} onClick={onToggleCollapse} label="Trades" />}
        <div>
          <h2 className={styles.title}>Insider trades <InfoTip term="form4" /></h2>
          <p className={styles.subtitle}>
            Corporate insiders (SEC Form 4) buying and selling their own stock
          </p>
        </div>
        {compact && onViewAll && <ViewAll onClick={onViewAll} />}
      </header>

      {!collapsed && (showEmpty ? (
        <EmptyState icon="trending" title="No insider filings loaded yet" onRetry={onRefresh} busy={busy} />
      ) : (
        <ScrollHint className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <SortHeader label="Ticker" {...sortProps("ticker")} />
                <SortHeader label="Action" {...sortProps("action")} />
                <SortHeader label="Insider" {...sortProps("insider")} />
                <SortHeader label="Shares" className={styles.num} {...sortProps("shares")} />
                <SortHeader label="Value" className={styles.num} {...sortProps("value")} />
                <SortHeader label="Date" {...sortProps("date")} />
              </tr>
            </thead>
            <motion.tbody
              variants={loading ? undefined : staggerContainer}
              initial={loading || prefersReducedMotion() ? false : "hidden"}
              animate="visible"
            >
              {loading ? (
                <SkeletonRows />
              ) : (
                rows.map((t) => (
                  <motion.tr key={t.accession} variants={staggerItem}>
                    <td>
                      <Tooltip content={`${t.company} — open the Form 4 filing on SEC EDGAR (new tab)`}>
                        <a
                          className={styles.ticker}
                          href={t.filing_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {t.ticker || "—"}
                        </a>
                      </Tooltip>
                    </td>
                    <td>
                      <span className={styles.badge} data-tone={typeTone(t.transaction_type)}>
                        {t.transaction_type}
                      </span>
                    </td>
                    <td className={styles.insider}>
                      <Tooltip truncate><span className={styles.owner}>{t.owner}</span></Tooltip>
                      <Tooltip truncate><span className={styles.role}>{t.role}</span></Tooltip>
                    </td>
                    <td className={`${styles.num} tabular`}>{formatCount(Math.round(t.shares))}</td>
                    <td className={`${styles.num} tabular`}>
                      {t.value > 0 ? (
                        <Tooltip content={`Exact value: ${formatCurrencyFull(t.value)}`}>
                          <span>{formatCurrencyCompact(t.value)}</span>
                        </Tooltip>
                      ) : "—"}
                    </td>
                    <td className={styles.muted}>{formatDate(t.transaction_date)}</td>
                  </motion.tr>
                ))
              )}
            </motion.tbody>
          </table>
        </ScrollHint>
      ))}
    </section>
  );
}
