import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import Icon from "./Icon";
import { alertIcon, alertTypeLabel } from "../lib/alertMeta";
import { formatRelativeTime } from "../lib/format";
import { prefersReducedMotion } from "../lib/motionConfig";
import styles from "./StockAlerts.module.css";
import Tooltip from "./Tooltip";

/**
 * The alerts fired for this ticker, with what each one is telling you.
 *
 * The bell only ever showed the newest eight across all tickers, so an alert
 * about the stock you were reading about was routinely invisible here.
 */
export default function StockAlerts({ alerts = [], ticker, focusKey = null }) {
  // Arriving from an alert click: expand its explanation straight away — the
  // whole point of following the link is to find out what it was telling you.
  const [openKey, setOpenKey] = useState(focusKey);
  const focusRef = useRef(null);

  useEffect(() => {
    if (!focusKey || !focusRef.current) return;
    focusRef.current.scrollIntoView({
      behavior: prefersReducedMotion() ? "auto" : "smooth",
      block: "center",
    });
  }, [focusKey, alerts]);

  if (alerts.length === 0) {
    return (
      <p className={styles.empty}>
        No alerts have fired for {ticker}. Alerts are recorded when something
        changes — a threshold crossing, a breakout, an insider cluster — so an
        empty list means nothing has tripped, not that nothing is being watched.
      </p>
    );
  }

  return (
    <ul className={styles.list}>
      {alerts.map((a) => {
        const open = openKey === a.dedup_key;
        const focused = focusKey === a.dedup_key;
        return (
          <li
            key={a.dedup_key}
            ref={focused ? focusRef : null}
            className={styles.item}
            data-read={a.read ? "yes" : "no"}
            data-focused={focused ? "yes" : undefined}
          >
            <Tooltip content={`${a.severity} severity`} side="left">
              <span className={styles.severity} data-sev={a.severity} role="img" aria-label={`${a.severity} severity`} />
            </Tooltip>
            <div className={styles.body}>
              <div className={styles.top}>
                <Icon name={alertIcon(a.type)} size={13} />
                <span className={styles.title}>{a.title}</span>
                <span className={styles.type}>{alertTypeLabel(a.type)}</span>
                <span className={styles.time}>{formatRelativeTime(a.created_at)}</span>
                {!a.read && <span className={styles.unread}>new</span>}
              </div>
              <p className={styles.message}>{a.message}</p>

              {a.explain && (
                <>
                  <button
                    type="button"
                    className={styles.explainBtn}
                    aria-expanded={open}
                    onClick={() => setOpenKey(open ? null : a.dedup_key)}
                  >
                    <Icon name="info" size={12} />
                    {open ? "Hide what this means" : "What this means"}
                  </button>
                  <AnimatePresence initial={false}>
                    {open && (
                      <motion.div
                        className={styles.explain}
                        initial={prefersReducedMotion() ? false : { height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={prefersReducedMotion() ? { opacity: 0 } : { height: 0, opacity: 0 }}
                        transition={prefersReducedMotion() ? { duration: 0 } : { duration: 0.2 }}
                      >
                        <p><span className={styles.explainLabel}>What</span> {a.explain.what}</p>
                        <p><span className={styles.explainLabel}>Why</span> {a.explain.why}</p>
                        <p><span className={styles.explainLabel}>Check</span> {a.explain.look_at}</p>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
