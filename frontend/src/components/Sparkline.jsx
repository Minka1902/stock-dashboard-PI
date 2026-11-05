import { Area, AreaChart } from "recharts";
import { motion } from "motion/react";
import { prefersReducedMotion } from "../lib/motionConfig";
import styles from "./Sparkline.module.css";
import Tooltip from "./Tooltip";

/**
 * Tiny inline sparkline for a table row. `closes` is a number[] (real closes,
 * never fabricated). Coloured green/red by the period change. Fades in when the
 * range changes (keyed on `range`), reduced-motion aware.
 */
export default function Sparkline({
  closes = [], changePct = null, error = false, loading = false, range = "1m",
  width = 92, height = 30,
}) {
  if (loading) return <span className={styles.dash} style={{ width }}>···</span>;
  if (error || !closes || closes.length < 2) {
    return (
      <Tooltip content={error ? "No price history available for this range" : "Not enough price history to draw a trend"}>
        <span className={styles.dash} style={{ width }}>—</span>
      </Tooltip>
    );
  }
  const up = changePct != null ? changePct >= 0 : closes[closes.length - 1] >= closes[0];
  const color = up ? "var(--positive)" : "var(--negative)";
  const data = closes.map((c, i) => ({ i, c }));
  return (
    <motion.div
      key={range}
      className={styles.spark}
      style={{ width, height }}
      initial={prefersReducedMotion() ? false : { opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: prefersReducedMotion() ? 0 : 0.25 }}
    >
      {/* Fixed-size: a ResponsiveContainer here measured -1×-1 on first paint
          and warned on every row. */}
      <AreaChart width={width} height={height} data={data} margin={{ top: 2, right: 0, bottom: 2, left: 0 }}>
        <Area
          type="monotone" dataKey="c" stroke={color} strokeWidth={1.5}
          fill={color} fillOpacity={0.12} dot={false} isAnimationActive={false}
        />
      </AreaChart>
    </motion.div>
  );
}
