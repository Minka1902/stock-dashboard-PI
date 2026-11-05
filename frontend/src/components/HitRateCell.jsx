import styles from "./HitRateCell.module.css";

/**
 * A hit rate with its 95% (Wilson) interval drawn as a band on a 0–100 track,
 * a tick at 50%, and an explicit "not enough data yet (n=12)" instead of a
 * number while the sample is too small to mean anything.
 *
 * Props: stats ({ n, hit_rate, hit_rate_ci: [lo, hi], insufficient }).
 */
export default function HitRateCell({ stats }) {
  if (!stats || stats.hit_rate == null) return <span className={styles.unknown}>—</span>;
  if (stats.insufficient) {
    return <span className={styles.thin}>not enough data yet (n={stats.n})</span>;
  }
  const [lo, hi] = stats.hit_rate_ci;
  return (
    <span className={styles.cell}>
      <span className={styles.value} data-tone={stats.hit_rate >= 50 ? "pos" : "neg"}>{stats.hit_rate}%</span>
      <span className={styles.track} role="img" aria-label={`95% interval ${lo}% to ${hi}%`}
            title={`95% interval ${lo}–${hi}% over ${stats.n} outcomes`}>
        <span className={styles.half} />
        <span className={styles.band} style={{ left: `${lo}%`, width: `${hi - lo}%` }} />
        <span className={styles.mark} style={{ left: `${stats.hit_rate}%` }} />
      </span>
    </span>
  );
}
