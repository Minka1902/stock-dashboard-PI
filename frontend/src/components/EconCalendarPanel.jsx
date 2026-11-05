import { useMemo, useState } from "react";
import Icon from "./Icon";
import Skeleton from "./Skeleton";
import SuggestionHistoryPreview from "./SuggestionHistoryPreview";
import { formatDate } from "../lib/format";
import styles from "./EconCalendarPanel.module.css";
import Tooltip from "./Tooltip";
import ScrollHint from "./ScrollHint";

const IMPORTANCE_LABEL = { high: "High impact", medium: "Medium impact", low: "Low impact" };

function SkeletonRows({ rows = 8 }) {
  return Array.from({ length: rows }).map((_, i) => (
    <tr key={i}>
      <td><Skeleton w="48px" /></td>
      <td><Skeleton w="14px" /></td>
      <td><Skeleton w="200px" /></td>
      <td><Skeleton w="56px" /></td>
      <td><Skeleton w="56px" /></td>
      <td><Skeleton w="56px" /></td>
    </tr>
  ));
}

// Group events into ordered [dateLabel, rows[]] buckets, preserving array order
// (the API already sorts by date then time).
function groupByDate(events) {
  const groups = [];
  const index = new Map();
  for (const ev of events) {
    if (!index.has(ev.date)) {
      index.set(ev.date, groups.length);
      groups.push([ev.date, []]);
    }
    groups[index.get(ev.date)][1].push(ev);
  }
  return groups;
}

export default function EconCalendarPanel({ data = [], loading, busy, onRefresh, onNavigate }) {
  const [highOnly, setHighOnly] = useState(false);

  const filtered = useMemo(
    () => (highOnly ? data.filter((e) => e.importance === "high") : data),
    [data, highOnly],
  );
  const groups = useMemo(() => groupByDate(filtered), [filtered]);

  // Impact ratings are uniform per fetch: official from FMP, or our curated read.
  const curated = data.length > 0 && data.every((e) => e.importance_source === "curated");
  const showEmpty = !loading && filtered.length === 0;

  return (
    <>
    {/* What we suggested and how it turned out, above the macro schedule: the
        calendar answers "what's coming", this answers "how did the last two
        weeks of calls actually go". */}
    <SuggestionHistoryPreview onOpenFull={() => onNavigate?.("suggestion-history")} />

    <section className={styles.panel} id="econ-calendar">
      <header className={styles.head}>
        <div>
          <h2 className={styles.title}>Economic Calendar</h2>
          <p className={styles.subtitle}>
            Upcoming macro releases · CPI, FOMC, payrolls &amp; more ·{" "}
            {curated
              ? "impact estimated by SIGNAL from the event name"
              : "official impact ratings"}
          </p>
        </div>
        <div className={styles.controls}>
          <label className={styles.toggle}>
            <input
              type="checkbox"
              checked={highOnly}
              onChange={(e) => setHighOnly(e.target.checked)}
            />
            High impact only
          </label>
        </div>
      </header>

      {showEmpty ? (
        <div className={styles.empty}>
          <span className={styles.emptyIcon}><Icon name="calendar" size={24} /></span>
          <p className={styles.emptyTitle}>
            {highOnly && data.length > 0
              ? "No high-impact events in this window"
              : "No economic events loaded yet"}
          </p>
          <button className={styles.emptyBtn} onClick={onRefresh} disabled={busy}>
            {busy ? "Refreshing…" : "Refresh now"}
          </button>
        </div>
      ) : (
        <ScrollHint className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Time</th>
                <th aria-label="Impact"></th>
                <th>Event</th>
                <th className={styles.num}>Actual</th>
                <th className={styles.num}>Forecast</th>
                <th className={styles.num}>Previous</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <SkeletonRows />
              ) : (
                groups.map(([date, rows]) => (
                  <DateGroup key={date} date={date} rows={rows} />
                ))
              )}
            </tbody>
          </table>
        </ScrollHint>
      )}
    </section>
    </>
  );
}

function DateGroup({ date, rows }) {
  return (
    <>
      <tr className={styles.dateRow}>
        <td colSpan={6}>{formatDate(date)}</td>
      </tr>
      {rows.map((ev) => (
        <tr key={ev.event_id}>
          <td className={styles.time}>{ev.time || "—"}</td>
          <td>
            <Tooltip content={IMPORTANCE_LABEL[ev.importance] || ev.importance}>
              <span
                className={styles.dot}
                data-impact={ev.importance}
                role="img"
                aria-label={IMPORTANCE_LABEL[ev.importance] || ev.importance}
              />
            </Tooltip>
          </td>
          <td className={styles.event}>
            <Tooltip truncate><span className={styles.eventName}>{ev.event}</span></Tooltip>
            {ev.country && <span className={styles.country}>{ev.country}</span>}
          </td>
          <td className={`${styles.num} ${ev.actual ? styles.actual : ""}`}>
            {ev.actual || "—"}
          </td>
          <td className={styles.num}>{ev.forecast || "—"}</td>
          <td className={`${styles.num} ${styles.muted}`}>{ev.previous || "—"}</td>
        </tr>
      ))}
    </>
  );
}
