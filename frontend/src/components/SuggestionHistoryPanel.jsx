import { useEffect, useMemo, useRef, useState } from "react";
import { animate } from "animejs";
import { AnimatePresence, motion } from "motion/react";
import BacktestPanels from "./BacktestPanels";
import EmptyState from "./EmptyState";
import TickerLabel from "./TickerLabel";
import { getSuggestionHistory } from "../api";
import { openTickerTab } from "../lib/nav";
import { DAY_NAMES, monthGrid } from "../lib/calendarGrid";
import { outcomeTone, pctLabel } from "../lib/suggestionHistory";
import { prefersReducedMotion } from "../lib/motionConfig";
import styles from "./SuggestionHistoryPanel.module.css";
import Tooltip from "./Tooltip";

const MONTH_FMT = new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" });

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

export default function SuggestionHistoryPanel({ compact = false, ticker = null }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [cursor, setCursor] = useState(() => {
    const now = new Date();
    return { year: now.getFullYear(), month: now.getMonth() };
  });
  const [openDay, setOpenDay] = useState(null);

  useEffect(() => {
    let alive = true;
    getSuggestionHistory({ ticker, months: 12 })
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, [ticker]);

  // Memoized: a fresh [] each render would invalidate every downstream memo.
  const entries = useMemo(() => data?.entries || [], [data]);

  const byDay = useMemo(() => {
    const m = new Map();
    for (const e of entries) {
      if (!m.has(e.for_date)) m.set(e.for_date, []);
      m.get(e.for_date).push(e);
    }
    return m;
  }, [entries]);

  const stats = useMemo(() => {
    const scored = entries.filter((e) => e.outcomes.d7 != null);
    const up = scored.filter((e) => e.outcomes.d7 > 0).length;
    return { total: entries.length, scored: scored.length, up };
  }, [entries]);

  const cells = monthGrid(cursor.year, cursor.month);
  const monthLabel = MONTH_FMT.format(new Date(cursor.year, cursor.month, 1));
  const step = (delta) => setCursor((c) => {
    const d = new Date(c.year, c.month + delta, 1);
    return { year: d.getFullYear(), month: d.getMonth() };
  });

  const dayEntries = openDay ? byDay.get(openDay) || [] : [];

  if (error) {
    return (
      <section className={styles.panel} id="suggestion-history">
        <p className={styles.error}>Couldn&apos;t load suggestion history: {error}</p>
      </section>
    );
  }

  return (
    <>
    <section className={styles.panel} id="suggestion-history">
      {!compact && (
        <header className={styles.head}>
          <div>
            <h2 className={styles.title}>Suggestion history</h2>
            <p className={styles.subtitle}>
              What was suggested for your watched and held stocks, and what the
              stock did afterwards.
            </p>
          </div>
          {entries.length > 0 && (
            <div className={styles.tallies}>
              <span className={styles.tally}>
                <Tally value={stats.total} className={styles.tallyNum} />
                <span className={styles.tallyLabel}>recorded</span>
              </span>
              <span className={styles.tally}>
                <Tally value={stats.up} className={styles.tallyNum} />
                <span className={styles.tallyLabel}>up after 7d</span>
              </span>
              <span className={styles.tally}>
                <Tally value={stats.scored - stats.up} className={styles.tallyNum} />
                <span className={styles.tallyLabel}>down after 7d</span>
              </span>
            </div>
          )}
        </header>
      )}

      {data && entries.length === 0 ? (
        <EmptyState
          icon="calendar"
          title="No history yet"
          text={
            "Suggestion history starts accumulating from the day this shipped — "
            + "past suggestions weren't recorded, and inventing them would make the "
            + "outcomes meaningless. Check back after a few trading days."
          }
        />
      ) : (
        <>
          <div className={styles.monthBar}>
            <Tooltip content="Previous month"><button className={styles.navBtn} onClick={() => step(-1)} aria-label="Previous month">‹</button></Tooltip>
            <span className={styles.monthLabel}>{monthLabel}</span>
            <Tooltip content="Next month"><button className={styles.navBtn} onClick={() => step(1)} aria-label="Next month">›</button></Tooltip>
            <span className={styles.legend}>
              <span className={styles.dot} data-tone="up" /> up
              <span className={styles.dot} data-tone="down" /> down
              <span className={styles.dot} data-tone="flat" /> flat
              <span className={styles.dot} data-tone="pending" /> pending
            </span>
          </div>

          <div className={styles.grid} role="grid" aria-label={`Suggestions in ${monthLabel}`}>
            {DAY_NAMES.map((d) => (
              <span key={d} className={styles.dayName}>{d}</span>
            ))}
            {cells.map((cell, i) => {
              if (!cell) return <span key={`pad-${i}`} className={styles.pad} />;
              const dayRows = byDay.get(cell.iso) || [];
              const isOpen = openDay === cell.iso;
              return (
                <Tooltip key={cell.iso} disabled={dayRows.length === 0 || isOpen} content={(
                  <>
                    <strong>{cell.iso}</strong>
                    {dayRows.map((e) => (
                      <p key={e.ticker}>{e.ticker} · {e.action} · 7d {pctLabel(e.outcomes.d7)}</p>
                    ))}
                  </>
                )}>
                <button
                  className={styles.cell}
                  data-has={dayRows.length ? "yes" : "no"}
                  data-open={isOpen ? "yes" : "no"}
                  onClick={() => setOpenDay(isOpen ? null : cell.iso)}
                  disabled={dayRows.length === 0}
                  aria-label={`${cell.iso}: ${dayRows.length} suggestion${dayRows.length === 1 ? "" : "s"}`}
                >
                  <span className={styles.cellDay}>{cell.day}</span>
                  <span className={styles.dots}>
                    {dayRows.slice(0, 6).map((e) => (
                      <span
                        key={e.ticker}
                        className={styles.dot}
                        data-tone={outcomeTone(e.outcomes.d7)}
                      />
                    ))}
                    {dayRows.length > 6 && <span className={styles.more}>+{dayRows.length - 6}</span>}
                  </span>
                </button>
                </Tooltip>
              );
            })}
          </div>

          <AnimatePresence initial={false}>
            {openDay && dayEntries.length > 0 && (
              <motion.div
                className={styles.detail}
                initial={prefersReducedMotion() ? { opacity: 0 } : { opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={prefersReducedMotion() ? { opacity: 0 } : { opacity: 0, height: 0 }}
                transition={{ duration: prefersReducedMotion() ? 0 : 0.2 }}
              >
                <h3 className={styles.detailTitle}>{openDay}</h3>
                <ul className={styles.list}>
                  {dayEntries.map((e) => (
                    <li key={e.ticker} className={styles.row}>
                      <Tooltip content={`Open ${e.ticker} analysis in a new tab`}>
                        <button
                          className={styles.symbolBtn}
                          onClick={() => openTickerTab(e.ticker)}
                        >
                          <TickerLabel ticker={e.ticker} className={styles.symbol} />
                        </button>
                      </Tooltip>
                      <span className={styles.kind}>{e.kind}</span>
                      <span className={styles.action}>{e.action}</span>
                      <span className={styles.price}>
                        {e.price != null ? `@ ${e.price.toFixed(2)}` : "—"}
                      </span>
                      {["d7", "d30", "since"].map((k) => (
                        <span key={k} className={styles.outcome} data-tone={outcomeTone(e.outcomes[k])}>
                          <span className={styles.outcomeLabel}>
                            {k === "since" ? "to date" : k.replace("d", "+") + "d"}
                          </span>
                          {pctLabel(e.outcomes[k])}
                        </span>
                      ))}
                    </li>
                  ))}
                </ul>
              </motion.div>
            )}
          </AnimatePresence>
        </>
      )}
      </section>

      {/* "What happened next" per day is the calendar above; this is the same
          question asked in aggregate. */}
      {!compact && <BacktestPanels />}
    </>
  );
}
