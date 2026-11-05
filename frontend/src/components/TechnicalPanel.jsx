import { LineChart, Line, ResponsiveContainer } from "recharts";
import Icon from "./Icon";
import Skeleton from "./Skeleton";
import ViewAll from "./ViewAll";
import CollapseToggle from "./CollapseToggle";
import { currencyForSymbol, formatMoney, formatRelativeTime, freshnessTone } from "../lib/format";

// Per-row prices in the ticker's own currency (₪ for Tel Aviv listings).
const money = (v, t) => formatMoney(v, currencyForSymbol(t) || "USD", { compact: true });
import styles from "./TechnicalPanel.module.css";
import Tooltip from "./Tooltip";
import Term from "./Term";
import ScrollHint from "./ScrollHint";
import { freshnessTip } from "../lib/freshness";

const COMPACT_LIMIT = 5;

function RsiCell({ rsi }) {
  if (rsi == null) return <span className={styles.muted}>—</span>;
  const tone = rsi < 30 ? "oversold" : rsi > 70 ? "overbought" : "neutral";
  const read = tone === "oversold" ? "below 30: oversold" : tone === "overbought" ? "above 70: overbought" : "between 30 and 70";
  return (
    <Tooltip content={`RSI ${rsi.toFixed(1)} — ${read}`}>
      <span className={styles.rsi} data-tone={tone}>{rsi.toFixed(1)}</span>
    </Tooltip>
  );
}

function CrossCell({ golden_cross }) {
  if (golden_cross == null) return <span className={styles.muted}>—</span>;
  return (
    <span className={styles.badge} data-tone={golden_cross ? "buy" : "sell"}>
      {golden_cross ? "Golden" : "Death"}
    </span>
  );
}

function MacdCell({ macd_crossover, macd }) {
  if (macd == null) return <span className={styles.muted}>—</span>;
  if (macd_crossover) {
    return <span className={styles.badge} data-tone="buy">Crossover</span>;
  }
  const tone = macd > 0 ? "pos" : "neg";
  return <span className={styles.chg} data-tone={tone}>{macd > 0 ? "+" : ""}{macd.toFixed(2)}</span>;
}

function VolCell({ rel_volume }) {
  if (rel_volume == null) return <span className={styles.muted}>—</span>;
  const tone = rel_volume > 1.5 ? "pos" : rel_volume < 0.7 ? "neg" : "neutral";
  return (
    <Tooltip content={`Today's volume is ${rel_volume.toFixed(1)}× its average. Above 1.5× reads as strong participation.`}>
      <span className={styles.chg} data-tone={tone}>{rel_volume.toFixed(1)}×</span>
    </Tooltip>
  );
}

function FreshnessCell({ fetched_at }) {
  const text = formatRelativeTime(fetched_at);
  const tone = freshnessTone(fetched_at);
  return (
    <Tooltip content={freshnessTip(fetched_at)}>
      <span className={styles.freshness} data-tone={tone}>{text}</span>
    </Tooltip>
  );
}

