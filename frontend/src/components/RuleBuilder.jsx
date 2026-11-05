import Button from "./Button";
import ConditionRow from "./ConditionRow";
import Field from "./Field";
import Segmented from "./Segmented";
import { blankCondition } from "../lib/ruleSpec";
import styles from "./RuleBuilder.module.css";

const MODES = [
  { value: "all", label: "All of", title: "Every condition must hold" },
  { value: "any", label: "Any of", title: "At least one condition must hold" },
];

const RISK = [
  { key: "stop_pct", label: "Stop", suffix: "% below entry" },
  { key: "target_pct", label: "Target", suffix: "% above entry" },
  { key: "trail_pct", label: "Trailing stop", suffix: "% under the high" },
  { key: "size_pct", label: "Position size", suffix: "% of equity" },
];

function GroupEditor({ title, group, onChange, schema, errors, path, max }) {
  const update = (i, c) => onChange({ ...group, conditions: group.conditions.map((x, j) => (j === i ? c : x)) });
  return (
    <fieldset className={styles.group}>
      <legend className={styles.legend}>{title}</legend>
      <Segmented options={MODES} value={group.mode} onChange={(mode) => onChange({ ...group, mode })}
                 ariaLabel={`${title}: combine conditions`} />
      {group.conditions.map((c, i) => (
        <ConditionRow key={i} value={c} schema={schema} onChange={(v) => update(i, v)}
                      error={Object.entries(errors).find(([k]) => k.startsWith(`${path}.conditions.${i}`))?.[1]}
                      onRemove={group.conditions.length > 1
                        ? () => onChange({ ...group, conditions: group.conditions.filter((_, j) => j !== i) })
                        : null} />
      ))}
      <Button size="sm" variant="quiet" disabled={max}
              onClick={() => onChange({ ...group, conditions: [...group.conditions, blankCondition()] })}>
        + Condition
      </Button>
    </fieldset>
  );
}

/**
 * Builds a rule: entry conditions, optional exit conditions (each "all of" /
 * "any of" a list of indicator comparisons), and its stop, target, trailing
 * stop and size. Controlled: `value` is the RuleSpec, `onChange(spec)` gets
 * the new one. `errors` are the server's field errors keyed by spec path.
 * Props: value, onChange, schema (GET /api/rules/schema), errors.
 */
export default function RuleBuilder({ value, onChange, schema, errors = {} }) {
  const count = value.entry.conditions.length + (value.exit?.conditions.length || 0);
  const full = count >= schema.max_conditions;
  const num = (v) => (v === "" ? null : Number(v));

  return (
    <div className={styles.root}>
      <Field label="Rule name" error={errors.name}>
        {(a) => <input {...a} value={value.name} maxLength={60} onChange={(e) => onChange({ ...value, name: e.target.value })} />}
      </Field>
      <GroupEditor title="Enter when" group={value.entry} schema={schema} errors={errors} path="entry" max={full}
                   onChange={(entry) => onChange({ ...value, entry })} />
      {value.exit ? (
        <>
          <GroupEditor title="Exit when" group={value.exit} schema={schema} errors={errors} path="exit" max={full}
                       onChange={(exit) => onChange({ ...value, exit })} />
          <Button size="sm" variant="quiet" onClick={() => onChange({ ...value, exit: null })}>Remove exit conditions</Button>
        </>
      ) : (
        <Button size="sm" variant="quiet"
                onClick={() => onChange({ ...value, exit: { mode: "any", conditions: [blankCondition()] } })}>
          + Exit conditions
        </Button>
      )}
      <div className={styles.risk}>
        {RISK.map((r) => (
          <Field key={r.key} label={r.label} suffix={r.suffix} error={errors[r.key]}>
            {(a) => <input {...a} type="number" min="0" step="any" inputMode="decimal" value={value[r.key] ?? ""}
                           placeholder="none" onChange={(e) => onChange({ ...value, [r.key]: num(e.target.value) })} />}
          </Field>
        ))}
      </div>
      <p className={styles.note}>
        Signals are read on a bar's close and filled at the next open; stops and targets act intrabar. Only price
        and volume indicators: fundamentals, ratings and other snapshots aren't point-in-time.
      </p>
    </div>
  );
}
