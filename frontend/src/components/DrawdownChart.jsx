import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AXIS_TICK, CHART_CURSOR, CHART_TOOLTIP } from "../lib/chartStyle";

/**
 * How far below its running high an account sat on each day (an "underwater"
 * chart). Props: series ([{ date, drawdown }] with drawdown in %, ≥ 0), height.
 */
export default function DrawdownChart({ series, height = 140 }) {
  const data = series.map((p) => ({ date: p.date, dd: -p.drawdown }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 6, right: 12, bottom: 4, left: 0 }}>
        <XAxis dataKey="date" tick={AXIS_TICK} axisLine={false} tickLine={false} minTickGap={40} />
        <YAxis width={48} tick={AXIS_TICK} axisLine={false} tickLine={false}
               tickFormatter={(v) => `${v.toFixed(0)}%`} domain={["dataMin", 0]} />
        <Tooltip contentStyle={CHART_TOOLTIP} cursor={CHART_CURSOR}
                 formatter={(v) => [`${Math.abs(v).toFixed(2)}% below the high`, "Drawdown"]} />
        <Area type="monotone" dataKey="dd" stroke="var(--negative)" fill="var(--negative-weak)"
              strokeWidth={1.5} isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}
