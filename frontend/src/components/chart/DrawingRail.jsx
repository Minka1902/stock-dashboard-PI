import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { prefersReducedMotion } from "../../lib/motionConfig";
import { GROUPS, TOOLS, TOOL_BY_KEY } from "../../lib/drawings/tools";
import DrawIcon from "./DrawIcons";
import Tooltip from "../Tooltip";
import styles from "./ChartTools.module.css";

const RAIL_KEY = "chartRailTools";

function loadRemembered() {
  try {
    return JSON.parse(localStorage.getItem(RAIL_KEY) || "{}") || {};
  } catch {
    return {};
  }
}

/** Alt+<key> accelerator of a tool, in Tooltip's `shortcut` form. */
function toolKeys(t) {
  return t.shortcut ? ["Alt", t.shortcut] : undefined;
}

const flyoutMotion = (reduced) => ({
  initial: reduced ? { opacity: 0 } : { opacity: 0, x: -6, scale: 0.98 },
  animate: { opacity: 1, x: 0, scale: 1 },
  exit: reduced ? { opacity: 0 } : { opacity: 0, x: -6, scale: 0.98 },
  transition: { duration: reduced ? 0 : 0.16, ease: [0.22, 1, 0.36, 1] },
});

const listVariants = (reduced) => ({
  hidden: {},
  visible: { transition: reduced ? { staggerChildren: 0 } : { staggerChildren: 0.025 } },
});
const itemVariants = (reduced) => ({
  hidden: { opacity: 0, x: reduced ? 0 : -4 },
  visible: { opacity: 1, x: 0, transition: { duration: reduced ? 0 : 0.14 } },
});

/**
 * TradingView-style vertical tool rail: cursor, five tool groups (each shows
 * the last tool used from it; the chevron opens the full group), then the
 * global toggles — magnet, lock all, hide all — and undo / redo / clear.
 */
/** Rendered flyout width (min-width 212px + padding/border). */
const FLYOUT_WIDTH = 230;

