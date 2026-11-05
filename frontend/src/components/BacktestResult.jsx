import EquityCurveChart from "./EquityCurveChart";
import MetricTiles from "./MetricTiles";
import styles from "./BacktestResult.module.css";

const pct = (v) => (v == null ? null : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
const tone = (v) => (v == null ? undefined : v > 0 ? "pos" : v < 0 ? "neg" : undefined);

function tiles(m) {
  return [
    { key: "ret", label: "Total return", value: pct(m.total_return_pct), tone: tone(m.total_return_pct) },
    { key: "cagr", label: "CAGR", value: pct(m.cagr_pct), tone: tone(m.cagr_pct) },
    { key: "bh", label: "Buy & hold", value: pct(m.buy_hold_pct), tone: tone(m.buy_hold_pct),
      tip: "Bought at the first close and held to the last, after costs." },
    { key: "dd", label: "Max drawdown", value: m.max_drawdown_pct == null ? null : `−${m.max_drawdown_pct}%` },
    { key: "sharpe", label: "Sharpe", value: m.sharpe?.toFixed(2) ?? null },
    { key: "trades", label: "Trades", value: String(m.trades) },
    { key: "win", label: "Win rate", value: m.win_rate == null ? null : `${m.win_rate}%` },
    { key: "exp", label: "Time in market", value: m.exposure_pct == null ? null : `${m.exposure_pct}%` },
  ];
}

function Stretch({ title, m, minTrades }) {
  return (
    <section className={styles.stretch}>
      <h4 className={styles.title}>{title}</h4>
      {m.insufficient && (
        <p className={styles.thin}>{m.trades} trades — no metric is shown below {minTrades}, it would be noise.</p>
      )}
      {!m.insufficient && <MetricTiles items={tiles(m)} />}
    </section>
  );
}

/**
 * A backtest's result: how many variants you've tried, the data it ran on,
 * the whole run and its in-sample / out-of-sample halves separately, the
 * equity curve, and the trade list. Props: result (POST /api/rules/backtest).
 */
export default function BacktestResult({ result: r }) {
  const series = r.equity.map((p) => ({ date: p.date, index: p.equity / r.start_cash, benchmark: null }));
  return (
    <div className={styles.root}>
      <p className={styles.meta}>
        <strong>{r.ticker}</strong> · {r.from} → {r.to} ({r.bars} bars, {r.data}) · {r.model_version} ·{" "}
        <span className={styles.variants} data-many={r.variants_tested >= 10 ? "yes" : "no"}>
          {r.variants_tested} variant{r.variants_tested === 1 ? "" : "s"} of this rule tested
        </span>
      </p>
      {r.variants_tested >= 10 && (
        <p className={styles.warn}>
          The more variants you try, the more likely the best one fits the past by luck. Trust the
          out-of-sample numbers, not the in-sample ones.
        </p>
      )}
      <Stretch title="Whole history" m={r.metrics} minTrades={r.min_trades} />
      {r.split_date && (
        <div className={styles.split}>
          <Stretch title={`In-sample (to ${r.split_date})`} m={r.in_sample} minTrades={r.min_trades} />
          <Stretch title={`Out-of-sample (from ${r.split_date})`} m={r.out_of_sample} minTrades={r.min_trades} />
        </div>
      )}
      {series.length > 1 && <EquityCurveChart series={series} label="Rule equity" height={200} />}
      {r.trades.length > 0 && (
        <details className={styles.trades}>
          <summary>Trades ({r.trades.length})</summary>
          <ul>
            {r.trades.map((t, i) => (
              <li key={i}>
                <span>{t.entry_date} → {t.exit_date}</span>
                <span>{t.entry} → {t.exit}</span>
                <span data-tone={t.return_pct >= 0 ? "pos" : "neg"}>{pct(t.return_pct)}</span>
                <span className={styles.faint}>{t.reason}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
