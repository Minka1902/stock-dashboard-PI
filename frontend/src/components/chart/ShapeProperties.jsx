import { AnimatePresence, motion } from "motion/react";
import { prefersReducedMotion } from "../../lib/motionConfig";
import {
  DASHES, DEFAULT_FILL, FILLABLE, PALETTE_KEYS, STROKED, TEXTUAL, TOOL_BY_KEY, WIDTHS,
} from "../../lib/drawings/tools";
import DrawIcon from "./DrawIcons";
import Tooltip from "../Tooltip";
import styles from "./ChartTools.module.css";

const PALETTE_LABELS = {
  stroke: "Default", up: "Positive", down: "Negative", info: "Info",
  compare: "Violet", muted: "Muted", text: "Text",
};
const DASH_LABELS = { solid: "Solid", dashed: "Dashed", dotted: "Dotted" };

/**
 * Floating style bar for the selected drawing: colour (theme palette keys, so
 * the shape follows the theme), line width, dash, fill opacity, text edit,
 * lock and delete. Every change is one undoable commit.
 */
export default function ShapeProperties({ drawing, palette }) {
  const shape = drawing.selected;
  const reduced = prefersReducedMotion();
  const locked = !!shape?.locked || drawing.lockAll;

  const set = (patch) => shape && drawing.updateShape(shape.id, patch);
  const activeKey = shape ? (shape.colorKey || (shape.color ? null : "stroke")) : null;
  // Why a style control is greyed out, appended to its tooltip.
  const lockedWhy = drawing.lockAll ? " — every drawing is locked from the rail" : " — unlock this drawing first";
  const tip = (label) => (locked ? `${label}${lockedWhy}` : label);

  return (
    <AnimatePresence>
      {shape && !drawing.hidden && (
        <motion.div
          key="props"
          className={styles.props}
          role="toolbar"
          aria-label={`${TOOL_BY_KEY[shape.kind]?.label || "Drawing"} style`}
          initial={reduced ? { opacity: 0 } : { opacity: 0, y: -8, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={reduced ? { opacity: 0 } : { opacity: 0, y: -8, scale: 0.98 }}
          transition={{ duration: reduced ? 0 : 0.16, ease: [0.22, 1, 0.36, 1] }}
          onPointerDown={(e) => e.stopPropagation()}
        >
          <span className={styles.propsKind}>{TOOL_BY_KEY[shape.kind]?.label || shape.kind}</span>

          {/* measure is always tinted by direction (up/down), so no colour pick */}
          {shape.kind !== "measure" && (
            <div className={styles.swatches} role="radiogroup" aria-label="Colour">
              {PALETTE_KEYS.map((k) => (
                <Tooltip key={k} side="bottom" content={tip(`${PALETTE_LABELS[k]} colour`)}>
                  <button type="button" role="radio" aria-checked={activeKey === k}
                          className={styles.swatch} data-active={activeKey === k ? "yes" : "no"}
                          style={{ "--sw": palette[k] }} disabled={locked}
                          aria-label={`${PALETTE_LABELS[k]} colour`}
                          onClick={() => set({ colorKey: k === "stroke" ? undefined : k, color: undefined })} />
                </Tooltip>
              ))}
            </div>
          )}

          {STROKED.has(shape.kind) && (
            <>
              <div className={styles.propsGroup} role="radiogroup" aria-label="Line width">
                {WIDTHS.map((w) => {
                  // legacy shapes have no width and draw at 1.8px, i.e. "2"
                  const on = Math.round(shape.width ?? 1.8) === w;
                  return (
                    <Tooltip key={w} side="bottom" content={tip(`Line width ${w}px`)}>
                      <button type="button" role="radio" aria-checked={on}
                              className={styles.propsBtn} data-active={on ? "yes" : "no"}
                              disabled={locked} aria-label={`Line width ${w}`}
                              onClick={() => set({ width: w })}>
                        <span className={styles.widthGlyph} style={{ height: w }} />
                      </button>
                    </Tooltip>
                  );
                })}
              </div>
              <div className={styles.propsGroup} role="radiogroup" aria-label="Line style">
                {Object.keys(DASHES).map((d) => {
                  const on = (shape.dash || "solid") === d;
                  return (
                    <Tooltip key={d} side="bottom" content={tip(`${DASH_LABELS[d]} line`)}>
                    <button type="button" role="radio" aria-checked={on}
                            className={styles.propsBtn} data-active={on ? "yes" : "no"}
                            disabled={locked} aria-label={`${DASH_LABELS[d]} line`}
                            onClick={() => set({ dash: d === "solid" ? undefined : d })}>
                      <svg viewBox="0 0 20 6" width="20" height="6" aria-hidden="true">
                        <line x1="1" y1="3" x2="19" y2="3" stroke="currentColor" strokeWidth="1.6"
                              strokeLinecap="round"
                              strokeDasharray={d === "dashed" ? "4 3" : d === "dotted" ? "0.5 3" : undefined} />
                      </svg>
                    </button>
                    </Tooltip>
                  );
                })}
              </div>
            </>
          )}

          {FILLABLE.has(shape.kind) && (
            <label className={styles.opacity}>
              <span>Fill</span>
              <input type="range" min="0" max="1" step="0.05" disabled={locked}
                     aria-label="Fill opacity"
                     value={shape.fillOpacity ?? DEFAULT_FILL[shape.kind] ?? 0.12}
                     onChange={(e) => set({ fillOpacity: Number(e.target.value) })} />
            </label>
          )}

          {TEXTUAL.has(shape.kind) && (
            <Tooltip side="bottom" content={tip("Edit text (or double-click the label)")}>
              <button type="button" className={styles.propsBtn} disabled={locked}
                      aria-label="Edit text"
                      onClick={() => drawing.startEdit(shape.id)}>
                <DrawIcon name="edit" size={15} />
              </button>
            </Tooltip>
          )}

          <Tooltip side="bottom" content={drawing.lockAll
            ? "Every drawing is locked from the rail — unlock there first"
            : shape.locked ? "Unlock this drawing" : "Lock this drawing in place"}>
            <button type="button" className={styles.propsBtn} data-active={shape.locked ? "yes" : "no"}
                    aria-pressed={!!shape.locked} disabled={drawing.lockAll}
                    aria-label={shape.locked ? "Unlock drawing" : "Lock drawing"}
                    onClick={() => set({ locked: shape.locked ? undefined : true })}>
              <DrawIcon name={shape.locked ? "lock" : "unlock"} size={15} />
            </button>
          </Tooltip>
          <Tooltip side="bottom" shortcut={locked ? undefined : ["Del"]}
                   content={locked ? `Delete drawing${lockedWhy}` : "Delete drawing"}>
            <button type="button" className={styles.propsBtn} data-tone="danger" disabled={locked}
                    aria-label="Delete drawing"
                    onClick={() => drawing.deleteShape(shape.id)}>
              <DrawIcon name="trash" size={15} />
            </button>
          </Tooltip>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
