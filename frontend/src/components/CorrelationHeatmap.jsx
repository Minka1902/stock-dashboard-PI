import styles from "./CorrelationHeatmap.module.css";

// Diverging scale: positive correlation tints toward caution, negative toward info.
function tint(rho) {
  if (rho == null) return "transparent";
  const a = Math.min(Math.abs(rho), 1) * 70;
  return `color-mix(in oklch, var(${rho >= 0 ? "--caution" : "--info"}) ${a.toFixed(0)}%, transparent)`;
}

/**
 * A ticker × ticker grid of daily-return correlations. Cells above
 * `flagAbove` are outlined as "move together"; a pair without enough shared
 * days shows a dash, never a number. Props: tickers, matrix (rows of
 * rho | null), flagAbove (0.8).
 */
export default function CorrelationHeatmap({ tickers, matrix, flagAbove = 0.8 }) {
  if (tickers.length < 2) return <p className={styles.empty}>Needs at least two priced holdings.</p>;
  return (
    <div className={styles.wrap}>
      <table className={styles.grid} aria-label="Daily-return correlation between holdings">
        <thead>
          <tr>
            <th aria-hidden="true" />
            {tickers.map((t) => <th key={t} scope="col">{t}</th>)}
          </tr>
        </thead>
        <tbody>
          {tickers.map((row, i) => (
            <tr key={row}>
              <th scope="row">{row}</th>
              {matrix[i].map((rho, j) => (
                <td key={tickers[j]} style={{ background: i === j ? "var(--surface-2)" : tint(rho) }}
                    data-flag={i !== j && rho != null && rho > flagAbove ? "yes" : "no"}
                    title={i === j ? row : `${row} vs ${tickers[j]}: ${rho == null ? "not enough shared days" : rho.toFixed(2)}`}>
                  {i === j ? "" : rho == null ? "—" : rho.toFixed(2)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
