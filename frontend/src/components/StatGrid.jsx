import Icon from "./Icon";
import Skeleton from "./Skeleton";
import {
  formatCurrencyCompact,
  formatCount,
  formatRelativeTime,
} from "../lib/format";
import styles from "./StatGrid.module.css";

// KPI cards derived ONLY from real data we actually have.
export default function StatGrid({ contracts, sources, loading }) {
  const amounts = contracts.map((c) => c.amount);
  const largest = amounts.length ? Math.max(...amounts) : null;
  const total = amounts.reduce((sum, a) => sum + a, 0);
  const usa = sources.find((s) => s.source === "usaspending");
  const lastRefresh = usa ? formatRelativeTime(usa.last_refreshed_at) : "never";
  const sourceOk = usa ? usa.status === "ok" : null;

  const cards = [
    {
      key: "largest",
      label: "Largest contract",
      value: largest != null ? formatCurrencyCompact(largest) : "—",
      icon: "trending",
      tone: "accent",
    },
    {
      key: "count",
      label: "Contracts tracked",
      value: formatCount(contracts.length),
      icon: "contract",
      tone: "neutral",
    },
    {
      key: "total",
      label: "Combined value (shown)",
      value: contracts.length ? formatCurrencyCompact(total) : "—",
      icon: "layers",
      tone: "neutral",
    },
    {
      key: "refresh",
      label: "Last refresh",
      value: lastRefresh,
      icon: "refresh",
      tone: sourceOk === false ? "negative" : "positive",
      small: true,
    },
  ];

  return (
    <div className={styles.grid}>
      {cards.map((c) => (
        <div key={c.key} className={styles.card}>
          <div className={styles.head}>
            <span className={styles.label}>{c.label}</span>
            <span className={styles.icon} data-tone={c.tone}>
              <Icon name={c.icon} size={16} />
            </span>
          </div>
          {loading ? (
            <Skeleton w="60%" h="28px" />
          ) : (
            <span className={`${styles.value} ${c.small ? styles.small : ""} tabular`}>
              {c.value}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
