import Button from "./Button";
import SelectMenu from "./SelectMenu";
import { OP_LABEL } from "../lib/ruleSpec";
import styles from "./ConditionRow.module.css";

const OPS = Object.entries(OP_LABEL).map(([value, label]) => ({ value, label }));

function OperandPicker({ value, onChange, schema, allowValue, label }) {
  const options = [
    ...schema.indicators.map((i) => ({ value: i.name, label: i.label })),
    ...(allowValue ? [{ value: "__value", label: "A number" }] : []),
  ];
  const current = value.kind === "value" ? "__value" : value.name;
  const ind = schema.indicators.find((i) => i.name === value.name);

  function pick(name) {
    if (name === "__value") {
      onChange({ kind: "value", value: value.value ?? 0 });
      return;
    }
    const meta = schema.indicators.find((i) => i.name === name);
    onChange({ kind: "indicator", name, period: meta.period ?? null });
  }

  return (
    <span className={styles.operand}>
      <SelectMenu label={label} value={current} options={options} onChange={pick} className={styles.select} />
      {value.kind === "value" ? (
        <input className={styles.num} type="number" step="any" aria-label={`${label} value`} value={value.value ?? ""}
               onChange={(e) => onChange({ ...value, value: e.target.value === "" ? null : Number(e.target.value) })} />
      ) : ind?.bounds ? (
        <input className={styles.num} type="number" min={ind.bounds[0]} max={ind.bounds[1]} step="1"
               aria-label={`${label} period`} title="period (bars)" value={value.period ?? ""}
               onChange={(e) => onChange({ ...value, period: e.target.value === "" ? null : Number(e.target.value) })} />
      ) : null}
    </span>
  );
}

/**
 * One condition: indicator (with its period) · comparison · indicator or a
 * number. Props: value, onChange, onRemove (hidden when null), schema,
 * error (shown under the row).
 */
export default function ConditionRow({ value, onChange, onRemove, schema, error }) {
  return (
    <div className={styles.row} data-invalid={error ? "yes" : "no"}>
      <OperandPicker label="Left side" value={value.left} schema={schema}
                     onChange={(left) => onChange({ ...value, left })} />
      <SelectMenu label="Comparison" value={value.op} options={OPS} className={styles.select}
                  onChange={(op) => onChange({ ...value, op })} />
      <OperandPicker label="Right side" value={value.right} schema={schema} allowValue
                     onChange={(right) => onChange({ ...value, right })} />
      {onRemove && <Button size="sm" variant="quiet" onClick={onRemove} aria-label="Remove condition">×</Button>}
      {error && <span className={styles.error} role="alert">{error}</span>}
    </div>
  );
}
