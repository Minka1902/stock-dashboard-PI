import { useState } from "react";
import Segmented from "./Segmented";
import { useAutomute } from "../hooks/useAutomute";
import styles from "./MutedSignals.module.css";

const OVERRIDES = [
  { value: "auto", label: "Auto", title: "Follow the auto-mute" },
  { value: "on", label: "On", title: "Always count this signal" },
  { value: "off", label: "Off", title: "Never count this signal" },
];

function statusOf(i) {
  if (i.override === "on") return { tone: "on", text: i.auto_muted ? "Counting (you overrode the mute)" : "Counting (forced on)" };
  if (i.override === "off") return { tone: "off", text: "Not counting (you turned it off)" };
  if (i.auto_muted) return { tone: "muted", text: "Muted" };
  return { tone: "on", text: "Counting" };
}

/**
 * Each Boom Score signal's recent hit rate and whether it counts: auto-muted
 * when it stops working, with the reason, and your per-signal override.
 * Muted and measured signals first; never-measured ones fold away.
 */
export default function MutedSignals() {
  const { data, error, override } = useAutomute();
  const [busy, setBusy] = useState(null);
  const [actionError, setActionError] = useState(null);

  if (error) return <p className={styles.error} role="alert">{error}</p>;
  if (!data) return null;

  const r = data.rules;
  const measured = data.items.filter((i) => i.n > 0 || i.auto_muted || i.override !== "auto")
    .sort((a, b) => Number(b.auto_muted) - Number(a.auto_muted) || (a.hit_rate ?? 101) - (b.hit_rate ?? 101));
  const unmeasured = data.items.filter((i) => !measured.includes(i));

  async function change(component, mode) {
    setBusy(component);
    setActionError(null);
    try {
      await override(component, mode);
    } catch (e) {
      setActionError(e.message);
    } finally {
      setBusy(null);
    }
  }

  const row = (i) => {
    const st = statusOf(i);
    return (
      <li key={i.component} className={styles.row} data-busy={busy === i.component ? "yes" : "no"}>
        <span className={styles.name}>{i.label}<span className={styles.weight}>{i.weight > 0 ? "+" : ""}{i.weight}</span></span>
        <span className={styles.status} data-tone={st.tone}>{st.text}</span>
        <span className={styles.reason}>{i.reason}</span>
        <Segmented options={OVERRIDES} value={i.override} onChange={(m) => change(i.component, m)}
                   ariaLabel={`${i.label}: count it`} />
      </li>
    );
  };

  return (
    <section className={styles.card} aria-labelledby="automute-title">
      <header className={styles.head}>
        <h3 id="automute-title" className={styles.title}>Signal auto-mute</h3>
      </header>
      <p className={styles.text}>
        Each Boom Score signal is scored on its own firings over the last {r.window_days} days ({r.horizon_days}-day
        move, net of costs, in the signal's direction). Below {r.mute_below}% on at least {r.min_n} outcomes it stops
        counting; it counts again only at {r.unmute_at}%+. Your override applies to your suggestions and bots.
      </p>
      {actionError && <p className={styles.error} role="alert">{actionError}</p>}
      {measured.length > 0
        ? <ul className={styles.list}>{measured.map(row)}</ul>
        : <p className={styles.faint}>No signal has scored outcomes yet — they build up as the Boom Score runs.</p>}
      {unmeasured.length > 0 && (
        <details className={styles.more}>
          <summary>Not measured yet ({unmeasured.length})</summary>
          <ul className={styles.list}>{unmeasured.map(row)}</ul>
        </details>
      )}
    </section>
  );
}
