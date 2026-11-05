import { useState } from "react";
import Button from "./Button";
import KillSwitchDialog from "./KillSwitchDialog";
import ReleaseTradingDialog from "./ReleaseTradingDialog";
import { useRiskStatus } from "../hooks/useRiskStatus";
import { formatRelativeTime } from "../lib/format";
import styles from "./KillSwitchButton.module.css";

/**
 * The always-visible global kill switch: "Kill switch" while trading runs;
 * "Trading halted · Re-enable…" (with a TOTP step-up) once it has fired.
 */
export default function KillSwitchButton() {
  const { data } = useRiskStatus();
  const [confirming, setConfirming] = useState(false);
  const [releasing, setReleasing] = useState(false);

  const halted = data?.kill_switch?.halted_at;

  return (
    <div className={styles.root}>
      {halted ? (
        <>
          <span className={styles.halted} role="status">Trading halted {formatRelativeTime(halted)}</span>
          <Button size="sm" variant="primary" onClick={() => setReleasing(true)}>Re-enable…</Button>
        </>
      ) : (
        <Button size="sm" variant="danger" onClick={() => setConfirming(true)} disabled={!data}>Kill switch</Button>
      )}
      <KillSwitchDialog open={confirming} onClose={() => setConfirming(false)} />
      {releasing && <ReleaseTradingDialog onClose={() => setReleasing(false)} />}
    </div>
  );
}
