import Tooltip from "./Tooltip";
import styles from "./SparkRange.module.css";

const SPARK_RANGES = [
  { key: "1d", label: "1D", name: "Last day" },
  { key: "3d", label: "3D", name: "Last 3 days" },
  { key: "1w", label: "1W", name: "Last week" },
  { key: "1m", label: "1M", name: "Last month" },
];

// Segmented control selecting the sparkline range for a table.
export default function SparkRange({ value, onChange }) {
  return (
    <div className={styles.toggle} role="group" aria-label="Sparkline range">
      {SPARK_RANGES.map((r) => (
        <Tooltip key={r.key} content={`Sparklines: ${r.name.toLowerCase()}`}>
          <button
            type="button"
            className={styles.btn}
            data-active={value === r.key ? "yes" : "no"}
            aria-pressed={value === r.key}
            onClick={() => onChange(r.key)}
          >
            {r.label}
          </button>
        </Tooltip>
      ))}
    </div>
  );
}
