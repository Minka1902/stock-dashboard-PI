import { useState } from "react";
import Segmented from "./Segmented";
import { setRegimeGate, useRiskStatus } from "../hooks/useRiskStatus";
import { GATE_OPTIONS, REGIME_LABEL, REGIME_TONE } from "../lib/regime";
import styles from "./RegimeCard.module.css";

const VOTE = { 1: "risk-on", 0: "neutral", [-1]: "risk-off" };

/**
 * The market regime with every input that voted (or why it couldn't), the
 * rules it was judged by, and the gate that decides what a risk-off regime
 * does to new buys. Reads the shared risk-status store.
 */
export default function RegimeCard() {
  const { data } = useRiskStatus();
  const [error, setError] = useState(null);

  const reg = data?.regime;
  if (!reg) return null;
  const rules = reg.rules;

  async function changeGate(gate) {
    setError(null);
    try {
      await setRegimeGate(gate);
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <section className={styles.card} aria-labelledby="regime-title">
      <header className={styles.head}>
        <h3 id="regime-title" className={styles.title}>Market regime</h3>
        <span className={styles.pill} data-tone={REGIME_TONE[reg.label]}>{REGIME_LABEL[reg.label]}</span>
        {reg.label !== "unknown" && (
          <span className={styles.faint}>score {reg.score > 0 ? "+" : ""}{reg.score} · {reg.date}</span>
        )}
      </header>
      <p className={styles.text}>
        A plain vote: each input that is available and fresh counts +1 (risk-on), −1 (risk-off) or 0.
        {` It needs ${rules.min_inputs} inputs to call it, ±${rules.score_threshold} sets the label, and an input older than ${rules.max_input_age_days} days doesn't vote.`}
        {reg.note ? ` Right now: ${reg.note}.` : ""}
      </p>

      {reg.inputs.length > 0 && (
        <ul className={styles.inputs}>
          {reg.inputs.map((i) => (
            <li key={i.key} data-vote={i.vote == null ? "na" : String(i.vote)}>
              <span className={styles.inputLabel}>{i.label}</span>
              <span className={styles.vote}>{i.vote == null ? "no vote" : VOTE[i.vote]}</span>
              <span className={styles.detail}>{i.detail}{i.as_of ? ` · ${String(i.as_of).slice(0, 10)}` : ""}</span>
            </li>
          ))}
        </ul>
      )}

      <div className={styles.gate}>
        <span className={styles.gateLabel}>In a risk-off regime, new buys are</span>
        <Segmented options={GATE_OPTIONS} value={data.regime_gate} onChange={changeGate} ariaLabel="Regime gate" />
      </div>
      <p className={styles.faint}>
        Sells, exits and holdings alerts are never touched. Bots hold new entries under Downgrade or Pause.
        An unknown regime gates nothing.
      </p>
      {data.downgrade && <p className={styles.active} role="status">Active now: {data.downgrade} — new buys show as watch.</p>}
      {error && <p className={styles.error} role="alert">{error}</p>}
    </section>
  );
}
