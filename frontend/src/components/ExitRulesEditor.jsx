import { useState } from "react";
import Button from "./Button";
import Field from "./Field";
import Segmented from "./Segmented";
import TimeMachineOdds from "./TimeMachineOdds";
import { formatMoney } from "../lib/format";
import styles from "./ExitRulesEditor.module.css";

const STOP_MODES = [
  { value: "price", label: "Price" },
  { value: "pct", label: "% below cost" },
];

const KIND_LABEL = { stop: "stop", take_profit: "take-profit", trailing: "trailing stop" };

const num = (v) => (v === "" || v == null ? null : Number(v));
const str = (v) => (v == null ? "" : String(+Number(v).toFixed(4)));
const pct = (v) => `${(v * 100).toFixed(1)}%`;

function stopFrom(mode, value, avgCost) {
  const v = num(value);
  if (v == null || !(v > 0)) return null;
  return mode === "price" ? v : avgCost * (1 - v / 100);
}

// One plain line per level: how far it sits from the price and, where the
// stop defines a risk (it's below your cost), what that is in R.
function readout({ price, avgCost, stop, takeProfit, trailing, trailHigh, currency }) {
  const m = (v) => formatMoney(v, currency);
  const risk = stop != null && avgCost > stop ? avgCost - stop : null;
  const lines = [];
  if (stop != null) {
    let line = `Stop ${m(stop)}`;
    if (price) line += ` — ${pct((price - stop) / price)} below ${m(price)}`;
    if (risk) {
      line += ` · 1R = ${m(risk)} a share`;
      if (price) line += ` · now ${((price - avgCost) / risk).toFixed(1)}R`;
    } else {
      line += ` · locks in ${pct(stop / avgCost - 1)} over your cost`;
    }
    lines.push(line);
  }
  if (takeProfit != null && takeProfit > 0) {
    let line = `Target ${m(takeProfit)}`;
    if (price) line += ` — ${pct(takeProfit / price - 1)} above the price`;
    if (risk) line += ` · ${((takeProfit - avgCost) / risk).toFixed(1)}R at the target`;
    lines.push(line);
  }
  if (trailing != null && trailing > 0) {
    const high = Math.max(trailHigh ?? price ?? 0, price ?? 0);
    if (high > 0) {
      lines.push(`Trails ${trailing}% under the high of ${m(high)} → stop now ${m(high * (1 - trailing / 100))}`);
    }
  }
  return lines;
}

/**
 * Edit one holding's exit rule: a stop (a price, or a % below your cost), a
 * take-profit and a trailing stop. Rules alert; they never sell. Shows each
 * level's distance from the price and in R, and offers the stop suggested by
 * the stored trade plan (never applied unless you pick it).
 *
 * Props: ticker, price (live, may be null), currency, avgCost, rule (the
 * stored rule or null), suggestedStop ({stop_price, reason} or null),
 * onSave(body) → Promise (rejects with ApiError.fields for field errors),
 * onDelete() (shown only when a rule exists), onCancel, compact (fields
 * only, no readout).
 */
