// Shared recharts styling for the Trading Bot page's charts: tokens, never
// hex, so both themes follow (recharts defaults its cursor to #ccc).

export const CHART_TOOLTIP = {
  background: "var(--surface-2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--r-sm)",
  fontSize: "12px",
  color: "var(--text)",
  padding: "6px 10px",
};

export const CHART_CURSOR = { stroke: "var(--border-strong)" };

export const AXIS_TICK = { fontSize: 10, fill: "var(--text-faint)" };
