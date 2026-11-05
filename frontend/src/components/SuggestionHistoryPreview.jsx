import { useEffect, useMemo, useRef, useState } from "react";
import { animate } from "animejs";
import Icon from "./Icon";
import { getSuggestionHistory } from "../api";
import { groupByDay, recentDays } from "../lib/calendarGrid";
import { outcomeTone, pctLabel } from "../lib/suggestionHistory";
import { prefersReducedMotion } from "../lib/motionConfig";
import styles from "./SuggestionHistoryPreview.module.css";
import Tooltip from "./Tooltip";

const DAYS = 14;

/** A number that counts up to its value — animejs, reduced-motion aware. */
function Tally({ value, className }) {
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    if (prefersReducedMotion()) { el.textContent = String(value); return undefined; }
    const obj = { n: 0 };
    const anim = animate(obj, {
      n: value,
      duration: 700,
      ease: "outExpo",
      onUpdate: () => { el.textContent = String(Math.round(obj.n)); },
    });
    return () => anim.pause();
  }, [value]);
  return <span ref={ref} className={className}>{value}</span>;
}

/**
 * A fortnight of suggestion outcomes, for the Calendar view.
 *
 * Deliberately a horizontal strip rather than a month grid: the Calendar view
 * is already a date-organised list of macro events, and a second full calendar
 * above it would read as two competing calendars on one screen.
 */
export default function SuggestionHistoryPreview({ onOpenFull }) {
  const [entries, setEntries] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    getSuggestionHistory({ months: 1 })
      .then((d) => { if (alive) setEntries(d?.entries || []); })
      .catch((e) => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, []);

  const days = useMemo(() => recentDays(DAYS), []);
  const byDay = useMemo(() => groupByDay(entries || []), [entries]);

  const stats = useMemo(() => {
    const inWindow = (entries || []).filter((e) => days.includes(e.for_date));
    const scored = inWindow.filter((e) => e.outcomes?.d7 != null);
    return {
      total: inWindow.length,
      scored: scored.length,
      up: scored.filter((e) => e.outcomes.d7 > 0).length,
    };
  }, [entries, days]);

  if (error) return null;                 // the macro calendar below is the point
  if (entries && entries.length === 0) {
    return (
      <section className={styles.wrap}>
        <div className={styles.head}>
          <span className="caption">Suggestion history</span>
          <button type="button" className={styles.link} onClick={onOpenFull}>
            View full history <Icon name="arrowRight" size={12} />
          </button>
        </div>
        <p className={styles.empty}>
          Nothing recorded yet — history starts accruing from the first daily run,
          so this fills in as suggestions are made and their outcomes land.
        </p>
      </section>
    );
  }

  return (
    <section className={styles.wrap}>
      <div className={styles.head}>
        <span className="caption">Suggestion history · last {DAYS} days</span>
        <button type="button" className={styles.link} onClick={onOpenFull}>
          View full history <Icon name="arrowRight" size={12} />
        </button>
      </div>

      <div className={styles.stats}>
        <span className={styles.stat}>
          <Tally value={stats.total} className={styles.statNum} />
          <span className={styles.statLabel}>recorded</span>
        </span>
        <span className={styles.stat}>
          <Tally value={stats.up} className={styles.statNum} data-tone="up" />
          <span className={styles.statLabel}>up after 7d</span>
        </span>
        <span className={styles.stat}>
          <Tally value={stats.scored - stats.up} className={styles.statNum} data-tone="down" />
          <span className={styles.statLabel}>down after 7d</span>
        </span>
        {/* Say how many are still unscored rather than quietly excluding them. */}
        {stats.total > stats.scored && (
          <span className={styles.pending}>
            {stats.total - stats.scored} still within the 7-day window
          </span>
        )}
      </div>

      <ol className={styles.strip}>
        {days.map((iso) => {
          const dayEntries = byDay.get(iso) || [];
          const label = new Date(`${iso}T00:00:00`).getDate();
          return (
            <li key={iso} className={styles.day} data-empty={dayEntries.length ? "no" : "yes"}>
              <span className={styles.dayNum}>{label}</span>
              <span className={styles.dots}>
                {dayEntries.slice(0, 4).map((e, i) => (
                  <Tooltip key={i} content={`${e.ticker} · ${e.action || e.kind} · ${pctLabel(e.outcomes?.d7)} after 7d`}>
                    <span
                      className={styles.dot}
                      data-tone={outcomeTone(e.outcomes?.d7)}
                    />
                  </Tooltip>
                ))}
                {dayEntries.length > 4 && (
                  <span className={styles.more}>+{dayEntries.length - 4}</span>
                )}
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
