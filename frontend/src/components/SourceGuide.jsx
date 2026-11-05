import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { formatRelativeTime, formatCount } from "../lib/format";
import { SOURCE_META, SOURCE_ORDER, STATE_TIP, sourceState, sourceNote } from "../lib/sources";
import Tooltip from "./Tooltip";
import { prefersReducedMotion } from "../lib/motionConfig";
import styles from "./SourceGuide.module.css";

// One row: static SOURCE_META joined to live /api/sources status. Errored rows
// gain a "details" disclosure with the full traceback (error_detail), the
// last-attempt time, the record count, and a copy button.
function SourceRow({ sourceKey, status }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const meta = SOURCE_META[sourceKey];
  const state = status ? sourceState(status.status) : "idle";
  const note = status ? sourceNote(status.status) : null;
  // Deferred sources get the same disclosure: the reason and next attempt.
  const isError = state === "error" || state === "deferred";
  const detail = status?.error_detail || status?.status || "";

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(detail);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard unavailable */ }
  };

  return (
    <li className={styles.item} data-state={state}>
      <div className={styles.row}>
        <Tooltip content={note ? `${STATE_TIP[state]} Note: ${note}` : STATE_TIP[state]}>
          <span className={styles.dot} role="img" aria-label={state} />
        </Tooltip>
        <span className={styles.text}>
          <span className={styles.name}>
            {meta.label}
            <span className={styles.provider}>{meta.provider}</span>
          </span>
          <span className={styles.blurb}>{meta.blurb}</span>
        </span>
        <span className={styles.meta}>
          {!status ? (
            "no data yet"
          ) : state === "ok" ? (
            <>{formatCount(status.record_count)} · {formatRelativeTime(status.last_refreshed_at)}</>
          ) : (
            <button
              type="button"
              className={styles.detailsBtn}
              onClick={() => setOpen((o) => !o)}
              aria-expanded={open}
            >
              <Tooltip truncate><span className={styles.err}>{status.status}</span></Tooltip>
              <span className={styles.chevron} data-open={open ? "yes" : "no"}>▾</span>
            </button>
          )}
        </span>
      </div>

      <AnimatePresence initial={false}>
        {isError && open && (
          <motion.div
            className={styles.details}
            initial={prefersReducedMotion() ? false : { height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={prefersReducedMotion() ? { opacity: 0 } : { height: 0, opacity: 0 }}
            transition={{ duration: prefersReducedMotion() ? 0 : 0.22, ease: "easeOut" }}
          >
            <div className={styles.detailsInner}>
              <div className={styles.detailsMeta}>
                <span>Last attempt: {formatRelativeTime(status.last_refreshed_at) || "—"}</span>
                <span>Records: {formatCount(status.record_count)}</span>
                <button type="button" className={styles.copyBtn} onClick={copy}>
                  {copied ? "Copied" : "Copy"}
                </button>
              </div>
              <pre className={styles.pre}>{detail}</pre>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </li>
  );
}

// The source directory: every external feed the dashboard pulls, its upstream
// provider, whether it last responded, and expandable diagnostics on failure.
export default function SourceGuide({ sources }) {
  const byName = new Map((sources || []).map((s) => [s.source, s]));
  return (
    <ul className={styles.list}>
      {SOURCE_ORDER.map((key) => (
        <SourceRow key={key} sourceKey={key} status={byName.get(key)} />
      ))}
    </ul>
  );
}
