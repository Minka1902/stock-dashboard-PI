import {
  LineChart,
  Line,
  ResponsiveContainer,
  ReferenceLine,
  Tooltip,
  YAxis,
} from "recharts";
import Icon from "./Icon";
import Skeleton from "./Skeleton";
import ViewAll from "./ViewAll";
import CollapseToggle from "./CollapseToggle";
import styles from "./YieldCurvePanel.module.css";
import InfoTip from "./InfoTip";
import HintTip from "./Tooltip";

// Recharts draws its hover cursor in #ccc unless told otherwise.
const CURSOR = { stroke: "var(--border-strong)" };

const TOOLTIP_STYLE = {
  background: "var(--surface-2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--r-sm)",
  fontSize: "12px",
  color: "var(--text)",
  padding: "6px 10px",
};

export default function YieldCurvePanel({ data, loading, busy, onRefresh, compact = false, onViewAll, collapsible = false, collapsed = false, onToggleCollapse }) {
  const showEmpty = !loading && data.length === 0;
  const latest = data.length > 0 ? data[data.length - 1] : null;
  const spread = latest?.spread ?? null;
  const spreadBps = spread !== null ? (spread * 100).toFixed(0) : null;
  const tone = spread === null ? "neutral" : spread >= 0 ? "positive" : "negative";

  const chartData = data.map((p) => ({ date: p.date, spread: p.spread }));

  return (
    <section className={styles.panel} id="yield-curve">
      <header className={styles.head}>
        {collapsible && <CollapseToggle collapsed={collapsed} onClick={onToggleCollapse} label="Yield Curve" />}
        <div>
          <h2 className={styles.title}>US Treasury Yield Curve <InfoTip term="yield_curve" /></h2>
          <p className={styles.subtitle}>
            10yr − 2yr spread · negative = inverted · normalization often precedes a boom
          </p>
        </div>
        {latest && !loading && (
          <HintTip content={spread === null
            ? "No 10-year − 2-year spread available yet"
            : `10-year minus 2-year Treasury yield, in basis points (1 bp = 0.01 percentage point). ${spread >= 0 ? "Positive: the curve is not inverted." : "Negative: the curve is inverted."}`}>
            <span className={styles.spreadBadge} data-tone={tone} tabIndex={0}>
              {spreadBps !== null ? `${spread >= 0 ? "+" : ""}${spreadBps} bps` : "—"}
            </span>
          </HintTip>
        )}
        {compact && onViewAll && <ViewAll onClick={onViewAll} />}
      </header>

      {!collapsed && (loading ? (
        <div className={styles.loadWrap}>
          <Skeleton w="100%" h="80px" />
          <div className={styles.chips}>
            <Skeleton w="80px" h="36px" />
            <Skeleton w="80px" h="36px" />
            <Skeleton w="80px" h="36px" />
          </div>
        </div>
      ) : showEmpty ? (
        <div className={styles.empty}>
          <span className={styles.emptyIcon}><Icon name="trending" size={24} /></span>
          <p className={styles.emptyTitle}>No yield curve data loaded yet</p>
          <button className={styles.emptyBtn} onClick={onRefresh} disabled={busy}>
            {busy ? "Refreshing…" : "Refresh now"}
          </button>
        </div>
      ) : (
        <>
          <div className={styles.chartWrap}>
            <ResponsiveContainer width="100%" height={80}>
              <LineChart data={chartData} margin={{ top: 6, right: 16, bottom: 6, left: 0 }}>
                <YAxis
                  width={42}
                  tickFormatter={(v) => `${v.toFixed(1)}%`}
                  tick={{ fontSize: 10, fill: "var(--text-faint)" }}
                  axisLine={false}
                  tickLine={false}
                />
                <ReferenceLine
                  y={0}
                  stroke="var(--negative)"
                  strokeDasharray="3 3"
                  strokeWidth={1}
                />
                <Line
                  type="monotone"
                  dataKey="spread"
                  dot={false}
                  strokeWidth={2}
                  stroke="var(--accent)"
                  isAnimationActive={false}
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  cursor={CURSOR}
                  formatter={(v) => [`${v != null ? v.toFixed(2) : "—"}%`, "Spread"]}
                  labelFormatter={(l) => l}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>

          {latest && (
            <div className={styles.chips}>
              <div className={styles.chip}>
                <span className={styles.chipLabel}>2yr</span>
                <span className={styles.chipValue}>{latest.yr2 != null ? `${latest.yr2.toFixed(2)}%` : "—"}</span>
              </div>
              <div className={styles.chip}>
                <span className={styles.chipLabel}>10yr</span>
                <span className={styles.chipValue}>{latest.yr10 != null ? `${latest.yr10.toFixed(2)}%` : "—"}</span>
              </div>
              <div className={styles.chip}>
                <span className={styles.chipLabel}>30yr</span>
                <span className={styles.chipValue}>{latest.yr30 != null ? `${latest.yr30.toFixed(2)}%` : "—"}</span>
              </div>
              <div className={styles.chip}>
                <span className={styles.chipLabel}>As of</span>
                <span className={styles.chipValue}>{latest.date}</span>
              </div>
            </div>
          )}
        </>
      ))}
    </section>
  );
}
