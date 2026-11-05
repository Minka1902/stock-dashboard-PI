import Icon from "./Icon";
import Tooltip from "./Tooltip";
import { formatRelativeTime } from "../lib/format";
import styles from "./PanelHeader.module.css";

/**
 * Shared panel header: title + subtitle, an optional data-age badge, and an
 * optional per-panel refresh. Normalizes the header treatment across panels
 * (the design pass in Task 15) without changing any data contract.
 */
export default function PanelHeader({
  title, subtitle, asOf, busy, onRefresh, right, children,
}) {
  return (
    <header className={styles.head}>
      <div className={styles.text}>
        <h2 className={styles.title}>{title}</h2>
        {subtitle && <p className={styles.subtitle}>{subtitle}</p>}
      </div>
      <div className={styles.right}>
        {asOf && <span className={styles.age}>{formatRelativeTime(asOf)}</span>}
        {right}
        {onRefresh && (
          <Tooltip content={busy ? "Refreshing…" : `Refresh ${typeof title === "string" ? title : "this panel"}`}>
            <button className={styles.refresh} onClick={onRefresh} disabled={busy} aria-label="Refresh">
              <span className={busy ? styles.spin : ""}><Icon name="refresh" size={14} /></span>
            </button>
          </Tooltip>
        )}
        {children}
      </div>
    </header>
  );
}
