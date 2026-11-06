import { useEffect, useState } from "react";
import Button from "./Button";
import styles from "./PendingApproval.module.css";

const POLL_MS = 30_000;

/**
 * Waiting screen for an account in the approval queue. Re-checks every
 * `pollMs` and on demand through `onCheck` (which moves the app on once the
 * account is approved); `onLogout` signs out.
 */
export default function PendingApproval({ email, onCheck, onLogout, pollMs = POLL_MS }) {
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState("");

  const check = async () => {
    setChecking(true);
    setError("");
    try {
      await onCheck();
    } catch (e) {
      setError(e.message || "could not reach the server");
    } finally {
      setChecking(false);
    }
  };

  useEffect(() => {
    const timer = setInterval(() => { onCheck().catch(() => {}); }, pollMs);
    return () => clearInterval(timer);
  }, [onCheck, pollMs]);

  return (
    <div className={styles.root} role="status">
      <h2 className={styles.title}>Waiting for approval</h2>
      <p className={styles.text}>
        <strong>{email}</strong> is signed up and two-factor authentication is ready.
        The admin has been notified; this page opens the dashboard by itself once
        your account is approved.
      </p>
      {error && <p className={styles.error}>{error}</p>}
      <div className={styles.actions}>
        <Button variant="primary" onClick={check} disabled={checking}>
          {checking ? "Checking…" : "Check again"}
        </Button>
        <Button variant="quiet" onClick={onLogout}>Sign out</Button>
      </div>
    </div>
  );
}
