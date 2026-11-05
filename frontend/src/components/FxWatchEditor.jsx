import { useEffect, useRef, useState } from "react";
import { AnimatePresence, Reorder, motion } from "motion/react";
import SelectMenu from "./SelectMenu";
import Icon from "./Icon";
import Tooltip from "./Tooltip";
import InfoTip from "./InfoTip";
import { getFxWatch, saveFxWatch } from "../api";
import { prefersReducedMotion } from "../lib/motionConfig";
import styles from "./FxWatchEditor.module.css";

// Mirrors main.py _MAX_FX_PAIRS.
const MAX_PAIRS = 12;
const SAVE_DEBOUNCE_MS = 500;
export const FX_WATCH_EVENT = "fxwatch:changed";

const pairLabel = (p) => `${p.slice(0, 3)}/${p.slice(3, 6)}`;

/**
 * The user's own currency pairs for the ticker tape: add (two ISO pickers),
 * remove, and reorder by drag (motion Reorder) or with the ↑/↓ buttons for
 * keyboard users. Changes save themselves shortly after the last edit and
 * nudge the live-quotes hook to refetch, so the tape updates at once.
 */
export default function FxWatchEditor() {
  const [pairs, setPairs] = useState([]);
  const [currencies, setCurrencies] = useState([]);
  const [from, setFrom] = useState("USD");
  const [to, setTo] = useState("ILS");
  const [state, setState] = useState("loading"); // loading | idle | saving | saved | error text
  const timer = useRef(null);
  const reduced = prefersReducedMotion();

  useEffect(() => {
    let alive = true;
    getFxWatch()
      .then((d) => {
        if (!alive) return;
        setPairs(d.pairs || []);
        setCurrencies(d.currencies || []);
        setState("idle");
      })
      .catch((err) => alive && setState(err.message || "could not load FX pairs"));
    return () => { alive = false; clearTimeout(timer.current); };
  }, []);

  function commit(next) {
    setPairs(next);
    setState("saving");
    clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      try {
        const saved = await saveFxWatch(next);
        setPairs(saved.pairs);
        setState("saved");
        window.dispatchEvent(new Event(FX_WATCH_EVENT));
      } catch (err) {
        setState(err.message || "could not save");
      }
    }, SAVE_DEBOUNCE_MS);
  }

  const candidate = `${from}${to}=X`;
  const addError = from === to
    ? "Pick two different currencies"
    : pairs.includes(candidate)
      ? `${pairLabel(candidate)} is already on the tape`
      : pairs.length >= MAX_PAIRS
        ? `At most ${MAX_PAIRS} pairs`
        : null;

  const move = (i, delta) => {
    const j = i + delta;
    if (j < 0 || j >= pairs.length) return;
    const next = [...pairs];
    [next[i], next[j]] = [next[j], next[i]];
    commit(next);
  };

  const options = currencies.map((c) => ({ value: c, label: c }));

  return (
    <fieldset className={styles.group} data-tour="fx-watch">
      <legend className={styles.legend}>
        Currencies in the ticker tape <InfoTip term="fx_pair" size={14} />
      </legend>
      <p className={styles.hint}>
        Exchange rates shown in the scrolling tape, in this order. Live Yahoo quotes, just like the
        stocks — a pair Yahoo can&apos;t price shows no quote rather than a made-up one. Drag to
        reorder, or use the arrows.
      </p>

      {state === "loading" ? (
        <p className={styles.muted}>Loading…</p>
      ) : pairs.length === 0 ? (
        <p className={styles.muted}>No currency pairs — the tape shows stocks only.</p>
      ) : (
        <Reorder.Group axis="y" values={pairs} onReorder={setPairs} className={styles.list}>
          <AnimatePresence initial={false}>
            {pairs.map((p, i) => (
              <Reorder.Item
                key={p}
                value={p}
                className={styles.item}
                onDragEnd={() => commit(pairs)}
                initial={reduced ? false : { opacity: 0, y: -6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={reduced ? { opacity: 0 } : { opacity: 0, x: -16 }}
                transition={reduced ? { duration: 0 } : undefined}
              >
                <span className={styles.grip} aria-hidden="true"><Icon name="grip" size={16} /></span>
                <span className={styles.pair}>{pairLabel(p)}</span>
                <span className={styles.symbol}>{p}</span>
                <span className={styles.actions}>
                  <Tooltip content={`Move ${pairLabel(p)} earlier`}>
                    <button type="button" className={styles.iconBtn} onClick={() => move(i, -1)}
                            disabled={i === 0} aria-label={`Move ${pairLabel(p)} up`}>↑</button>
                  </Tooltip>
                  <Tooltip content={`Move ${pairLabel(p)} later`}>
                    <button type="button" className={styles.iconBtn} onClick={() => move(i, 1)}
                            disabled={i === pairs.length - 1} aria-label={`Move ${pairLabel(p)} down`}>↓</button>
                  </Tooltip>
                  <Tooltip content={`Remove ${pairLabel(p)} from the tape`}>
                    <button type="button" className={styles.removeBtn}
                            onClick={() => commit(pairs.filter((x) => x !== p))}
                            aria-label={`Remove ${pairLabel(p)}`}>×</button>
                  </Tooltip>
                </span>
              </Reorder.Item>
            ))}
          </AnimatePresence>
        </Reorder.Group>
      )}

      {options.length > 0 && (
        <div className={styles.addRow}>
          <span className={styles.picker}>
            <SelectMenu label="From currency" value={from} options={options} onChange={setFrom} />
          </span>
          <span className={styles.slash} aria-hidden="true">/</span>
          <span className={styles.picker}>
            <SelectMenu label="To currency" value={to} options={options} onChange={setTo} />
          </span>
          <Tooltip content={addError || `Add ${pairLabel(candidate)} (Yahoo ${candidate})`}>
            <button type="button" className={styles.addBtn} disabled={Boolean(addError)}
                    onClick={() => commit([...pairs, candidate])}>
              Add pair
            </button>
          </Tooltip>
          <span className={styles.status} role="status">
            {state === "saving" && "Saving…"}
            {state === "saved" && <span className={styles.ok}>Saved ✓</span>}
            {!["loading", "idle", "saving", "saved"].includes(state) && <span className={styles.err}>{state}</span>}
          </span>
        </div>
      )}
      {addError && from !== to && <motion.p className={styles.muted} layout>{addError}</motion.p>}
    </fieldset>
  );
}
