// Helpers for the rule builder's spec (mirrors backend/app/rules.py RuleSpec).

export const OP_LABEL = {
  ">": "is above", "<": "is below", ">=": "is at or above", "<=": "is at or below",
  crosses_above: "crosses above", crosses_below: "crosses below",
};

export const blankCondition = () => ({
  left: { kind: "indicator", name: "sma", period: 20 },
  op: "crosses_above",
  right: { kind: "indicator", name: "sma", period: 50 },
});

export const blankRule = () => ({
  name: "My rule",
  entry: { mode: "all", conditions: [blankCondition()] },
  exit: null,
  stop_pct: 8,
  target_pct: null,
  trail_pct: null,
  size_pct: 100,
});

/** "SMA(20) crosses above SMA(50)" — a condition as one readable line. */
export function describeOperand(o, schema) {
  if (o.kind === "value") return String(o.value);
  const ind = schema?.indicators.find((i) => i.name === o.name);
  const label = ind?.label || o.name;
  return o.period ? `${label}(${o.period})` : label;
}
