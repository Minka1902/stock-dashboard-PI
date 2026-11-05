import { useEffect, useState } from "react";
import CorrelationHeatmap from "./CorrelationHeatmap";
import DrawdownChart from "./DrawdownChart";
import EquityCurveChart from "./EquityCurveChart";
import ExposureBars from "./ExposureBars";
import MetricTiles from "./MetricTiles";
import SelectMenu from "./SelectMenu";
import Skeleton from "./Skeleton";
import { usePortfolioAnalytics } from "../hooks/usePortfolioAnalytics";
import { getBots } from "../api";
import { formatMoney } from "../lib/format";
import styles from "./PerformanceTab.module.css";

const BASE_ACCOUNTS = [
  { value: "manual", label: "Portfolio" },
  { value: "paper", label: "Paper account" },
];

const signedPct = (v) => (v == null ? null : `${v > 0 ? "+" : ""}${v.toFixed(2)}%`);
const tone = (v) => (v == null ? undefined : v > 0 ? "pos" : v < 0 ? "neg" : undefined);

function metricItems(m, bench) {
  return [
    { key: "twr", label: "Return (TWR)", value: signedPct(m.twr_pct), tone: tone(m.twr_pct),
      tip: "Time-weighted: deposits and withdrawals don't count as gains or losses." },
    { key: "bench", label: bench.ticker, value: signedPct(bench.return_pct), tone: tone(bench.return_pct),
      tip: "The benchmark over the same days." },
    { key: "dd", label: "Max drawdown", value: m.max_drawdown_pct == null ? null : `−${m.max_drawdown_pct.toFixed(2)}%`,
      tone: m.max_drawdown_pct ? "neg" : undefined },
    { key: "vol", label: "Volatility", value: m.volatility_pct == null ? null : `${m.volatility_pct.toFixed(1)}%`,
      tip: "Annualized standard deviation of daily returns." },
    { key: "sharpe", label: "Sharpe", value: m.sharpe?.toFixed(2) ?? null,
      tip: "Return above the 3-month T-bill per unit of volatility, annualized." },
    { key: "sortino", label: "Sortino", value: m.sortino?.toFixed(2) ?? null,
      tip: "Like Sharpe, but only downside volatility counts." },
    { key: "beta", label: `Beta vs ${bench.ticker}`, value: m.beta?.toFixed(2) ?? null,
      tip: "How much the account moved per 1% move in the benchmark." },
  ];
}

/**
 * The Performance tab of the Trading Bot page: for the Portfolio, the paper
 * account or any bot — the equity curve against its benchmark, the drawdown,
 * the headline metrics, exposure by sector and currency, how the holdings
 * move together, and a one-line risk read. Every block names its source.
 */
export default function PerformanceTab() {
  const [account, setAccount] = useState("manual");
  const [bots, setBots] = useState([]);
  const { data, error, loading } = usePortfolioAnalytics(account);

  useEffect(() => {
    getBots().then((d) => setBots(d.bots)).catch(() => {});
  }, []);

  const options = [...BASE_ACCOUNTS, ...bots.map((b) => ({
    value: `bot:${b.id}`, label: `Bot · ${b.name}${b.status === "stopped" ? " (stopped)" : ""}` }))];
  const perf = data?.performance;
  const expo = data?.exposure;
  const corr = data?.correlation;

  return (
    <div className={styles.root}>
      <div className={styles.bar}>
        <SelectMenu label="Account" value={account} options={options} onChange={setAccount}
                    className={styles.picker} />
        {data && <p className={styles.read} role="status"><strong>Risk read:</strong> {data.risk_read}</p>}
      </div>

      {error && <p className={styles.error} role="alert">{error}</p>}
      {loading && <div className={styles.section}><Skeleton w="100%" h="260px" /></div>}

      {perf && (
        <section className={styles.section} aria-labelledby="perf-title">
          <header className={styles.head}>
            <h3 id="perf-title" className={styles.title}>Performance</h3>
            <span className={styles.source}>{perf.source}{perf.as_of ? ` · as of ${perf.as_of}` : ""}</span>
          </header>
          {perf.n_snapshots < perf.min_snapshots && (
            <p className={styles.note}>
              {perf.n_snapshots} of {perf.min_snapshots} daily snapshots so far — metrics appear once there are
              enough to mean something. A snapshot is taken after each US close.
            </p>
          )}
          <MetricTiles items={metricItems(perf.metrics, perf.benchmark)} />
          {!perf.risk_free && (
            <p className={styles.note}>No 3-month Treasury rate stored yet, so Sharpe and Sortino are blank.</p>
          )}
          {perf.series.length > 1 && (
            <>
              <EquityCurveChart series={perf.series} benchmarkLabel={perf.benchmark.ticker}
                                label={options.find((o) => o.value === account)?.label} />
              <DrawdownChart series={perf.series} />
            </>
          )}
        </section>
      )}

      {expo && (
        <section className={styles.section} aria-labelledby="expo-title">
          <header className={styles.head}>
            <h3 id="expo-title" className={styles.title}>Exposure</h3>
            <span className={styles.source}>{expo.source}</span>
          </header>
          {expo.positions.length === 0 ? (
            <p className={styles.note}>No priced holdings in this account.</p>
          ) : (
            <>
              <MetricTiles items={[
                { key: "total", label: `Invested (${data.base_currency})`, value: formatMoney(expo.total, data.base_currency) },
                { key: "top3", label: "Top 3", value: `${(expo.top3_weight * 100).toFixed(0)}%` },
                { key: "eff", label: "Effective positions", value: String(expo.effective_n),
                  tip: "1 / HHI: how many equal-sized positions this book behaves like." },
              ]} />
              <div className={styles.bars}>
                <ExposureBars title="By holding" rows={expo.positions.map((p) => ({ name: p.ticker, weight: p.weight }))} />
                <ExposureBars title="By sector" rows={expo.by_sector} />
                <ExposureBars title="By currency" rows={expo.by_currency} highlightAbove={1.1} />
              </div>
            </>
          )}
          {expo.unpriced.length > 0 && (
            <p className={styles.note}>Left out — no price or FX rate: {expo.unpriced.join(", ")}.</p>
          )}
        </section>
      )}

      {corr && (
        <section className={styles.section} aria-labelledby="corr-title">
          <header className={styles.head}>
            <h3 id="corr-title" className={styles.title}>Move together</h3>
            <span className={styles.source}>{corr.source}{corr.as_of ? ` · as of ${corr.as_of}` : ""}</span>
          </header>
          <CorrelationHeatmap tickers={corr.tickers} matrix={corr.matrix} flagAbove={corr.flag_above} />
          {corr.pairs.length > 0 && (
            <p className={styles.note}>
              Above {corr.flag_above}: {corr.pairs.map((p) => `${p.a} & ${p.b} (${p.rho.toFixed(2)})`).join(", ")} — they
              tend to rise and fall together, so they diversify less than their count suggests.
            </p>
          )}
        </section>
      )}
    </div>
  );
}
