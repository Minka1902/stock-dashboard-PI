import { useState } from "react";
import Button from "./Button";
import Segmented from "./Segmented";
import { runTimeMachine } from "../api";
import { formatMoney } from "../lib/format";
import styles from "./TimeMachineOdds.module.css";

const HORIZONS = [5, 10, 20, 60].map((d) => ({ value: d, label: `${d}d`, title: `${d} trading days` }));
const pct = (v) => `${(v * 100).toFixed(1)}%`;

/**
 * The Time Machine for one plan: 10,000 seeded replays from the stock's own
 * past days give the odds of target first, stop first or neither within the
 * horizon, plus what the same levels really did from every past close. Runs
 * on demand; a result whose inputs have since changed is dropped. Props:
 * ticker, entry (null = the live price / last close), stop, target (optional),
 * currency, horizon (20), compact (no bands or history line).
 */
export default function TimeMachineOdds({ ticker, entry = null, stop, target = null, currency = "USD",
  horizon: initialHorizon = 20, compact = false }) {
  const [horizon, setHorizon] = useState(initialHorizon);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const key = JSON.stringify([ticker, entry, stop, target, horizon]);
  const current = result?.key === key ? result.data : null;
  const ready = ticker && stop > 0 && (entry == null || stop < entry);
  const m = (v) => formatMoney(v, currency);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const data = await runTimeMachine({ ticker, entry, stop, target, horizon_days: horizon });
      setResult({ key, data });
    } catch (e) {
      setError(Object.values(e.fields || {})[0] || e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={styles.root} aria-label="Time Machine">
      <div className={styles.head}>
        <span className={styles.title}>Time Machine</span>
        <Segmented options={HORIZONS} value={horizon} onChange={setHorizon} ariaLabel="Horizon" />
        <Button size="sm" variant="ghost" onClick={run} disabled={!ready || busy}>
          {busy ? "Replaying…" : current ? "Run again" : "Replay 10,000 times"}
        </Button>
      </div>
      {!ready && <p className={styles.faint}>Needs a stop below the entry.</p>}
      {error && <p className={styles.error} role="alert">{error}</p>}
      {current && (
        <div className={styles.result} aria-live="polite">
          <div className={styles.bar} role="img"
               aria-label={`target first ${pct(current.p_target)}, neither ${pct(current.p_neither)}, stop first ${pct(current.p_stop)}`}>
            <span data-kind="target" style={{ flexGrow: current.p_target }} />
            <span data-kind="neither" style={{ flexGrow: current.p_neither }} />
            <span data-kind="stop" style={{ flexGrow: current.p_stop }} />
          </div>
          <dl className={styles.odds}>
            {current.target != null && <div data-kind="target"><dt>Target first</dt><dd>{pct(current.p_target)}</dd></div>}
            <div data-kind="stop"><dt>Stop first</dt><dd>{pct(current.p_stop)}</dd></div>
            <div><dt>Neither in {current.horizon_days}d</dt><dd>{pct(current.p_neither)}</dd></div>
            <div><dt>Average</dt><dd>{current.avg_r >= 0 ? "+" : ""}{current.avg_r.toFixed(2)}R</dd></div>
          </dl>
          <p className={styles.verdict}>{current.verdict}</p>
          {current.notes.map((n) => <p key={n} className={styles.note}>{n}</p>)}
          {!compact && (
            <>
              {current.history.sessions > 0 && (
                <p className={styles.note}>
                  What really happened: from {current.history.sessions} past closes, the same stop and target hit the
                  target first {current.history.target}×, the stop first {current.history.stop}×, neither{" "}
                  {current.history.neither}× (average {current.history.avg_r >= 0 ? "+" : ""}{current.history.avg_r.toFixed(2)}R).
                </p>
              )}
              <p className={styles.note}>
                After {current.horizon_days} days: 5% of replays below {m(current.bands.at(-1).p5)}, median{" "}
                {m(current.bands.at(-1).p50)}, 95% below {m(current.bands.at(-1).p95)}.
              </p>
            </>
          )}
          <p className={styles.faint}>
            From {m(current.entry)} ({current.entry_source}) · {current.replays.toLocaleString()} replays of{" "}
            {current.pool.days} of {current.ticker}&apos;s days ({current.pool.from} – {current.pool.to}), seed{" "}
            {current.seed}. A day touching both counts as the stop; gaps fill at the open. A simulation from the
            past, not a prediction.
          </p>
        </div>
      )}
    </section>
  );
}
