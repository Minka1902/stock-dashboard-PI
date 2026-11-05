import { useState } from "react";
import Button from "./Button";
import Field from "./Field";
import styles from "./ScannerAlertsCard.module.css";

/**
 * Opt-in scanner alerts: off by default, one alert per ticker a day, at most
 * `per_hour` an hour, the rest in one digest an hour. Props: prefs
 * ({alerts_enabled, per_hour}), onSave(prefs) → saved prefs, maxPerHour (20).
 */
export default function ScannerAlertsCard({ prefs, onSave, maxPerHour = 20 }) {
  const [enabled, setEnabled] = useState(prefs.alerts_enabled);
  const [perHour, setPerHour] = useState(String(prefs.per_hour));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [saved, setSaved] = useState(false);

  const dirty = enabled !== prefs.alerts_enabled || Number(perHour) !== prefs.per_hour;

  async function save(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await onSave({ alerts_enabled: enabled, per_hour: Number(perHour) });
      setSaved(true);
    } catch (err) {
      setError(err.fields?.per_hour || err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className={styles.card} onSubmit={save} aria-labelledby="scanner-alerts-title">
      <h3 id="scanner-alerts-title" className={styles.title}>Scanner alerts</h3>
      <p className={styles.text}>
        Off by default. When on, each ticker alerts you at most once a day, and no more than the number
        below go out in an hour; anything over that waits for a single digest, at most one an hour.
      </p>
      <div className={styles.row}>
        <label className={styles.check}>
          <input type="checkbox" checked={enabled} onChange={(e) => { setEnabled(e.target.checked); setSaved(false); }} />
          Alert me about scanner matches
        </label>
        <Field label="At most" suffix="an hour" error={error} inline>
          {(a) => <input {...a} type="number" min={1} max={maxPerHour} step={1} size={4} value={perHour}
                         disabled={!enabled}
                         onChange={(e) => { setPerHour(e.target.value); setSaved(false); }} />}
        </Field>
        <Button type="submit" size="sm" variant="primary" disabled={busy || !dirty}>{busy ? "Saving…" : "Save"}</Button>
        {saved && !dirty && <span className={styles.ok} role="status">Saved.</span>}
      </div>
    </form>
  );
}
