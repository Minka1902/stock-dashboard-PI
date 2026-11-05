import { useState } from "react";
import ConfirmDialog from "./ConfirmDialog";
import Field from "./Field";
import { haltAll } from "../hooks/useRiskStatus";

/**
 * Confirm the global kill switch: every open paper order is cancelled, every
 * bot paused, and every new order refused until you re-enable trading with
 * an authenticator code. Nothing is sold. Props: open, onClose.
 */
export default function KillSwitchDialog({ open, onClose }) {
  const [reason, setReason] = useState("");
  return (
    <ConfirmDialog open={open} tone="negative" caption="Kill switch" title="Halt all trading?"
                   confirmLabel="Halt everything" onConfirm={() => haltAll(reason)} onClose={onClose}>
      <p>
        Open paper orders are cancelled and every bot is paused. Every new order — yours and the bots' — is
        refused until you re-enable trading with a code from your authenticator. Nothing is sold.
      </p>
      <Field label="Why (optional, kept in the audit log)">
        {(a) => <input {...a} value={reason} maxLength={200} onChange={(e) => setReason(e.target.value)} />}
      </Field>
    </ConfirmDialog>
  );
}
