import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import Icon from "./Icon";
import Tooltip from "./Tooltip";
import { putServerSchedule, runScheduleNow } from "../api";
import { formatUntil } from "../lib/format";
import { prefersReducedMotion } from "../lib/motionConfig";
import styles from "./SchedulerSection.module.css";

const DAYS = [
  ["mon", "M", "Monday"], ["tue", "T", "Tuesday"], ["wed", "W", "Wednesday"],
  ["thu", "T", "Thursday"], ["fri", "F", "Friday"], ["sat", "S", "Saturday"],
  ["sun", "S", "Sunday"],
];
const UNIT_SECONDS = { m: 60, h: 3600, d: 86400 };
const UNIT_LABEL = { m: "min", h: "h", d: "d" };

const TIMEZONES = (() => {
  try {
    return Intl.supportedValuesOf("timeZone");
  } catch {
    return ["UTC", "Asia/Jerusalem", "America/New_York", "Europe/London"];
  }
})();

function unitFor(seconds) {
  if (seconds && seconds % 86400 === 0) return "d";
  if (seconds && seconds % 3600 === 0) return "h";
  return "m";
}

function humanSeconds(n) {
  if (!n) return "—";
  const u = unitFor(n);
  return `${n / UNIT_SECONDS[u]} ${UNIT_LABEL[u]}`;
}

/** "Every [3] [min|h|d]" — commits on Enter/blur or a unit click. */
function IntervalEditor({ seconds, onCommit, source }) {
  const [draft, setDraft] = useState(null);
  const unit = unitFor(seconds);
  const value = draft ?? String((seconds || 3600) / UNIT_SECONDS[unit]);

  const commit = (nextValue, nextUnit) => {
    setDraft(null);
    const n = Number(nextValue);
    // Empty/garbage just reverts; a real number — even one below the 1-minute
    // floor — goes to the server so its reason is shown, not swallowed.
    if (nextValue === "" || !Number.isFinite(n)) return;
    const secs = Math.round(n * UNIT_SECONDS[nextUnit]);
    if (secs !== seconds) onCommit(secs);
  };

  return (
    <span className={styles.interval}>
      <span className={styles.word}>every</span>
      <input
        type="number"
        min="1"
        step="1"
        className={styles.num}
        value={value}
        aria-label={`${source} interval`}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => { if (draft != null) commit(draft, unit); }}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit(value, unit);
          if (e.key === "Escape") setDraft(null);
        }}
      />
      <span className={styles.seg} role="group" aria-label={`${source} interval unit`}>
        {Object.keys(UNIT_SECONDS).map((u) => (
          <button
            key={u}
            type="button"
            aria-pressed={u === unit}
            className={styles.segBtn}
            onClick={() => commit(value, u)}
          >
            {UNIT_LABEL[u]}
          </button>
        ))}
      </span>
    </span>
  );
}

/** Wall-clock chips (HH:MM) with add/remove. Never lets the last one go. */
function TimesEditor({ times, onCommit, reduced, source }) {
  const [adding, setAdding] = useState("");
  const add = () => {
    if (!adding) return;
    if (!times.includes(adding)) onCommit([...times, adding].sort());
    setAdding("");
  };
  const chipMotion = reduced
    ? {}
    : { initial: { opacity: 0, scale: 0.85 }, animate: { opacity: 1, scale: 1 }, exit: { opacity: 0, scale: 0.85 } };
  return (
    <span className={styles.times}>
      <span className={styles.word}>at</span>
      <AnimatePresence initial={false}>
        {times.map((t) => (
          <motion.span key={t} layout={!reduced} className={styles.chip} {...chipMotion}>
            {t}
            <Tooltip content={times.length <= 1 ? `Can't remove ${t} — a schedule needs at least one time` : `Remove ${t}`}>
              <button
                type="button"
                className={styles.chipX}
                aria-label={`Remove ${t}`}
                disabled={times.length <= 1}
                onClick={() => onCommit(times.filter((x) => x !== t))}
              >
                ×
              </button>
            </Tooltip>
          </motion.span>
        ))}
      </AnimatePresence>
      <input
        type="time"
        className={styles.time}
        value={adding}
        aria-label={`${source}: add a time`}
        onChange={(e) => setAdding(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") add(); }}
      />
      <button type="button" className={styles.addBtn} onClick={add} disabled={!adding}>
        Add
      </button>
    </span>
  );
}

function TzEditor({ tz, onCommit, listId, source }) {
  const [draft, setDraft] = useState(null);
  const value = draft ?? tz;
  const commit = () => {
    const next = (draft ?? "").trim();
    setDraft(null);
    if (next && next !== tz) onCommit(next);
  };
  return (
    <input
      className={styles.tz}
      list={listId}
      value={value}
      aria-label={`${source} timezone`}
      spellCheck={false}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") setDraft(null);
      }}
    />
  );
}

