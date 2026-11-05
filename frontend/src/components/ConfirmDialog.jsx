import { useState } from "react";
import Button from "./Button";
import Dialog from "./Dialog";
import styles from "./ConfirmDialog.module.css";

/**
 * "Are you sure?" built on Dialog. `onConfirm` may be async; its error is
 * shown in place and the dialog stays open. `tone` "negative" marks a
 * destructive action (red rule and button).
 */
export default function ConfirmDialog({
  open, title, caption, children, confirmLabel = "Confirm", tone, onConfirm, onClose,
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);

  const close = () => {
    setError(null);
    onClose();
  };
  async function confirm() {
    setPending(true);
    try {
      await onConfirm();
      close();
    } catch (e) {
      setError(e.message);
    } finally {
      setPending(false);
    }
  }

  return (
    <Dialog open={open} size="sm" title={title} caption={caption} tone={tone} onClose={close}
            footer={(
              <>
                <Button variant="quiet" onClick={close}>Cancel</Button>
                <Button variant={tone === "negative" ? "danger" : "primary"} onClick={confirm}
                        disabled={pending}>
                  {pending ? "Working…" : confirmLabel}
                </Button>
              </>
            )}>
      <div className={styles.body}>{children}</div>
      {error && <p className={styles.error} role="alert">{error}</p>}
    </Dialog>
  );
}
