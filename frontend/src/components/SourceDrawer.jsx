import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import Icon from "./Icon";
import { getServerSourceRuns, runScheduleNow } from "../api";
import { formatRelativeTime, formatUntil } from "../lib/format";
import { prefersReducedMotion } from "../lib/motionConfig";
import { STATE_TIP, sourceState } from "../lib/sources";
import Tooltip from "./Tooltip";
import styles from "./SourceDrawer.module.css";

const TONE = { ok: "pos", error: "neg", deferred: "caution" };

function ms(v) {
  if (v == null) return "—";
  return v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${Math.round(v)}ms`;
}

function stamp(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** One past run; its own traceback folds out when it has one. */
function RunRow({ run }) {
  const [open, setOpen] = useState(false);
  return (
    <li className={styles.run}>
      <div className={styles.runHead}>
        <span className={styles.outcome} data-outcome={run.outcome}>{run.outcome}</span>
        <Tooltip content={stamp(run.finished_at)}>
          <span className={styles.runWhen}>
            {formatRelativeTime(run.finished_at)}
          </span>
        </Tooltip>
        <span className={styles.runMeta}>{ms(run.duration_ms)} · {run.record_count} rec</span>
        {run.error_detail && (
          <button
            type="button"
            className={styles.linkBtn}
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
          >
            {open ? "Hide trace" : "Trace"}
          </button>
        )}
      </div>
      {run.detail && <p className={styles.runDetail}>{run.detail}</p>}
      {run.next_attempt_at && (
        <p className={styles.runNext}>next attempt {stamp(run.next_attempt_at)}</p>
      )}
      {open && <pre className={styles.trace}>{run.error_detail}</pre>}
    </li>
  );
}

/**
 * Slide-over with everything about one source: status, the full traceback,
 * its last 10 runs, Copy error and Retry now. Replaces the old Errors section,
 * which listed tracebacks far from the row they belonged to.
 */
export default function SourceDrawer({ source, onClose }) {
  // Keyed by source name so switching sources never flashes the previous
  // source's runs or retry state (and needs no reset-in-effect).
  const [runsState, setRunsState] = useState({ name: null, runs: null, error: null });
  const [copiedFor, setCopiedFor] = useState(null);
  const [retryState, setRetryState] = useState({ name: null, v: null });
  const closeRef = useRef(null);
  const returnFocus = useRef(null);
  const name = source?.source;
  const lastTry = source?.last_refreshed_at;
  const runs = runsState.name === name ? runsState.runs : null;
  const runsError = runsState.name === name ? runsState.error : null;
  const retry = retryState.name === name ? retryState.v : null; // "sending" | "queued" | error text
  const copied = copiedFor === name;
  const setRetry = (v) => setRetryState({ name, v });

  // Reload the run list when the drawer opens and whenever the source records
  // a new attempt (e.g. after Retry now), so the result shows up in place.
  useEffect(() => {
    if (!name) return undefined;
    let live = true;
    getServerSourceRuns(name, 10)
      .then((r) => { if (live) setRunsState({ name, runs: r, error: null }); })
      .catch((e) => { if (live) setRunsState({ name, runs: null, error: e.message }); });
    return () => { live = false; };
  }, [name, lastTry]);

  // Focus in on open, back to the trigger on close; Esc closes.
  useEffect(() => {
    if (!name) return undefined;
    returnFocus.current = document.activeElement;
    closeRef.current?.focus();
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (returnFocus.current?.focus) returnFocus.current.focus();
    };
  }, [name, onClose]);

  const reduced = prefersReducedMotion();
  const state = source?.status ? sourceState(source.status) : "never";
  const errorText = source?.error_detail || source?.status || "";
  const schedName = source?.schedule?.source || name;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(errorText);
      setCopiedFor(name);
      setTimeout(() => setCopiedFor(null), 1500);
    } catch { /* clipboard unavailable: the text is selectable below */ }
  };

  const retryNow = async () => {
    setRetry("sending");
    try {
      await runScheduleNow(schedName);
      setRetry("queued");
    } catch (e) {
      setRetry(e.message || "retry failed");
    }
  };

  return createPortal(
    <AnimatePresence>
      {source && (
        <div className={styles.layer} key="drawer">
          <motion.div
            className={styles.backdrop}
            onClick={onClose}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: reduced ? 0 : 0.18 }}
          />
          <motion.aside
            className={styles.drawer}
            role="dialog"
            aria-modal="true"
            aria-labelledby="source-drawer-title"
            initial={reduced ? { opacity: 0 } : { x: "100%" }}
            animate={reduced ? { opacity: 1 } : { x: 0 }}
            exit={reduced ? { opacity: 0 } : { x: "100%" }}
            transition={reduced ? { duration: 0 } : { type: "spring", stiffness: 420, damping: 40 }}
          >
            <header className={styles.head}>
              <div>
                <span className="caption">Source</span>
                <h2 id="source-drawer-title" className={styles.title}>{name}</h2>
              </div>
              <Tooltip side="left" content="Close" shortcut={["Esc"]}>
                <button
                  ref={closeRef}
                  type="button"
                  className={styles.close}
                  onClick={onClose}
                  aria-label="Close source details"
                >
                  <Icon name="x" size={14} />
                </button>
              </Tooltip>
            </header>

            <div className={styles.body}>
              <p className={styles.status} data-tone={TONE[state] || "faint"}>
                {source.never_run ? "never run" : source.status}
              </p>
              <p className={styles.stateNote}>{STATE_TIP[source.never_run ? "never" : state]}</p>

              <dl className={styles.facts}>
                <dt>Last success</dt><dd>{source.last_success_at ? formatRelativeTime(source.last_success_at) : "never"}</dd>
                <dt>Last attempt</dt><dd>{lastTry ? formatRelativeTime(lastTry) : "—"}</dd>
                <dt>Schedule</dt>
                <dd>
                  {source.schedule
                    ? `${source.schedule.description}${source.schedule.enabled ? "" : " (paused)"}${
                      source.schedule.source !== name ? ` · via ${source.schedule.source}` : ""}`
                    : "—"}
                </dd>
                <dt>Next run</dt>
                <dd>
                  {source.next_attempt_at
                    ? `retry ${formatUntil(source.next_attempt_at)}`
                    : source.next_run_at ? formatUntil(source.next_run_at) : "not scheduled"}
                </dd>
              </dl>

              <div className={styles.actions}>
                <Tooltip content={errorText ? "Copy the status and full error details" : "No error to copy — there is no error recorded for this source"}>
                  <button type="button" className={styles.btn} onClick={copy} disabled={!errorText}>
                    {copied ? "Copied" : "Copy error"}
                  </button>
                </Tooltip>
                <button
                  type="button"
                  className={styles.btnPrimary}
                  onClick={retryNow}
                  disabled={retry === "sending"}
                >
                  <Icon name="refresh" size={12} /> Retry now
                </button>
                <span className={styles.retryNote} aria-live="polite">
                  {retry === "queued" && "Queued on the refresh thread — results appear below."}
                  {retry && retry !== "queued" && retry !== "sending" && retry}
                </span>
              </div>

              {source.error_detail ? (
                <pre className={styles.trace}>{source.error_detail}</pre>
              ) : (
                state !== "ok" && !source.never_run && (
                  <p className={styles.muted}>No traceback for the latest run — the status line above is the whole reason.</p>
                )
              )}

              <h3 className={styles.sub}>Last 10 runs</h3>
              {runsError && <p className={styles.muted}>Couldn&apos;t load runs: {runsError}</p>}
              {runs && runs.length === 0 && <p className={styles.muted}>No runs recorded yet.</p>}
              {runs && runs.length > 0 && (
                <ul className={styles.runs}>
                  {runs.map((r) => <RunRow key={r.id} run={r} />)}
                </ul>
              )}
            </div>
          </motion.aside>
        </div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
