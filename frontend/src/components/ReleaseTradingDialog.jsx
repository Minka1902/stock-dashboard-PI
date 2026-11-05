import { useState } from "react";
import Button from "./Button";
import Dialog from "./Dialog";
import Field from "./Field";
import { releaseAll } from "../hooks/useRiskStatus";

/**
 * Re-enable trading after the kill switch, with a fresh 6-digit code from
 * the authenticator (a step-up: the same code can't be used twice). Bots stay
 * paused; resume each one yourself. Props: onClose.
 */
export default function ReleaseTradingDialog({ onClose }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      await releaseAll(code);
      onClose();
    } catch (err) {
      setError(err.fields?.code || err.message);
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog open size="sm" tone="caution" caption="Kill switch" title="Re-enable trading" onClose={onClose}
            footer={(
              <>
                <Button variant="quiet" onClick={onClose}>Cancel</Button>
                <Button variant="primary" type="submit" form="release-trading" disabled={pending || code.length !== 6}>
                  {pending ? "Checking…" : "Re-enable"}
                </Button>
              </>
            )}>
      <form id="release-trading" onSubmit={submit}>
        <Field label="Authenticator code" error={error}
               hint="Orders are accepted again. Paused bots stay paused until you resume them.">
          {(a) => <input {...a} inputMode="numeric" autoComplete="one-time-code" maxLength={6} value={code}
                         onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />}
        </Field>
      </form>
    </Dialog>
  );
}
