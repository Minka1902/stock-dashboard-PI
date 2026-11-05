import Icon from "./Icon";
import Skeleton from "./Skeleton";
import ViewAll from "./ViewAll";
import CollapseToggle from "./CollapseToggle";
import {
  formatCurrencyCompact,
  formatCurrencyFull,
  formatDate,
} from "../lib/format";
import styles from "./ContractsPanel.module.css";
import Tooltip from "./Tooltip";
import ScrollHint from "./ScrollHint";

const COMPACT_LIMIT = 5;

function SkeletonRows({ rows = 8 }) {
  return Array.from({ length: rows }).map((_, i) => (
    <tr key={i}>
      <td><Skeleton w="70%" /></td>
      <td><Skeleton w="50%" /></td>
      <td className={styles.amountCell}><Skeleton w="60px" /></td>
      <td><Skeleton w="64px" /></td>
      <td><Skeleton w="80%" /></td>
    </tr>
  ));
}

export default function ContractsPanel({ contracts, loading, busy, onRefresh, compact = false, onViewAll, collapsible = false, collapsed = false, onToggleCollapse }) {
  const showEmpty = !loading && contracts.length === 0;
  const rows = compact ? contracts.slice(0, COMPACT_LIMIT) : contracts;

  return (
    <section className={styles.panel} id="contracts">
      <header className={styles.head}>
        {collapsible && <CollapseToggle collapsed={collapsed} onClick={onToggleCollapse} label="Contracts" />}
        <div>
          <h2 className={styles.title}>Biggest recent federal contracts</h2>
          <p className={styles.subtitle}>
            Sourced live from USASpending.gov · sorted by award amount
          </p>
        </div>
        {compact && onViewAll && <ViewAll onClick={onViewAll} />}
      </header>

      {!collapsed && (showEmpty ? (
        <div className={styles.empty}>
          <span className={styles.emptyIcon}>
            <Icon name="contract" size={26} />
          </span>
          <p className={styles.emptyTitle}>No contracts loaded yet</p>
          <p className={styles.emptyText}>
            Pull the latest federal awards to populate the table.
          </p>
          <button className={styles.emptyBtn} onClick={onRefresh} disabled={busy}>
            {busy ? "Refreshing…" : "Refresh now"}
          </button>
        </div>
      ) : (
        <ScrollHint className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Recipient</th>
                <th>Agency</th>
                <th className={styles.amountCell}>Amount</th>
                <th>Start</th>
                <th>Award ID</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <SkeletonRows />
              ) : (
                rows.map((c) => (
                  <tr key={c.external_id}>
                    <Tooltip truncate>
                      <td className={styles.recipient}>
                        {c.recipient_name}
                      </td>
                    </Tooltip>
                    <td>
                      <Tooltip truncate>
                        <span className={styles.agency}>
                          {c.awarding_agency}
                        </span>
                      </Tooltip>
                    </td>
                    <td className={`${styles.amountCell} ${styles.amount} tabular`}>
                      <Tooltip content={`Exact amount: ${formatCurrencyFull(c.amount)}`}>
                        <span>{formatCurrencyCompact(c.amount)}</span>
                      </Tooltip>
                    </td>
                    <td className={styles.muted}>{formatDate(c.start_date)}</td>
                    <Tooltip truncate>
                      <td className={styles.award}>
                        {c.award_id || "—"}
                      </td>
                    </Tooltip>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </ScrollHint>
      ))}
    </section>
  );
}
