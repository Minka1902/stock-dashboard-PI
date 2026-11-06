import Tooltip from "./Tooltip";
import styles from "./Stat.module.css";

/**
 * A labelled metric: small caps label over a mono value. `tone`: pos | neg |
 * caution colours the value; `tip` adds a tooltip on the value; `className`
 * lets the caller place it in its own grid.
 */
export default function Stat({ label, value, tone, tip, className }) {
  const shown = <span className={styles.value} data-tone={tone}>{value}</span>;
  return (
    <div className={`${styles.stat} ${className || ""}`}>
      <span className={styles.label}>{label}</span>
      {tip ? <Tooltip content={tip}>{shown}</Tooltip> : shown}
    </div>
  );
}
