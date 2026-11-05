import Tooltip from "./Tooltip";
import { useRiskStatus } from "../hooks/useRiskStatus";
import { navigateTo } from "../lib/nav";
import { REGIME_LABEL, REGIME_TONE, RISK_TAB_ROUTE } from "../lib/regime";
import styles from "./RegimeChip.module.css";

/**
 * The market regime in the top bar; opens the Trading Bot page's Risk tab,
 * where its inputs and the gate live. Renders nothing until the first load.
 */
export default function RegimeChip() {
  const { data } = useRiskStatus();

  const reg = data?.regime;
  if (!reg) return null;

  return (
    <Tooltip side="bottom" content={(
      <>
        <strong>Market regime: {REGIME_LABEL[reg.label]}</strong>
        <p>
          {reg.label === "unknown"
            ? `Not enough fresh inputs to call it${reg.note ? ` (${reg.note})` : ""} — it gates nothing.`
            : `Score ${reg.score > 0 ? "+" : ""}${reg.score} from ${reg.inputs.filter((i) => i.vote != null).length} inputs on ${reg.date}.`}
          {" "}Click for the inputs and the gate.
        </p>
      </>
    )}>
      <button type="button" className={styles.chip} data-tone={REGIME_TONE[reg.label]}
              onClick={() => navigateTo(RISK_TAB_ROUTE)}>
        <span className={styles.cap}>Regime</span>
        {REGIME_LABEL[reg.label]}
      </button>
    </Tooltip>
  );
}
