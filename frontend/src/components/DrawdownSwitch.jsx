import { useState } from "react";
import Button from "./Button";
import ConfirmDialog from "./ConfirmDialog";
import Field from "./Field";
import { acknowledgeTrip, setMaxDrawdown, useRiskStatus } from "../hooks/useRiskStatus";
import { formatRelativeTime } from "../lib/format";
import styles from "./DrawdownSwitch.module.css";

const STATE_LABEL = { off: "Off", armed: "Armed", tripped: "Tripped" };

function stateOf(dd) {
  if (!dd || dd.max_pct == null) return "off";
  return dd.tripped ? "tripped" : "armed";
}

/**
 * The drawdown kill switch: its limit, how far the portfolio is below its
 * high right now, and — once tripped — the acknowledge that re-enables new
 * entries. Reads the shared risk-status store, so the app banner updates too.
 */
export default function DrawdownSwitch() {
  const { data, error } = useRiskStatus();
  const [draft, setDraft] = useState(null);
  const [fieldError, setFieldError] = useState(null);
  const [pending, setPending] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const dd = data?.drawdown;
  const state = stateOf(dd);
  const value = draft ?? (dd?.max_pct != null ? String(dd.max_pct) : "");
  const [lo, hi] = dd?.range || [1, 90];

  async function save(pct) {
    setPending(true);
    setFieldError(null);
    try {
      await setMaxDrawdown(pct);
      setDraft(null);
    } catch (err) {
      setFieldError(err.fields?.max_drawdown_pct || err.message);
    } finally {
      setPending(false);
    }
  }

  const submit = (e) => {
    e.preventDefault();
    save(value === "" ? null : Number(value));
  };

  return (
    <section className={styles.card} data-state={state} aria-labelledby="dd-title">
      <header className={styles.head}>
        <h3 id="dd-title" className={styles.title}>Drawdown kill switch</h3>
        <span className={styles.pill} data-state={state}>{STATE_LABEL[state]}</span>
      </header>
      <p className={styles.text}>
        Pauses new-buy suggestions and new bot entries once your portfolio falls this far below
        its high (time-weighted, so deposits and withdrawals don't count). Exits keep running and
        nothing is ever sold for you.
      </p>
      {error && <p className={styles.error} role="alert">{error}</p>}

      {dd?.current ? (
        <p className={styles.now}>
          Now <strong>{dd.current.pct.toFixed(1)}%</strong> below the high of {dd.current.peak_date}
          <span className={styles.faint}> · as of {dd.current.as_of} · {dd.current.base_currency}</span>
        </p>
      ) : data && (
        <p className={styles.faint}>
          {dd?.acknowledged_at
            ? `Measuring again from ${dd.acknowledged_at.slice(0, 10)}, when you re-enabled — it needs two daily equity snapshots since then.`
            : "Not enough history yet — it needs two daily equity snapshots."}
        </p>
      )}

      {state === "tripped" && (
        <div className={styles.tripped} role="alert">
          <p>
            Tripped {formatRelativeTime(dd.tripped_at)} at {dd.tripped_pct.toFixed(1)}% below the high.
            New buys and bot entries are paused.
          </p>
          <Button variant="primary" size="sm" onClick={() => setConfirming(true)}>Acknowledge &amp; re-enable</Button>
        </div>
      )}

      <form className={styles.form} onSubmit={submit}>
        <Field label="Pause new entries at (% below the high)" suffix="%" error={fieldError}
               hint={`${lo}–${hi}%. Leave empty to turn it off.`}>
          {(a) => <input {...a} type="number" min={lo} max={hi} step="0.5" inputMode="decimal"
                         value={value} placeholder="off" onChange={(e) => setDraft(e.target.value)} />}
        </Field>
        <div className={styles.actions}>
          {dd?.max_pct != null && (
            <Button variant="quiet" size="sm" disabled={pending} onClick={() => save(null)}>Turn off</Button>
          )}
          <Button variant="primary" size="sm" type="submit" disabled={pending || draft == null}>
            {pending ? "Saving…" : "Save"}
          </Button>
        </div>
      </form>

      <ConfirmDialog
        open={confirming}
        caption="Kill switch"
        title="Re-enable new entries?"
        confirmLabel="Re-enable"
        onConfirm={acknowledgeTrip}
        onClose={() => setConfirming(false)}
      >
        <p>
          New-buy suggestions and bot entries start again. The high is reset to today, so the
          switch trips next only if the portfolio falls a further {dd?.max_pct}% from here.
        </p>
      </ConfirmDialog>
    </section>
  );
}