export default function ExitRulesEditor({
  ticker, price, currency = "USD", avgCost, rule, suggestedStop,
  onSave, onDelete, onCancel, compact = false,
}) {
  const [mode, setMode] = useState(rule?.stop_pct != null ? "pct" : "price");
  const [stop, setStop] = useState(str(rule?.stop_pct ?? rule?.stop_price));
  const [takeProfit, setTakeProfit] = useState(str(rule?.take_profit));
  const [trailing, setTrailing] = useState(str(rule?.trailing_pct));
  const [active, setActive] = useState(rule?.active ?? true);
  const [errors, setErrors] = useState({});
  const [formError, setFormError] = useState(null);
  const [pending, setPending] = useState(false);

  const stopLevel = stopFrom(mode, stop, avgCost);
  const lines = compact ? [] : readout({
    price, avgCost, currency, stop: stopLevel, takeProfit: num(takeProfit),
    trailing: num(trailing), trailHigh: rule?.trail_high,
  });
  const fired = rule?.fired || [];
  const formId = `exit-rule-${ticker}`;

  function applySuggested() {
    setMode("price");
    setStop(str(suggestedStop.stop_price));
  }

  async function run(fn) {
    setPending(true);
    setErrors({});
    setFormError(null);
    try {
      await fn();
    } catch (err) {
      setErrors(err.fields || {});
      setFormError(err.fields ? null : err.message);
    } finally {
      setPending(false);
    }
  }

  const submit = (e) => {
    e.preventDefault();
    run(() => onSave({
      stop_price: mode === "price" ? num(stop) : null,
      stop_pct: mode === "pct" ? num(stop) : null,
      take_profit: num(takeProfit),
      trailing_pct: num(trailing),
      active,
    }));
  };

  return (
    <form id={formId} className={styles.form} data-compact={compact ? "yes" : "no"} onSubmit={submit}>
      <div className={styles.stopRow}>
        <Segmented options={STOP_MODES} value={mode} onChange={setMode} ariaLabel="Stop measured as" />
        <Field label="Stop" error={errors.stop_price || errors.stop_pct}
               suffix={mode === "price" ? currency : "%"}
               hint={mode === "pct" ? `below your average cost of ${formatMoney(avgCost, currency)}` : null}>
          {(a) => <input {...a} type="number" min="0" step="any" inputMode="decimal" value={stop}
                         placeholder="none" onChange={(e) => setStop(e.target.value)} />}
        </Field>
      </div>
      <div className={styles.row}>
        <Field label="Take-profit" error={errors.take_profit} suffix={currency}>
          {(a) => <input {...a} type="number" min="0" step="any" inputMode="decimal" value={takeProfit}
                         placeholder="none" onChange={(e) => setTakeProfit(e.target.value)} />}
        </Field>
        <Field label="Trailing stop (% under the high)" error={errors.trailing_pct} suffix="%">
          {(a) => <input {...a} type="number" min="0" step="any" inputMode="decimal" value={trailing}
                         placeholder="none" onChange={(e) => setTrailing(e.target.value)} />}
        </Field>
      </div>

      {suggestedStop && (
        <div className={styles.suggested}>
          <span>
            <strong>Suggested stop {formatMoney(suggestedStop.stop_price, currency)}</strong>
            <span className={styles.reason}> — {suggestedStop.reason}</span>
          </span>
          <Button size="sm" variant="quiet" onClick={applySuggested}>Use it</Button>
        </div>
      )}

      {lines.length > 0 && (
        <ul className={styles.readout} aria-live="polite">
          {lines.map((l) => <li key={l}>{l}</li>)}
        </ul>
      )}

      <label className={styles.check}>
        <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
        Watch these levels (untick to pause the rule without losing it)
      </label>
      {fired.length > 0 && (
        <p className={styles.note}>
          Already alerted: {fired.map((k) => KIND_LABEL[k] || k).join(", ")}. Saving re-arms every level.
        </p>
      )}
      {!compact && price != null && stopLevel != null && stopLevel < price && (
        <TimeMachineOdds ticker={ticker} entry={price} stop={stopLevel} target={num(takeProfit)} currency={currency} />
      )}
      {!compact && <p className={styles.note}>Rules alert you; they never sell anything.</p>}
      {formError && <p className={styles.error} role="alert">{formError}</p>}

      <div className={styles.actions}>
        {rule && onDelete && (
          <Button variant="danger" size="sm" disabled={pending} onClick={() => run(onDelete)}>Remove rule</Button>
        )}
        <span className={styles.spacer} />
        {onCancel && <Button variant="quiet" onClick={onCancel}>Cancel</Button>}
        <Button variant="primary" type="submit" disabled={pending}>
          {pending ? "Saving…" : rule ? "Save rule" : "Add rule"}
        </Button>
      </div>
    </form>
  );
}