function ScheduleRow({ row, error, note, onSave, onRunNow, reduced }) {
  const isTimes = row.mode === "times";
  const toggleDay = (d) => {
    const on = row.days.includes(d);
    if (on && row.days.length <= 1) return; // at least one day
    const days = on ? row.days.filter((x) => x !== d) : [...row.days, d];
    onSave({ days: DAYS.map(([k]) => k).filter((k) => days.includes(k)) });
  };

  return (
    <motion.li
      layout={!reduced}
      className={styles.row}
      data-enabled={row.enabled ? "yes" : "no"}
      transition={{ duration: reduced ? 0 : 0.2 }}
    >
      <div className={styles.name}>
        <span className={styles.src}>{row.source}</span>
        <Tooltip truncate>
          <span className={styles.desc}>
            {row.members?.length ? `${row.members.join(" → ")} · ` : ""}
            {row.description}
            {row.retry_seconds ? ` · retry ${humanSeconds(row.retry_seconds)}` : ""}
          </span>
        </Tooltip>
      </div>

      <span className={styles.seg} role="group" aria-label={`${row.source} schedule mode`}>
        <button
          type="button"
          className={styles.segBtn}
          aria-pressed={!isTimes}
          onClick={() => isTimes && onSave({ mode: "interval", interval_seconds: row.interval_seconds || 3600 })}
        >
          Every N
        </button>
        <button
          type="button"
          className={styles.segBtn}
          aria-pressed={isTimes}
          onClick={() => !isTimes && onSave({ mode: "times", times: row.times?.length ? row.times : ["06:00"] })}
        >
          At times
        </button>
      </span>

      <div className={styles.when}>
        {isTimes ? (
          <TimesEditor times={row.times} onCommit={(times) => onSave({ times })} reduced={reduced} source={row.source} />
        ) : (
          <IntervalEditor
            seconds={row.interval_seconds}
            source={row.source}
            onCommit={(interval_seconds) => onSave({ interval_seconds })}
          />
        )}
      </div>

      <span className={styles.days} role="group" aria-label={`${row.source} days`}>
        {DAYS.map(([key, letter, full]) => (
          <Tooltip key={key} content={`${full} — ${row.days.includes(key) ? "runs" : "skipped"}; click to toggle`}>
            <button
              type="button"
              className={styles.day}
              aria-pressed={row.days.includes(key)}
              aria-label={full}
              onClick={() => toggleDay(key)}
            >
              {letter}
            </button>
          </Tooltip>
        ))}
      </span>

      <TzEditor tz={row.tz} listId="scheduler-tz-list" source={row.source} onCommit={(tz) => onSave({ tz })} />

      <label className={styles.switch}>
        <input
          type="checkbox"
          checked={row.enabled}
          onChange={(e) => onSave({ enabled: e.target.checked })}
        />
        <span>{row.enabled ? "On" : "Paused"}</span>
      </label>

      <Tooltip disabled={!row.enabled || !row.next_run_at}
               content={row.next_run_at ? `Next run: ${new Date(row.next_run_at).toLocaleString()}` : null}>
        <span className={styles.next}>
          {!row.enabled ? "paused" : row.next_run_at ? formatUntil(row.next_run_at) : "—"}
        </span>
      </Tooltip>

      <button type="button" className={styles.runBtn} onClick={onRunNow}>
        <Icon name="refresh" size={11} /> {note || "Run now"}
      </button>

      <AnimatePresence>
        {error && (
          <motion.p
            role="alert"
            className={styles.err}
            initial={reduced ? false : { opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={reduced ? { opacity: 0 } : { opacity: 0, height: 0 }}
          >
            Not saved: {error}
          </motion.p>
        )}
      </AnimatePresence>
    </motion.li>
  );
}

/**
 * The scheduler: one row per source (plus the derived boom_score → alerts →
 * regime → automute → rules step). Edits apply optimistically and roll back with the server's reason
 * if they are rejected.
 */
export default function SchedulerSection({ schedules, setSchedules }) {
  const [errors, setErrors] = useState({});
  const [notes, setNotes] = useState({});
  const reduced = prefersReducedMotion();

  const save = async (source, patch) => {
    const prev = schedules.find((s) => s.source === source);
    setSchedules((list) => list.map((s) => (s.source === source ? { ...s, ...patch } : s)));
    setErrors((e) => ({ ...e, [source]: null }));
    try {
      const saved = await putServerSchedule(source, patch);
      setSchedules((list) => list.map((s) => (s.source === source ? saved : s)));
    } catch (err) {
      setSchedules((list) => list.map((s) => (s.source === source ? prev : s)));
      setErrors((e) => ({ ...e, [source]: err.message || "rejected" }));
    }
  };

  const runNow = async (source) => {
    setNotes((n) => ({ ...n, [source]: "Queuing…" }));
    try {
      await runScheduleNow(source);
      setNotes((n) => ({ ...n, [source]: "Queued" }));
    } catch (err) {
      setErrors((e) => ({ ...e, [source]: err.message || "run failed" }));
      setNotes((n) => ({ ...n, [source]: null }));
      return;
    }
    setTimeout(() => setNotes((n) => ({ ...n, [source]: null })), 4000);
  };

  return (
    <section className={styles.panel}>
      <div className={styles.head}>
        <span className="caption">Scheduler</span>
        <span className={styles.headRight}>
          each source on its own clock · one refresh thread · late runs queue, never drop
        </span>
      </div>
      <datalist id="scheduler-tz-list">
        {TIMEZONES.map((z) => <option key={z} value={z} />)}
      </datalist>
      {schedules.length === 0 ? (
        <p className={styles.muted}>No schedules yet — they are created when the server starts.</p>
      ) : (
        <ul className={styles.list}>
          {schedules.map((row) => (
            <ScheduleRow
              key={row.source}
              row={row}
              error={errors[row.source]}
              note={notes[row.source]}
              reduced={reduced}
              onSave={(patch) => save(row.source, patch)}
              onRunNow={() => runNow(row.source)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