export default function DrawingRail({ drawing, disabled }) {
  const [remembered, setRemembered] = useState(loadRemembered);
  const [openGroup, setOpenGroup] = useState(null);
  // "end" when a flyout opened from here would run past the right edge (the
  // rail is a horizontal row on phones).
  const [openAlign, setOpenAlign] = useState("start");
  const railRef = useRef(null);
  const reduced = prefersReducedMotion();

  useEffect(() => {
    if (!openGroup) return undefined;
    const onDown = (e) => { if (!railRef.current?.contains(e.target)) setOpenGroup(null); };
    const onKey = (e) => { if (e.key === "Escape") setOpenGroup(null); };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [openGroup]);

  // A tool picked by keyboard shortcut becomes its group's face too — derived
  // at render time rather than synced into state.
  const activeTool = drawing.tool ? TOOL_BY_KEY[drawing.tool] : null;
  const faceOf = (groupKey) => {
    if (activeTool?.group === groupKey) return activeTool;
    return TOOL_BY_KEY[remembered[groupKey]] || TOOLS.find((t) => t.group === groupKey);
  };

  const pick = (t) => {
    setRemembered((cur) => {
      const next = { ...cur, [t.group]: t.key };
      try { localStorage.setItem(RAIL_KEY, JSON.stringify(next)); } catch { /* private mode */ }
      return next;
    });
    setOpenGroup(null);
    if (drawing.tool !== t.key) drawing.setTool(t.key);
  };

  return (
    <div ref={railRef} className={styles.rail} role="toolbar" aria-orientation="vertical"
         aria-label="Drawing tools" data-disabled={disabled ? "yes" : "no"}>
      <Tooltip side="right" content="Cursor — select, move and pan" shortcut={["Esc"]}>
        <button type="button" className={styles.railBtn}
                data-active={!drawing.tool ? "yes" : "no"}
                aria-pressed={!drawing.tool}
                aria-label="Cursor"
                onClick={() => drawing.tool && drawing.setTool(drawing.tool)}>
          <DrawIcon name="cursor" />
        </button>
      </Tooltip>

      {GROUPS.map((g) => {
        const face = faceOf(g.key);
        const active = activeTool?.group === g.key;
        const open = openGroup === g.key;
        const items = TOOLS.filter((t) => t.group === g.key);
        return (
          <div key={g.key} className={styles.railGroup}>
            <Tooltip side="right" shortcut={toolKeys(face)}
                     content={disabled ? `${face.label} — available once the chart has data` : face.label}>
              <button type="button" className={styles.railBtn}
                      data-active={active ? "yes" : "no"} aria-pressed={active}
                      disabled={disabled}
                      aria-label={face.label}
                      onClick={() => drawing.setTool(face.key)}>
                <DrawIcon name={face.key} />
              </button>
            </Tooltip>
            {items.length > 1 && (
              <Tooltip side="right" disabled={open} content={`More ${g.label.toLowerCase()}`}>
                <button type="button" className={styles.railMore} disabled={disabled}
                        aria-haspopup="menu" aria-expanded={open}
                        aria-label={`More ${g.label.toLowerCase()}`}
                        onClick={(e) => {
                          const r = e.currentTarget.parentElement.getBoundingClientRect();
                          setOpenAlign(r.left + FLYOUT_WIDTH > window.innerWidth - 8 ? "end" : "start");
                          setOpenGroup(open ? null : g.key);
                        }}>
                  <DrawIcon name="chevron" size={9} />
                </button>
              </Tooltip>
            )}
            <AnimatePresence>
              {open && (
                <motion.div key="flyout" className={styles.flyout} role="menu" data-align={openAlign}
                            aria-label={g.label} {...flyoutMotion(reduced)}>
                  <div className={styles.flyoutTitle}>{g.label}</div>
                  <motion.ul className={styles.flyoutList} variants={listVariants(reduced)}
                             initial="hidden" animate="visible">
                    {items.map((t) => (
                      <motion.li key={t.key} variants={itemVariants(reduced)}>
                        <button type="button" role="menuitemradio" aria-checked={drawing.tool === t.key}
                                className={styles.flyoutItem}
                                data-active={drawing.tool === t.key ? "yes" : "no"}
                                onClick={() => pick(t)}>
                          <DrawIcon name={t.key} />
                          <span className={styles.flyoutLabel}>{t.label}</span>
                          {t.shortcut && <kbd className={styles.kbd}>Alt+{t.shortcut}</kbd>}
                        </button>
                      </motion.li>
                    ))}
                  </motion.ul>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        );
      })}

      <span className={styles.railSep} aria-hidden="true" />

      <Tooltip side="right" content={drawing.magnet
        ? "Magnet on — points snap to bar open/high/low/close"
        : "Magnet off — click to snap points to bar open/high/low/close"}>
        <button type="button" className={styles.railBtn} data-active={drawing.magnet ? "yes" : "no"}
                aria-pressed={drawing.magnet} disabled={disabled}
                aria-label="Magnet: snap to OHLC"
                onClick={() => drawing.setMagnet(!drawing.magnet)}>
          <DrawIcon name="magnet" />
        </button>
      </Tooltip>
      <Tooltip side="right" content={drawing.lockAll
        ? "Drawings locked — click to allow moving and editing"
        : "Lock all drawings (no moving or editing)"}>
        <button type="button" className={styles.railBtn} data-active={drawing.lockAll ? "yes" : "no"}
                aria-pressed={drawing.lockAll} disabled={disabled}
                aria-label="Lock all drawings"
                onClick={() => drawing.setLockAll(!drawing.lockAll)}>
          <DrawIcon name={drawing.lockAll ? "lock" : "unlock"} />
        </button>
      </Tooltip>
      <Tooltip side="right" content={drawing.hidden ? "Drawings hidden — click to show them" : "Hide all drawings"}>
        <button type="button" className={styles.railBtn} data-active={drawing.hidden ? "yes" : "no"}
                aria-pressed={drawing.hidden} disabled={disabled}
                aria-label="Hide all drawings"
                onClick={() => drawing.setHidden(!drawing.hidden)}>
          <DrawIcon name={drawing.hidden ? "eyeOff" : "eye"} />
        </button>
      </Tooltip>

      <span className={styles.railSep} aria-hidden="true" />

      <Tooltip side="right" shortcut={["Ctrl", "Z"]}
               content={drawing.canUndo ? "Undo" : "Undo — nothing to undo yet"}>
        <button type="button" className={styles.railBtn} disabled={disabled || !drawing.canUndo}
                aria-label="Undo" onClick={drawing.undo}>
          <DrawIcon name="undo" />
        </button>
      </Tooltip>
      <Tooltip side="right" shortcut={[["Ctrl", "Y"], ["Ctrl", "Shift", "Z"]]}
               content={drawing.canRedo ? "Redo" : "Redo — nothing to redo"}>
        <button type="button" className={styles.railBtn} disabled={disabled || !drawing.canRedo}
                aria-label="Redo" onClick={drawing.redo}>
          <DrawIcon name="redo" />
        </button>
      </Tooltip>
      <Tooltip side="right" content={drawing.shapes.length
        ? "Remove every drawing on this chart (undo with Ctrl+Z)"
        : "Remove all drawings — there are none on this chart"}>
        <button type="button" className={styles.railBtn}
                disabled={disabled || drawing.shapes.length === 0}
                aria-label="Remove all drawings"
                onClick={drawing.clearAll}>
          <DrawIcon name="trash" />
        </button>
      </Tooltip>
    </div>
  );
}
