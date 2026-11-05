import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import Icon from "./Icon";
import XPostCard from "./XPostCard";
import { sourceState, sourceNote } from "../lib/sources";
import { formatRelativeTime } from "../lib/format";
import { staggerContainer, staggerItem, prefersReducedMotion } from "../lib/motionConfig";
import styles from "./XPostsPanel.module.css";
import Tooltip from "./Tooltip";

/**
 * X (Twitter) watch feed. Renders stored posts from the monitored accounts with
 * clickable cashtags. When no official X API key is configured the source is
 * flagged as an unofficial mirror (its status is stamped as an error upstream),
 * which we surface as a caveat tag without hiding the real data.
 */
export default function XPostsPanel({ data = [], sources = [], loading, busy, onRefresh }) {
  const [account, setAccount] = useState("all");

  const status = sources.find((s) => s.source === "x_posts");
  const isDegraded = status && sourceState(status.status) === "error" && data.length > 0;
  const mirrorNote = status ? sourceNote(status.status) || status.status.replace(/^error:\s*/, "") : null;

  const accounts = useMemo(
    () => Array.from(new Set(data.map((p) => p.account))),
    [data],
  );
  // Always sort newest-first by parsed date so "All" reads chronologically even
  // if any stored rows predate the strict-ISO normalization (Task 19).
  const posts = useMemo(() => {
    const list = account === "all" ? data : data.filter((p) => p.account === account);
    return [...list].sort(
      (a, b) => (Date.parse(b.posted_at) || 0) - (Date.parse(a.posted_at) || 0),
    );
  }, [data, account]);
  const showEmpty = !loading && data.length === 0;

  return (
    <section className={styles.panel} id="x">
      <header className={styles.head}>
        <div>
          <h2 className={styles.title}>X Watch</h2>
          <p className={styles.subtitle}>
            Recent posts from monitored accounts, with detected cashtags. Signals, not predictions.
          </p>
          <p className={styles.cadence}>
            Checked hourly{status?.last_refreshed_at ? ` · updated ${formatRelativeTime(status.last_refreshed_at)}` : ""}
          </p>
        </div>
        <Tooltip content={busy ? "Refreshing X Watch…" : "Fetch the latest posts now"}>
          <button className={styles.refresh} onClick={onRefresh} disabled={busy}>
            <Icon name="refresh" size={15} /> {busy ? "…" : "Refresh"}
          </button>
        </Tooltip>
      </header>

      {isDegraded && (
        <Tooltip content={mirrorNote || null}>
          <p className={styles.degraded}>
            <Icon name="info" size={13} /> Unofficial mirror — set <code>STOCKS_X_BEARER</code> for the
            official X API. Data shown is real but its provenance is unverified.
          </p>
        </Tooltip>
      )}

      {accounts.length > 1 && (
        <div className={styles.tabs} role="tablist" aria-label="Filter by account">
          <button
            className={styles.tab} data-active={account === "all" ? "yes" : "no"}
            role="tab" aria-selected={account === "all"} onClick={() => setAccount("all")}
          >
            All
          </button>
          {accounts.map((a) => (
            <button
              key={a} className={styles.tab} data-active={account === a ? "yes" : "no"}
              role="tab" aria-selected={account === a} onClick={() => setAccount(a)}
            >
              @{a}
            </button>
          ))}
        </div>
      )}

      {showEmpty ? (
        <div className={styles.empty}>
          <span className={styles.emptyIcon}><Icon name="x" size={22} /></span>
          <p className={styles.emptyTitle}>No posts loaded yet</p>
          <button className={styles.emptyBtn} onClick={onRefresh} disabled={busy}>
            {busy ? "Refreshing…" : "Refresh now"}
          </button>
        </div>
      ) : (
        <motion.ul
          className={styles.feed}
          variants={staggerContainer}
          initial={prefersReducedMotion() ? false : "hidden"}
          animate="visible"
        >
          <AnimatePresence initial={false}>
            {posts.map((p) => (
              <motion.li
                key={`${p.account}:${p.post_id}`}
                variants={staggerItem}
                layout={!prefersReducedMotion()}
              >
                <XPostCard post={p} />
              </motion.li>
            ))}
          </AnimatePresence>
        </motion.ul>
      )}
    </section>
  );
}
