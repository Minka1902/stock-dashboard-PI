import styles from "./ExposureBars.module.css";

/**
 * Horizontal weight bars. Props: rows ([{ name, weight }] with weight 0–1),
 * title, max (rows shown before "+N more"; default 8), highlightAbove (weight
 * that colours a bar as a concentration; default 0.4).
 */
export default function ExposureBars({ rows, title, max = 8, highlightAbove = 0.4 }) {
  const shown = rows.slice(0, max);
  const rest = rows.length - shown.length;
  return (
    <figure className={styles.root}>
      {title && <figcaption className={styles.title}>{title}</figcaption>}
      <ul className={styles.list}>
        {shown.map((r) => (
          <li key={r.name} className={styles.row}>
            <span className={styles.name} title={r.name}>{r.name}</span>
            <span className={styles.track}>
              <span className={styles.fill} data-hot={r.weight >= highlightAbove ? "yes" : "no"}
                    style={{ width: `${Math.max(r.weight * 100, 0.5)}%` }} />
            </span>
            <span className={styles.value}>{(r.weight * 100).toFixed(1)}%</span>
          </li>
        ))}
      </ul>
      {rest > 0 && <p className={styles.more}>+{rest} more</p>}
    </figure>
  );
}
