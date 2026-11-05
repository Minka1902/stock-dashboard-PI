import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid, Line, LineChart, ReferenceDot, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { getSuggestionHistory } from "../api";
import { TONE_TOKEN, outcomeTone, pctLabel } from "../lib/suggestionHistory";
import { useThemeColors } from "../lib/themeColors";
import styles from "./SuggestionHistoryStrip.module.css";

// SVG chart chrome for this strip, resolved per theme (see lib/themeColors).
const STRIP_TOKENS = {
  grid: "--grid",
  tick: "--text-faint",
  line: "--accent",
  ring: "--surface",
  cursor: "--border-strong", // recharts' hover cursor is #ccc unless told otherwise
};

// The tooltip is an HTML overlay, so it takes var() directly.
const TOOLTIP_STYLE = {
  background: "var(--surface)", border: "1px solid var(--border-strong)",
  borderRadius: 6, fontSize: 12, color: "var(--text)",
};

/**
 * The compact analysis-page variant: this ticker's past suggestions plotted
 * against its closes, so you can see what was said and what happened next.
 * Bars come from the daily series already loaded for the chart.
 */
export default function SuggestionHistoryStrip({ ticker, daily = [] }) {
  const [entries, setEntries] = useState(null);
  const c = useThemeColors(STRIP_TOKENS);
  const tone = useThemeColors(TONE_TOKEN);

  useEffect(() => {
    let alive = true;
    getSuggestionHistory({ ticker, months: 12 })
      .then((d) => { if (alive) setEntries(d.entries || []); })
      .catch(() => { if (alive) setEntries([]); });
    return () => { alive = false; };
  }, [ticker]);

  // Last ~120 sessions is enough context around a year of suggestions.
  const series = useMemo(
    () => (daily || []).slice(-120).map((b) => ({ date: b.date, close: b.close })),
    [daily],
  );
  const closeByDate = useMemo(
    () => new Map(series.map((p) => [p.date, p.close])),
    [series],
  );

  // Only mark suggestions that fall inside the plotted window; a suggestion
  // whose exact day has no bar (holiday) snaps to the next session.
  const marks = useMemo(() => {
    if (!entries) return [];
    const dates = series.map((p) => p.date);
    return entries
      .map((e) => {
        const at = closeByDate.has(e.for_date)
          ? e.for_date
          : dates.find((d) => d >= e.for_date);
        if (!at) return null;
        return { ...e, at, close: closeByDate.get(at) };
      })
      .filter(Boolean);
  }, [entries, series, closeByDate]);

  if (entries === null) return <p className={styles.muted}>Loading suggestion history…</p>;

  if (entries.length === 0) {
    return (
      <p className={styles.muted}>
        No suggestions recorded for {ticker} yet. History starts accumulating from
        the day this shipped — it isn&apos;t backfilled.
      </p>
    );
  }

  return (
    <div className={styles.wrap}>
      {series.length > 1 && (
        <div className={styles.chart}>
          <ResponsiveContainer width="100%" height={120}>
            <LineChart data={series} margin={{ top: 6, right: 6, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={c.grid} vertical={false} />
              {/* Hidden, but it is what maps ReferenceDot's x (a date) onto
                  the plot: without it the x scale is row indices and no
                  suggestion marker is ever drawn. */}
              <XAxis dataKey="date" hide />
              <YAxis
                domain={["dataMin", "dataMax"]}
                width={46}
                tick={{ fill: c.tick, fontSize: 10 }}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                cursor={{ stroke: c.cursor }}
                labelStyle={{ color: "var(--text-faint)" }}
                itemStyle={{ color: "var(--text)" }}
                formatter={(v) => [Number(v).toFixed(2), "close"]}
              />
              <Line
                type="monotone" dataKey="close" stroke={c.line} strokeWidth={1.6}
                dot={false} isAnimationActive={false}
              />
              {marks.map((m) => (
                <ReferenceDot
                  key={m.for_date}
                  x={m.at}
                  y={m.close}
                  r={4}
                  fill={tone[outcomeTone(m.outcomes.d7)]}
                  stroke={c.ring}
                  strokeWidth={1.5}
                  isFront
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      <ul className={styles.list}>
        {entries.slice(0, 8).map((e) => (
          <li key={e.for_date} className={styles.row}>
            <span className={styles.date}>{e.for_date}</span>
            <span className={styles.kind}>{e.kind}</span>
            <span className={styles.action}>{e.action}</span>
            <span className={styles.price}>
              {e.price != null ? `@ ${e.price.toFixed(2)}` : "—"}
            </span>
            {["d7", "d30", "since"].map((k) => (
              <span key={k} className={styles.outcome} data-tone={outcomeTone(e.outcomes[k])}>
                <span className={styles.outcomeLabel}>
                  {k === "since" ? "to date" : `+${k.replace("d", "")}d`}
                </span>
                {pctLabel(e.outcomes[k])}
              </span>
            ))}
          </li>
        ))}
      </ul>
    </div>
  );
}
