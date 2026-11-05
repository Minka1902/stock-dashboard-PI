import { useEffect, useState } from "react";
import { AUDIT_CSV_URL, getAudit } from "../api";
import { formatRelativeTime } from "../lib/format";
import styles from "./AuditTab.module.css";

const KIND_LABEL = {
  order_placed: "Order placed", order_filled: "Order filled", order_refused: "Order refused",
  order_rejected: "Order rejected", order_cancelled: "Order cancelled", kill_switch_on: "Kill switch on",
  kill_switch_off: "Kill switch off", step_up_failed: "Wrong code", risk_settings_changed: "Risk settings",
  exit_rule_saved: "Exit rule saved", exit_rule_deleted: "Exit rule removed", bot_created: "Bot created",
  bot_paused: "Bot paused", bot_resumed: "Bot resumed", bot_stopped: "Bot stopped", paper_reset: "Paper reset",
  drawdown_acknowledged: "Drawdown acknowledged",
};
const TONE = { order_refused: "neg", order_rejected: "neg", kill_switch_on: "neg", step_up_failed: "neg", order_filled: "pos" };

function summary(detail) {
  const d = JSON.parse(detail);
  return Object.entries(d)
    .filter(([, v]) => v != null && v !== "" && !(Array.isArray(v) && v.length === 0))
    .map(([k, v]) => `${k.replace(/_/g, " ")}: ${Array.isArray(v) ? v.join(", ") : v}`)
    .join(" · ");
}

/**
 * The Audit tab: your append-only trading log, newest first, with the hash
 * chain's integrity check and a CSV export of the full chain.
 */
export default function AuditTab() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    getAudit(300).then(setData).catch((e) => setError(e.message));
  }, []);

  if (error) return <p className={styles.error} role="alert">{error}</p>;
  if (!data) return <p className={styles.placeholder}>Loading the audit log…</p>;
  const v = data.verify;

  return (
    <div className={styles.root}>
      <div className={styles.bar}>
        <p className={styles.verify} data-ok={v.ok ? "yes" : "no"} role="status">
          {v.ok
            ? `Chain intact — ${v.n} entries, each hashed with the one before.`
            : `Chain broken at entry #${v.broken_at}: ${v.reason}. Something changed the history after it was written.`}
        </p>
        <a className={styles.csv} href={AUDIT_CSV_URL} download="audit-log.csv">Export CSV</a>
      </div>
      {data.entries.length === 0 ? (
        <p className={styles.placeholder}>Nothing recorded yet — orders, refusals and setting changes appear here.</p>
      ) : (
        <ol className={styles.list}>
          {data.entries.map((e) => (
            <li key={e.id} className={styles.row}>
              <span className={styles.when} title={e.at}>{formatRelativeTime(e.at)}</span>
              <span className={styles.kind} data-tone={TONE[e.kind] || "flat"}>{KIND_LABEL[e.kind] || e.kind}</span>
              <span className={styles.detail}>{summary(e.detail)}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
