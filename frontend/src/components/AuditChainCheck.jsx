import { useState } from "react";
import Button from "./Button";
import { verifyAllAudit } from "../api";
import styles from "./AuditChainCheck.module.css";

/**
 * Admin: verify every user's audit hash chain at once and list any break.
 */
export default function AuditChainCheck() {
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [pending, setPending] = useState(false);

  async function run() {
    setPending(true);
    setError(null);
    try {
      setResult(await verifyAllAudit());
    } catch (e) {
      setError(e.message);
    } finally {
      setPending(false);
    }
  }

  const broken = result?.chains.filter((c) => !c.ok) || [];
  return (
    <div className={styles.root}>
      <Button size="sm" onClick={run} disabled={pending}>{pending ? "Verifying…" : "Verify every chain"}</Button>
      {error && <span className={styles.bad}>{error}</span>}
      {result && (broken.length === 0
        ? <span className={styles.ok}>{result.chains.length} chain(s) intact, {result.chains.reduce((s, c) => s + c.n, 0)} entries.</span>
        : broken.map((c) => (
          <span key={c.user_id} className={styles.bad}>User {c.user_id}: broken at entry #{c.broken_at} ({c.reason})</span>
        )))}
    </div>
  );
}
