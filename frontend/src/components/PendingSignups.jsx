import { useCallback, useEffect, useState } from "react";
import Button from "./Button";
import ConfirmDialog from "./ConfirmDialog";
import { approveSignup, getSignups, rejectSignup } from "../api";
import { formatRelativeTime } from "../lib/format";
import styles from "./PendingSignups.module.css";

const POLL_MS = 60_000;

/**
 * Admin: accounts waiting in the approval queue. Approve lets one in; reject
 * deletes it (after a confirm). `pollMs` sets how often the list refreshes.
 */
export default function PendingSignups({ pollMs = POLL_MS }) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [busyId, setBusyId] = useState(null);
  const [rejecting, setRejecting] = useState(null);

  const load = useCallback(() => {
    getSignups().then((r) => { setRows(r); setError(null); }).catch((e) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, pollMs);
    return () => clearInterval(timer);
  }, [load, pollMs]);

  const approve = async (id) => {
    setBusyId(id);
    try {
      await approveSignup(id);
      load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  };

  const reject = async () => {
    await rejectSignup(rejecting.id);
    load();
  };

  if (error) return <p className={styles.error}>{error}</p>;
  if (!rows) return <p className={styles.muted}>Loading…</p>;
  if (rows.length === 0) return <p className={styles.muted}>Nobody is waiting.</p>;

  return (
    <>
      <ul className={styles.list}>
        {rows.map((r) => (
          <li key={r.id} className={styles.row}>
            <span className={styles.email}>{r.email}</span>
            <span className={styles.meta}>
              signed up {formatRelativeTime(r.created_at)}
              {r.totp_enabled ? " · 2FA set up" : " · 2FA not set up yet"}
            </span>
            <span className={styles.actions}>
              <Button size="sm" variant="primary" disabled={busyId === r.id} onClick={() => approve(r.id)}>
                Approve
              </Button>
              <Button size="sm" variant="danger" disabled={busyId === r.id} onClick={() => setRejecting(r)}>
                Reject
              </Button>
            </span>
          </li>
        ))}
      </ul>
      <ConfirmDialog
        open={Boolean(rejecting)}
        tone="negative"
        caption="Approval queue"
        title={`Reject ${rejecting?.email ?? ""}?`}
        confirmLabel="Reject and delete"
        onConfirm={reject}
        onClose={() => setRejecting(null)}
      >
        The account is deleted. They can sign up again, and it would wait here again.
      </ConfirmDialog>
    </>
  );
}
