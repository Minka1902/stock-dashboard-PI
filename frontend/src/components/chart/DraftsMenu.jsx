import { useCallback, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import Popover from "../Popover";
import Tooltip from "../Tooltip";
import {
  createDrawingDraft, deleteDrawingDraft, listDrawingDrafts, updateDrawingDraft,
} from "../../api";
import { prefersReducedMotion } from "../../lib/motionConfig";
import DrawIcon from "./DrawIcons";
import styles from "./ChartTools.module.css";

const TITLE_MAX = 120;
const DESC_MAX = 2000;

function fmtWhen(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/**
 * Save / edit dialog. `draft` null = save the current drawings as a new draft;
 * otherwise edit that draft's title and description, optionally overwriting
 * its drawings with the ones currently on the chart.
 */
function DraftDialog({ ticker, tfLabel, shapesCount, draft, onClose, onSubmit }) {
  const [title, setTitle] = useState(draft?.title || "");
  const [description, setDescription] = useState(draft?.description || "");
  const [overwrite, setOverwrite] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const titleId = useId();
  const inputRef = useRef(null);
  const reduced = prefersReducedMotion();

  useEffect(() => { inputRef.current?.focus(); }, []);

  const trimmed = title.trim();
  const valid = trimmed.length > 0 && trimmed.length <= TITLE_MAX && description.length <= DESC_MAX
    && (draft || shapesCount > 0);

  const submit = async (e) => {
    e.preventDefault();
    if (!valid || saving) return;
    setSaving(true);
    setError(null);
    try {
      await onSubmit({ title: trimmed, description: description.trim(), overwrite });
    } catch (err) {
      setError(err.message || "could not save the draft");
      setSaving(false);
    }
  };

  return (
    <motion.div className={styles.scrim} onMouseDown={onClose}
                initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                transition={{ duration: reduced ? 0 : 0.16 }}>
      <motion.form
        className={styles.dialog}
        role="dialog" aria-modal="true" aria-labelledby={titleId}
        onMouseDown={(e) => e.stopPropagation()}
        onSubmit={submit}
        onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } }}
        initial={reduced ? { opacity: 0 } : { opacity: 0, y: 14, scale: 0.97 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={reduced ? { opacity: 0 } : { opacity: 0, y: 10, scale: 0.97 }}
        transition={{ duration: reduced ? 0 : 0.2, ease: [0.22, 1, 0.36, 1] }}
      >
        <h3 id={titleId} className={styles.dialogTitle}>
          {draft ? "Edit draft" : "Save drawing draft"}
        </h3>
        <p className={styles.dialogMeta}>
          {ticker} · {draft ? `saved on ${draft.timeframe || tfLabel}` : tfLabel} ·{" "}
          {draft ? `${draft.shapes.length} drawing${draft.shapes.length === 1 ? "" : "s"}` : `${shapesCount} drawing${shapesCount === 1 ? "" : "s"}`}
        </p>
        <label className={styles.field}>
          <span>Title <em aria-hidden="true">*</em></span>
          <input ref={inputRef} value={title} maxLength={TITLE_MAX} required
                 onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Flag breakout plan" />
          <small>{trimmed.length}/{TITLE_MAX}</small>
        </label>
        <label className={styles.field}>
          <span>Description</span>
          <textarea value={description} maxLength={DESC_MAX} rows={4}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="What you were marking up, and why" />
          <small>{description.length}/{DESC_MAX}</small>
        </label>
        {draft && (
          <label className={styles.check}>
            <input type="checkbox" checked={overwrite} disabled={shapesCount === 0}
                   onChange={(e) => setOverwrite(e.target.checked)} />
            <span>Replace its drawings with the {shapesCount} now on the chart</span>
          </label>
        )}
        {!draft && shapesCount === 0 && (
          <p className={styles.dialogError}>Nothing is drawn on this chart yet.</p>
        )}
        {error && <p className={styles.dialogError} role="alert">{error}</p>}
        <div className={styles.dialogActions}>
          <button type="button" className={styles.ghostBtn} onClick={onClose}>Cancel</button>
          <button type="submit" className={styles.primaryBtn} disabled={!valid || saving}>
            {saving ? "Saving…" : draft ? "Save changes" : "Save draft"}
          </button>
        </div>
      </motion.form>
    </motion.div>
  );
}

/**
 * Drafts for this ticker: save the current drawings under a title and
 * description, then load (replace or merge), edit or delete them later.
 */
export default function DraftsMenu({ ticker, tf, tfLabel, drawing, onToast, onTimeframe, disabled }) {
  const [open, setOpen] = useState(false);
  const [drafts, setDrafts] = useState(null);   // null = not loaded
  const [error, setError] = useState(null);
  const [confirm, setConfirm] = useState(null); // { id, type: "load" | "delete" }
  const [dialog, setDialog] = useState(null);   // { draft: null | draft }
  const btnRef = useRef(null);
  const menuRef = useRef(null);
  const reduced = prefersReducedMotion();

  const reload = useCallback(async () => {
    try {
      const res = await listDrawingDrafts(ticker);
      setDrafts(res.drafts || []);
      setError(null);
    } catch (e) {
      setError(e.message || "could not load drafts");
    }
  }, [ticker]);

  useEffect(() => {
    // Ticker changed: forget the previous stock's list.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDrafts(null);
    setOpen(false);
  }, [ticker]);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      // The dialog is portaled outside the menu; clicks in it aren't "outside".
      if (dialog) return;
      if (menuRef.current?.contains(e.target) || btnRef.current?.contains(e.target)) return;
      setOpen(false);
      setConfirm(null);
    };
    const onKey = (e) => { if (e.key === "Escape" && !dialog) { setOpen(false); setConfirm(null); } };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, dialog]);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    setConfirm(null);
    if (next) reload();
  };

  const load = (d, mode) => {
    if (d.timeframe && d.timeframe !== tf) onTimeframe(d.timeframe);
    if (mode === "merge") drawing.mergeShapes(d.shapes);
    else drawing.replaceShapes(d.shapes);
    setConfirm(null);
    setOpen(false);
    onToast(`${mode === "merge" ? "Merged" : "Loaded"} “${d.title}” — Ctrl+Z to undo`);
  };

  const askLoad = (d) => {
    if (drawing.shapes.length === 0) load(d, "replace");
    else setConfirm({ id: d.id, type: "load" });
  };

  const remove = async (d) => {
    try {
      await deleteDrawingDraft(d.id);
      setConfirm(null);
      onToast(`Deleted draft “${d.title}”`);
      reload();
    } catch (e) {
      onToast(`Delete failed: ${e.message}`, "error");
    }
  };

  const submitDialog = async ({ title, description, overwrite }) => {
    const draft = dialog?.draft;
    if (draft) {
      await updateDrawingDraft(draft.id, {
        title, description,
        ...(overwrite ? { shapes: drawing.shapes, timeframe: tf } : {}),
      });
      onToast(`Updated draft “${title}”`);
    } else {
      await createDrawingDraft(ticker, { title, description, timeframe: tf, shapes: drawing.shapes });
      onToast(`Saved draft “${title}”`);
    }
    setDialog(null);
    reload();
  };

  return (
    <>
      <Tooltip side="bottom" disabled={open} content={disabled
        ? "Drawing drafts — available once the chart has data"
        : "Drawing drafts — save the current drawings with a title, or load a saved set"}>
        <button ref={btnRef} type="button" className={styles.toolbarBtn} disabled={disabled}
                aria-haspopup="menu" aria-expanded={open}
                onClick={toggle}>
          <DrawIcon name="drafts" size={15} />
          <span>Drafts{drafts?.length ? ` · ${drafts.length}` : ""}</span>
        </button>
      </Tooltip>

      <Popover open={open} anchorRef={btnRef} contentRef={menuRef} className={`${styles.menu} ${styles.draftsMenu}`}
               role="dialog" aria-label={`Drawing drafts for ${ticker}`}>
        <div className={styles.draftsHead}>
          <span>Drafts · {ticker}</span>
          <Tooltip content={drawing.shapes.length
            ? "Save what's drawn now as a named draft"
            : "Nothing to save yet — draw on the chart first"}>
            <button type="button" className={styles.primaryBtn} disabled={drawing.shapes.length === 0}
                    onClick={() => { setDialog({ draft: null }); setConfirm(null); }}>
              <DrawIcon name="save" size={14} /> Save current…
            </button>
          </Tooltip>
        </div>
        {error && <p className={styles.dialogError}>{error}</p>}
        {drafts === null && !error && <p className={styles.menuNote}>Loading drafts…</p>}
        {drafts?.length === 0 && (
          <p className={styles.menuNote}>No drafts for {ticker} yet. Draw on the chart, then “Save current…”.</p>
        )}
        {drafts?.length > 0 && (
          <motion.ul className={styles.draftList} initial="hidden" animate="visible"
                     variants={{ hidden: {}, visible: { transition: { staggerChildren: reduced ? 0 : 0.03 } } }}>
            {drafts.map((d) => {
              const pending = confirm?.id === d.id ? confirm.type : null;
              return (
                <motion.li key={d.id} className={styles.draftItem} layout={!reduced}
                           variants={{ hidden: { opacity: 0, y: reduced ? 0 : 4 }, visible: { opacity: 1, y: 0 } }}>
                  <div className={styles.draftText}>
                    <Tooltip truncate><strong>{d.title}</strong></Tooltip>
                    {d.description && <Tooltip truncate><p>{d.description}</p></Tooltip>}
                    <small>{fmtWhen(d.updated_at)} · {d.timeframe || "—"} · {d.shapes.length} drawing{d.shapes.length === 1 ? "" : "s"}</small>
                  </div>
                  <AnimatePresence mode="wait" initial={false}>
                    {pending === "load" ? (
                      <motion.div key="confirm-load" className={styles.confirmRow}
                                  initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                                  transition={{ duration: reduced ? 0 : 0.12 }}>
                        <span>The chart has {drawing.shapes.length} drawing{drawing.shapes.length === 1 ? "" : "s"}.</span>
                        <Tooltip content="Swap the current drawings for this draft (undoable)">
                          <button type="button" className={styles.primaryBtn} onClick={() => load(d, "replace")}>Replace</button>
                        </Tooltip>
                        <Tooltip content="Add the draft's drawings to the current ones">
                          <button type="button" className={styles.ghostBtn} onClick={() => load(d, "merge")}>Merge</button>
                        </Tooltip>
                        <button type="button" className={styles.ghostBtn} onClick={() => setConfirm(null)}>Cancel</button>
                      </motion.div>
                    ) : pending === "delete" ? (
                      <motion.div key="confirm-delete" className={styles.confirmRow}
                                  initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                                  transition={{ duration: reduced ? 0 : 0.12 }}>
                        <span>Delete this draft for good?</span>
                        <button type="button" className={styles.dangerBtn} onClick={() => remove(d)}>Delete</button>
                        <button type="button" className={styles.ghostBtn} onClick={() => setConfirm(null)}>Keep</button>
                      </motion.div>
                    ) : (
                      <motion.div key="actions" className={styles.draftActions}
                                  initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
                                  transition={{ duration: reduced ? 0 : 0.12 }}>
                        <Tooltip content="Load this draft onto the chart">
                          <button type="button" className={styles.ghostBtn} onClick={() => askLoad(d)}>Load</button>
                        </Tooltip>
                        <Tooltip content="Rename / edit">
                          <button type="button" className={styles.iconBtn} aria-label={`Rename or edit ${d.title}`}
                                  onClick={() => { setDialog({ draft: d }); setConfirm(null); }}>
                            <DrawIcon name="edit" size={14} />
                          </button>
                        </Tooltip>
                        <Tooltip content="Delete draft">
                          <button type="button" className={styles.iconBtn} data-tone="danger"
                                  aria-label={`Delete ${d.title}`}
                                  onClick={() => setConfirm({ id: d.id, type: "delete" })}>
                            <DrawIcon name="trash" size={14} />
                          </button>
                        </Tooltip>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </motion.li>
              );
            })}
          </motion.ul>
        )}
      </Popover>

      {createPortal(
        <AnimatePresence>
          {dialog && (
            <DraftDialog
              key={dialog.draft?.id ?? "new"}
              ticker={ticker}
              tfLabel={tfLabel}
              shapesCount={drawing.shapes.length}
              draft={dialog.draft}
              onClose={() => setDialog(null)}
              onSubmit={submitDialog}
            />
          )}
        </AnimatePresence>,
        document.body,
      )}
    </>
  );
}
