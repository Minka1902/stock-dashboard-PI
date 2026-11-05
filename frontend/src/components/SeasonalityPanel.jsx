import { BarChart, Bar, Cell, Tooltip, ResponsiveContainer } from "recharts";
import Icon from "./Icon";
import Skeleton from "./Skeleton";
import ViewAll from "./ViewAll";
import CollapseToggle from "./CollapseToggle";
import TickerLabel from "./TickerLabel";
import HintTip from "./Tooltip";
import InfoTip from "./InfoTip";
import { currencyForSymbol, formatPercentSigned, formatPrice } from "../lib/format";
import styles from "./SeasonalityPanel.module.css";

const COMPACT_LIMIT = 5;

// Display metadata for each window key the backend emits.
const WINDOW_META = {
  fwd_day: { label: "Next Day", hint: "Return on the trading day around this date" },
  fwd_week: { label: "Next Week", hint: "Return over the ~7 days following this date" },
  fwd_month: { label: "Next Month", hint: "Return over the ~30 days following this date" },
  cal_week: { label: "This Calendar Week", hint: "Return during the calendar week containing this date" },
  cal_month: { label: "This Calendar Month", hint: "Return during this calendar month" },
};

const WINDOW_ORDER = ["fwd_day", "fwd_week", "fwd_month", "cal_week", "cal_month"];

// Aggregate per-year returns over the last N years (or all). per_year is sorted
// ascending by year from the backend.
function summarize(perYear, lookback) {
  if (!perYear || perYear.length === 0) return null;
  const sliced = lookback === "all" ? perYear : perYear.slice(-lookback);
  if (sliced.length === 0) return null;
  const returns = sliced.map((e) => e.return);
  const n = returns.length;
  const avg = returns.reduce((a, b) => a + b, 0) / n;
  const sorted = [...returns].sort((a, b) => a - b);
  const mid = Math.floor(n / 2);
  const median = n % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  const ups = returns.filter((r) => r > 0).length;
  return {
    avg,
    median,
    winRate: ups / n,
    ups,
    n,
    best: sorted[n - 1],
    worst: sorted[0],
    series: sliced,
  };
}

