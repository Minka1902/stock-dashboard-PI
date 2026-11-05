import { useState } from "react";
import Button from "./Button";
import Field from "./Field";
import { setGuardrails, useRiskStatus } from "../hooks/useRiskStatus";
import styles from "./GuardrailsCard.module.css";

const FIELDS = [
  { key: "max_order_notional", label: "Max per order", suffix: "base ccy", hint: "notional of one order" },
  { key: "max_notional_per_day", label: "Max per day", suffix: "base ccy", hint: "all orders placed today" },
  { key: "max_orders_per_day", label: "Max orders per day", suffix: "orders", hint: "yours and the bots'" },
  { key: "max_position_pct", label: "Max position", suffix: "% of account", hint: "after a buy" },
];
const PER_TRADE = [
  { key: "max_loss_per_trade", label: "Max loss per trade", suffix: "base ccy", hint: "at the stop; no stop = the whole position" },
  { key: "max_daily_loss", label: "Daily loss limit", suffix: "base ccy", hint: "today's entries, each at its stop" },
  { key: "min_reward_risk", label: "Min reward : risk", suffix: "R", hint: "needs a stop and a target" },
  { key: "no_entry_after_open_min", label: "No entries after the open", suffix: "min", hint: "skips the opening range" },
  { key: "no_entry_before_close_min", label: "No entries before the close", suffix: "min", hint: "half days follow the real close" },
];
const OPTIONAL = new Set(PER_TRADE.map((f) => f.key));

function toBody(draft) {
  return Object.fromEntries(Object.entries(draft).map(([k, v]) => [k,
    typeof v === "boolean" ? v : OPTIONAL.has(k) && v === "" ? null : Number(v)]));
}

/**
 * The order guardrails every entry passes — your paper tickets and every bot
 * fill — before it reaches the broker, plus the per-trade limits (each off
 * while empty). Selling what you hold is never blocked. Edits are checked
 * server-side; a refused order says which limit stopped it, in the bot log or
 * the ticket.
 */
export default function GuardrailsCard() {
  const { data } = useRiskStatus();
  const [draft, setDraft] = useState({});
  const [errors, setErrors] = useState({});
  const [pending, setPending] = useState(false);

  const g = data?.guardrails;
  if (!g) return null;
  const value = (k) => draft[k] ?? (g[k] == null ? "" : String(g[k]));
  const outside = draft.allow_outside_hours ?? g.allow_outside_hours;
  const requireStop = draft.require_stop ?? g.require_stop;
  const dirty = Object.keys(draft).length > 0;

  async function save(e) {
    e.preventDefault();
    setPending(true);
    setErrors({});
    try {
      await setGuardrails(toBody(draft));
      setDraft({});
    } catch (err) {
      setErrors(err.fields || { max_order_notional: err.message });
    } finally {
      setPending(false);
    }
  }

  return (
    <section className={styles.card} aria-labelledby="guard-title">
      <h3 id="guard-title" className={styles.title}>Order guardrails</h3>
      <p className={styles.text}>
        Checked on every new position before it's placed — your paper tickets and every bot fill. An order that
        breaks one is refused with the reason, and the refusal goes in the audit log. Selling what you hold is
        never blocked, not even by the kill switch.
      </p>
      <form className={styles.form} onSubmit={save}>
        <div className={styles.grid}>
          {FIELDS.map((f) => (
            <Field key={f.key} label={f.label} suffix={f.suffix} hint={f.hint} error={errors[f.key]}>
              {(a) => <input {...a} type="number" min="0" step="any" inputMode="decimal" value={value(f.key)}
                             onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })} />}
            </Field>
          ))}
        </div>
        <label className={styles.check}>
          <input type="checkbox" checked={outside}
                 onChange={(e) => setDraft({ ...draft, allow_outside_hours: e.target.checked })} />
          Allow placing orders while the market is closed (they rest until it opens)
        </label>
        <h4 className={styles.subtitle}>Per trade</h4>
        <div className={styles.grid}>
          {PER_TRADE.map((f) => (
            <Field key={f.key} label={f.label} suffix={f.suffix} hint={f.hint} error={errors[f.key]}>
              {(a) => <input {...a} type="number" min="0" step="any" inputMode="decimal" placeholder="off"
                             value={value(f.key)} onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })} />}
            </Field>
          ))}
        </div>
        <label className={styles.check}>
          <input type="checkbox" checked={requireStop}
                 onChange={(e) => setDraft({ ...draft, require_stop: e.target.checked })} />
          Require a stop on every new position
        </label>
        <div className={styles.actions}>
          {dirty && <Button variant="quiet" size="sm" onClick={() => { setDraft({}); setErrors({}); }}>Discard</Button>}
          <Button variant="primary" size="sm" type="submit" disabled={!dirty || pending}>
            {pending ? "Saving…" : "Save guardrails"}
          </Button>
        </div>
      </form>
    </section>
  );
}
