import Button from "./Button";
import Icon from "./Icon";
import { useRiskStatus } from "../hooks/useRiskStatus";
import { navigateTo } from "../lib/nav";
import { RISK_TAB_ROUTE } from "../lib/regime";
import styles from "./RiskBanner.module.css";

/**
 * App-wide, persistent notice while a risk switch has paused new entries,
 * with the way to review it (the Trading Bot page's Risk tab). Renders
 * nothing while nothing is paused.
 */
export default function RiskBanner() {
  const { data } = useRiskStatus();

  if (!data?.halted) return null;
  const killed = Boolean(data.kill_switch?.halted_at);

  return (
    <div className={styles.banner} role="status">
      <Icon name="shield" size={16} />
      <p className={styles.text}>
        <strong>{killed ? "Trading halted." : "New entries paused."}</strong> {data.reasons.join(" · ")}.{" "}
        {killed
          ? "Every new order is refused and bots are paused until you re-enable trading. Nothing was sold."
          : "Sells and exits keep running."}
      </p>
      <Button size="sm" variant="primary"
              onClick={() => navigateTo(RISK_TAB_ROUTE)}>
        Review
      </Button>
    </div>
  );
}
