import Term from "./Term";
import styles from "./MetricTiles.module.css";

/**
 * A row of labelled numbers. A null value renders as "—", never 0. Props:
 * items ([{ key, label, value, tip?, tone? ("pos" | "neg") }]).
 */
export default function MetricTiles({ items }) {
  return (
    <dl className={styles.tiles}>
      {items.map((m) => (
        <div key={m.key} className={styles.tile}>
          <dt>{m.tip ? <Term tip={m.tip}>{m.label}</Term> : m.label}</dt>
          <dd data-tone={m.value == null ? "none" : m.tone || "flat"}>{m.value ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}
