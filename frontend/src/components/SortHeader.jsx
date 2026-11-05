import Tooltip from "./Tooltip";
import styles from "./SortHeader.module.css";

/**
 * Accessible sortable table header. Spread a `useSortableRows` `sortProps(key)`
 * onto it plus a `label`. Keeps the host table's own `<th>` padding/alignment
 * (the inner button is zero-padding and inherits the cell's type styles), so it
 * drops into any table regardless of its cell metrics.
 */
export default function SortHeader({ label, active, dir, ariaSort, onSort, className = "" }) {
  return (
    <th aria-sort={ariaSort} className={className}>
      <Tooltip content={active
        ? `Sorted by ${label}, ${dir === "asc" ? "ascending" : "descending"} — click to reverse`
        : `Sort by ${label}`}>
        <button
          type="button"
          className={styles.btn}
          data-active={active ? "yes" : "no"}
          onClick={onSort}
        >
          <span>{label}</span>
          <span className={styles.caret} aria-hidden="true">
            {active ? (dir === "asc" ? "▲" : "▼") : "↕"}
          </span>
        </button>
      </Tooltip>
    </th>
  );
}