function MiniSparkline({ pricesJson }) {
  let prices = [];
  try { prices = JSON.parse(pricesJson); } catch { /* empty */ }
  if (prices.length < 2) return <span className={styles.muted}>—</span>;
  const data = prices.map((v, i) => ({ i, v }));
  const last = prices[prices.length - 1];
  const first = prices[0];
  const color = last >= first ? "var(--positive)" : "var(--negative)";
  return (
    <ResponsiveContainer width={60} height={28}>
      <LineChart data={data}>
        <Line type="monotone" dataKey="v" dot={false} strokeWidth={1.5} stroke={color} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

function SkeletonRows({ rows = 5 }) {
  return Array.from({ length: rows }).map((_, i) => (
    <tr key={i}>
      <td><Skeleton w="50px" /></td>
      <td className={styles.num}><Skeleton w="52px" /></td>
      <td className={styles.num}><Skeleton w="44px" /></td>
      <td className={styles.num}><Skeleton w="38px" /></td>
      <td className={styles.num}><Skeleton w="52px" /></td>
      <td className={styles.num}><Skeleton w="52px" /></td>
      <td><Skeleton w="56px" /></td>
      <td><Skeleton w="64px" /></td>
      <td className={styles.num}><Skeleton w="40px" /></td>
      <td className={styles.num}><Skeleton w="52px" /></td>
      <td className={styles.num}><Skeleton w="52px" /></td>
      <td><Skeleton w="60px" /></td>
      <td><Skeleton w="56px" /></td>
    </tr>
  ));
}

export default function TechnicalPanel({ data, loading, busy, onRefresh, compact = false, onViewAll, collapsible = false, collapsed = false, onToggleCollapse }) {
  const showEmpty = !loading && data.length === 0;
  const rows = compact ? data.slice(0, COMPACT_LIMIT) : data;

  return (
    <section className={styles.panel} id="signals">
      <header className={styles.head}>
        {collapsible && <CollapseToggle collapsed={collapsed} onClick={onToggleCollapse} label="Technical signals" />}
        <div>
          <h2 className={styles.title}>Technical Signals</h2>
          <p className={styles.subtitle}>
            RSI · MACD · moving averages · volume · 52-week range · per watchlist ticker
          </p>
        </div>
        {compact && onViewAll && <ViewAll onClick={onViewAll} />}
      </header>

      {!collapsed && (showEmpty ? (
        <div className={styles.empty}>
          <span className={styles.emptyIcon}><Icon name="spark" size={24} /></span>
          <p className={styles.emptyTitle}>Add tickers to your watchlist to see signals</p>
          <button className={styles.emptyBtn} onClick={onRefresh} disabled={busy}>
            {busy ? "Refreshing…" : "Refresh now"}
          </button>
        </div>
      ) : (
        <ScrollHint className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Ticker</th>
                <th className={styles.num}>Price</th>
                <th className={styles.num}><Term tip="Change since the previous close, in percent">Chg%</Term></th>
                <th className={styles.num}><Term term="rsi" tip="Computed over 14 days.">RSI14</Term></th>
                <th className={styles.num}><Term term="moving_average" tip="MA50: the 50-day average.">MA50</Term></th>
                <th className={styles.num}><Term term="moving_average" tip="MA200: the 200-day average.">MA200</Term></th>
                <th><Term tip="Golden: MA50 is above MA200 (uptrend). Death: MA50 is below MA200 (downtrend).">Cross</Term></th>
                <th><Term term="macd" tip="Shows “Crossover” when it just crossed its signal line; otherwise the MACD value.">MACD</Term></th>
                <th className={styles.num}><Term term="relative_volume">Vol Ratio</Term></th>
                <th className={styles.num}><Term tip="Highest price in the last 52 weeks">52W Hi</Term></th>
                <th className={styles.num}><Term tip="Lowest price in the last 52 weeks">52W Lo</Term></th>
                <th><Term tip="Recent closing prices; green if the last is above the first">Trend</Term></th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <SkeletonRows />
              ) : (
                rows.map((s) => (
                  <tr key={s.ticker}>
                    <td className={styles.ticker}>{s.ticker}</td>
                    <td className={`${styles.num} tabular`}>{s.price != null ? money(s.price, s.ticker) : "—"}</td>
                    <td className={`${styles.num} tabular`}>
                      {s.change_pct != null ? (
                        <span data-tone={s.change_pct >= 0 ? "pos" : "neg"} className={styles.chg}>
                          {s.change_pct >= 0 ? "+" : ""}{s.change_pct.toFixed(2)}%
                        </span>
                      ) : "—"}
                    </td>
                    <td className={styles.num}><RsiCell rsi={s.rsi14} /></td>
                    <td className={`${styles.num} tabular`}>{s.ma50 != null ? money(s.ma50, s.ticker) : "—"}</td>
                    <td className={`${styles.num} tabular`}>{s.ma200 != null ? money(s.ma200, s.ticker) : "—"}</td>
                    <td><CrossCell golden_cross={s.golden_cross} /></td>
                    <td><MacdCell macd_crossover={s.macd_crossover} macd={s.macd} /></td>
                    <td className={styles.num}><VolCell rel_volume={s.rel_volume} /></td>
                    <td className={`${styles.num} tabular`}>{s.high_52w != null ? money(s.high_52w, s.ticker) : "—"}</td>
                    <td className={`${styles.num} tabular`}>{s.low_52w != null ? money(s.low_52w, s.ticker) : "—"}</td>
                    <td><MiniSparkline pricesJson={s.prices_json} /></td>
                    <td><FreshnessCell fetched_at={s.fetched_at} /></td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </ScrollHint>
      ))}
    </section>
  );
}
