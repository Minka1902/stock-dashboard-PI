import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import { prefersReducedMotion } from "../../lib/motionConfig";
import styles from "./ChartTools.module.css";

const MAX_LEN = 280;

/**
 * Inline editor for text / callout drawings — replaces window.prompt. Sits on
 * the chart at the label's own position. Enter saves, Shift+Enter adds a line
 * (callouts), Esc cancels, clicking away saves.
 */
export default function TextEditor({ editing, onCommit, onCancel }) {
  const [value, setValue] = useState(editing.text || "");
  const ref = useRef(null);
  const doneRef = useRef(false);
  const reduced = prefersReducedMotion();
  const multiline = editing.shape.kind === "callout";

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    el.select();
  }, []);

  const finish = (commit) => {
    if (doneRef.current) return;
    doneRef.current = true;
    if (commit) onCommit(value);
    else onCancel();
  };

  return (
    <motion.div
      className={styles.textEditor}
      style={{ left: editing.x + (multiline ? 0 : 4), top: editing.y - (multiline ? 16 : 26) }}
      initial={reduced ? { opacity: 0 } : { opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: reduced ? 0 : 0.14 }}
      onPointerDown={(e) => e.stopPropagation()}
    >
      <textarea
        ref={ref}
        className={styles.textInput}
        value={value}
        rows={multiline ? Math.min(4, Math.max(1, value.split("\n").length)) : 1}
        maxLength={MAX_LEN}
        placeholder={multiline ? "Callout text" : "Label text"}
        aria-label={multiline ? "Callout text" : "Label text"}
        onChange={(e) => setValue(multiline ? e.target.value : e.target.value.replace(/\n/g, " "))}
        onKeyDown={(e) => {
          e.stopPropagation();
          if (e.key === "Escape") { e.preventDefault(); finish(false); }
          if (e.key === "Enter" && !(multiline && e.shiftKey)) { e.preventDefault(); finish(true); }
        }}
        onBlur={() => finish(true)}
      />
      <span className={styles.textHint}>{multiline ? "Enter saves · Shift+Enter new line · Esc cancels" : "Enter saves · Esc cancels"}</span>
    </motion.div>
  );
}
