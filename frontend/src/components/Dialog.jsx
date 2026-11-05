import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import Tooltip from "./Tooltip";
import { prefersReducedMotion } from "../lib/motionConfig";
import styles from "./Dialog.module.css";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Modal dialog: portaled, focus-trapped, Esc and backdrop close, focus goes
 * back to whatever opened it. On phones it becomes a bottom sheet.
 *
 * `tone` tints the top rule ("caution" | "negative" | "accent") so a dialog
 * that changes something risky reads differently from a plain form.
 * `initialFocus` is a ref to focus on open (default: the first field).
 */
export default function Dialog({
  open, title, caption, onClose, children, footer, size = "md", tone,
  initialFocus, closeOnBackdrop = true, className,
}) {
  const titleId = useId();
  const panelRef = useRef(null);
  const returnFocus = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    returnFocus.current = document.activeElement;
    const panel = panelRef.current;
    const first = initialFocus?.current || panel?.querySelector(FOCUSABLE);
    first?.focus();
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
        return;
      }
      if (e.key !== "Tab" || !panel) return;
      const items = [...panel.querySelectorAll(FOCUSABLE)];
      if (!items.length) return;
      const [head, tail] = [items[0], items[items.length - 1]];
      if (e.shiftKey && document.activeElement === head) {
        e.preventDefault();
        tail.focus();
      } else if (!e.shiftKey && document.activeElement === tail) {
        e.preventDefault();
        head.focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      returnFocus.current?.focus?.();
    };
  }, [open, onClose, initialFocus]);

  const reduced = prefersReducedMotion();

  return createPortal(
    <AnimatePresence>
      {open && (
        <div className={styles.layer} key="dialog">
          <motion.div
            className={styles.backdrop}
            onClick={closeOnBackdrop ? onClose : undefined}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: reduced ? 0 : 0.18 }}
          />
          <motion.div
            ref={panelRef}
            className={`${styles.panel} ${className || ""}`}
            data-size={size}
            data-tone={tone}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            initial={reduced ? { opacity: 0 } : { opacity: 0, y: 16, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={reduced ? { opacity: 0 } : { opacity: 0, y: 12, scale: 0.98 }}
            transition={reduced ? { duration: 0 } : { duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
          >
            <header className={styles.head}>
              <div>
                {caption && <span className="caption">{caption}</span>}
                <h2 id={titleId} className={styles.title}>{title}</h2>
              </div>
              <Tooltip side="left" content="Close" shortcut={["Esc"]}>
                <button type="button" className={styles.close} onClick={onClose} aria-label="Close">
                  ×
                </button>
              </Tooltip>
            </header>
            <div className={styles.body}>{children}</div>
            {footer && <footer className={styles.foot}>{footer}</footer>}
          </motion.div>
        </div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
