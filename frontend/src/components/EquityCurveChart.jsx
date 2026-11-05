import { Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AXIS_TICK, CHART_CURSOR, CHART_TOOLTIP } from "../lib/chartStyle";

const pct = (v) => (v == null ? "—" : `${((v - 1) * 100).toFixed(1)}%`);

/**
 * Growth of 1 over time for an account (time-weighted, net of flows) against a
 * benchmark on the same days (left out when the series has none). Props:
 * series ([{ date, index, benchmark }]), label ("Portfolio"), benchmarkLabel
 * ("SPY"), height (220).
 */
export default function EquityCurveChart({ series, label = "Portfolio", benchmarkLabel = "Benchmark", height = 220 }) {
  const hasBenchmark = series.some((p) => p.benchmark != null);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={series} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
        <XAxis dataKey="date" tick={AXIS_TICK} axisLine={false} tickLine={false} minTickGap={40} />
        <YAxis width={48} tick={AXIS_TICK} axisLine={false} tickLine={false} domain={["auto", "auto"]}
               tickFormatter={pct} />
        <ReferenceLine y={1} stroke="var(--grid)" />
        <Tooltip contentStyle={CHART_TOOLTIP} cursor={CHART_CURSOR}
                 formatter={(v, name) => [pct(v), name]} />
        <Legend wrapperStyle={{ fontSize: 11, color: "var(--text-muted)" }} />
        <Line name={label} type="monotone" dataKey="index" stroke="var(--accent)" strokeWidth={2}
              dot={false} isAnimationActive={false} />
        {hasBenchmark && (
          <Line name={benchmarkLabel} type="monotone" dataKey="benchmark" stroke="var(--text-faint)"
                strokeWidth={1.5} strokeDasharray="4 3" dot={false} connectNulls isAnimationActive={false} />
        )}
      </LineChart>
    </ResponsiveContainer>
  );
}
