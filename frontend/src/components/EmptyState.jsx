import Icon from "./Icon";
import styles from "./EmptyState.module.css";

/**
 * Consistent empty / error block: icon + message + optional retry. Shared across
 * panels so the "no data yet" and error states look the same everywhere.
 */
export default function EmptyState({
  icon = "layers", title, text, onRetry, busy, error = false,
}) {
  return (
    <div className={styles.empty} data-error={error ? "yes" : "no"}>
      <span className={styles.icon}><Icon name={error ? "bell" : icon} size={22} /></span>
      {title && <p className={styles.title}>{title}</p>}
      {text && <p className={styles.text}>{text}</p>}
      {onRetry && (
        <button className={styles.btn} onClick={onRetry} disabled={busy}>
          {busy ? "Refreshing…" : "Refresh now"}
        </button>
      )}
    </div>
  );
}