function YearBars({ series }) {
  const data = series.map((e) => ({ year: e.year, pct: +(e.return * 100).toFixed(2) }));
  const width = Math.max(72, data.length * 11);
  return (
    <div className={styles.bars} style={{ width }}>
      <ResponsiveContainer width="100%" height={34}>
        <BarChart data={data} margin={{ top: 2, bottom: 2, left: 0, right: 0 }}>
          <Tooltip
            cursor={false}
            isAnimationActive={false}
            contentStyle={{
              background: "var(--surface-2)",
              border: "1px solid var(--border)",
              borderRadius: "var(--r-sm)",
              fontSize: "12px",
              color: "var(--text)",
              padding: "4px 8px",
            }}
            formatter={(v) => [`${v > 0 ? "+" : ""}${v}%`, "Return"]}
            labelFormatter={(l) => `${l}`}
          />
          <Bar dataKey="pct" isAnimationActive={false} radius={[1, 1, 0, 0]}>
            {data.map((d, i) => (
              <Cell key={i} fill={d.pct >= 0 ? "var(--positive)" : "var(--negative)"} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function WindowCell({ window, lookback }) {
  const meta = WINDOW_META[window.key] || { label: window.key, hint: "" };
  const stats = summarize(window.per_year, lookback);
  return (
    <div className={styles.window}>
      <HintTip content={meta.hint}>
        <div className={styles.wlabel} tabIndex={0}>{meta.label}</div>
      </HintTip>
      {stats ? (
        <>
          <div className={styles.wstats}>
            <HintTip content={`Average ${formatPercentSigned(stats.avg)} · median ${formatPercentSigned(stats.median)} · best ${formatPercentSigned(stats.best)} · worst ${formatPercentSigned(stats.worst)}`}>
              <span
                className={styles.avg}
                data-tone={stats.avg > 0 ? "pos" : stats.avg < 0 ? "neg" : "flat"}
                tabIndex={0}
              >
                {formatPercentSigned(stats.avg)}
              </span>
            </HintTip>
            <span className={styles.win}>
              <strong>{stats.ups}/{stats.n}</strong> up
            </span>
          </div>
          <YearBars series={stats.series} />
        </>
      ) : (
        <span className={styles.muted}>not enough history</span>
      )}
    </div>
  );
}

// "Where was this stock on this day 1/2/5/max years ago" — close then, and
// the move from that close to the current price when a live quote is known.
function AnchorStrip({ anchors, price, currency }) {
  if (!anchors || anchors.length === 0) return null;
  return (
    <div className={styles.anchorStrip}>
      {anchors.map((an) => {
        const delta = price && an.close ? (price / an.close - 1) * 100 : null;
        const label = an.years_ago === "max" ? "earliest" : `${an.years_ago}y ago`;
        return (
          <HintTip
            key={String(an.years_ago)}
            content={`Close on ${an.date}${delta != null ? ` → ${delta >= 0 ? "+" : ""}${delta.toFixed(1)}% to now` : ""}`}
          >
            <span className={styles.anchorChip} tabIndex={0}>
              <em>{label}</em> {formatPrice(an.close, currency)}
              {delta != null && (
                <b data-tone={delta >= 0 ? "pos" : "neg"}>
                  {delta >= 0 ? "+" : ""}{delta.toFixed(1)}%
                </b>
              )}
            </span>
          </HintTip>
        );
      })}
    </div>
  );
}

function SkeletonRows({ rows = 4 }) {
  return Array.from({ length: rows }).map((_, i) => (
    <li key={i} className={styles.row}>
      <div className={styles.tickerCol}><Skeleton w="52px" /></div>
      <div className={styles.windows}>
        {Array.from({ length: 3 }).map((_, j) => (
          <div key={j} className={styles.window}>
            <Skeleton w="80px" h="11px" />
            <Skeleton w="96px" h="34px" radius="6px" />
          </div>
        ))}
      </div>
    </li>
  ));
}

export default function SeasonalityPanel({ data, settings, quotes, loading, busy, onRefresh, compact = false, onViewAll, collapsible = false, collapsed = false, onToggleCollapse }) {
  const showEmpty = !loading && data.length === 0;
  const lookback = settings?.seasonalityLookback ?? 10;
  const activeKeys = settings?.seasonalityWindows ?? ["fwd_week", "fwd_month", "cal_month"];
  const lookbackLabel = lookback === "all" ? "all years" : `last ${lookback} years`;
  const rows = compact ? data.slice(0, COMPACT_LIMIT) : data;

  return (
    <section className={styles.panel} id="seasonality">
      <header className={styles.head}>
        {collapsible && <CollapseToggle collapsed={collapsed} onClick={onToggleCollapse} label="Seasonality" />}
        <div>
          <h2 className={styles.title}>Seasonality — This Time in Past Years <InfoTip term="seasonality" /></h2>
          <p className={styles.subtitle}>
            How each watchlist ticker historically moved around today's date · {lookbackLabel} ·
            green/red bars are individual years (configure in Settings)
          </p>
        </div>
        {compact && onViewAll && <ViewAll onClick={onViewAll} />}
      </header>

      {!collapsed && (showEmpty ? (
        <div className={styles.empty}>
          <span className={styles.emptyIcon}><Icon name="calendar" size={24} /></span>
          <p className={styles.emptyTitle}>Add tickers to your watchlist to see seasonal history</p>
          <button className={styles.emptyBtn} onClick={onRefresh} disabled={busy}>
            {busy ? "Refreshing…" : "Refresh now"}
          </button>
        </div>
      ) : (
        <ul className={styles.list}>
          {loading ? (
            <SkeletonRows />
          ) : (
            rows.map((s) => {
              let windows = [];
              try { windows = JSON.parse(s.windows_json); } catch { /* keep empty */ }
              let anchors = [];
              try { anchors = JSON.parse(s.anchors_json || "[]"); } catch { /* keep empty */ }
              const byKey = Object.fromEntries(windows.map((w) => [w.key, w]));
              const shown = WINDOW_ORDER.filter((k) => activeKeys.includes(k) && byKey[k]);
              return (
                <li key={s.ticker} className={styles.row}>
                  <div className={styles.tickerCol}>
                    <TickerLabel ticker={s.ticker} className={styles.ticker} />
                    <span className={styles.meta}>{s.history_years} yrs</span>
                  </div>
                  <div className={styles.rowBody}>
                    <div className={styles.windows}>
                      {shown.length === 0 ? (
                        <span className={styles.muted}>No windows selected — enable some in Settings</span>
                      ) : (
                        shown.map((k) => (
                          <WindowCell key={k} window={byKey[k]} lookback={lookback} />
                        ))
                      )}
                    </div>
                    <AnchorStrip anchors={anchors} price={quotes?.[s.ticker]?.price}
                                 currency={quotes?.[s.ticker]?.currency || currencyForSymbol(s.ticker)} />
                  </div>
                </li>
              );
            })
          )}
        </ul>
      ))}
    </section>
  );
}
