import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./Tour.module.css";

const PAD = 6; // px of breathing room around the spotlit element

/**
 * Hand-rolled guided tour: a spotlight cutout over the current step's target
 * plus a positioned coachmark card. Steps: [{ target, title, body, placement }]
 * where `target` is a CSS selector (null = centered intro card) and placement
 * is "top" | "bottom" | "left" | "right" (best-effort, clamped to viewport).
 * Esc closes, arrows navigate; steps whose target is missing are skipped.
 */
export default function Tour({ steps, onClose }) {
  const [index, setIndex] = useState(0);
  const [rect, setRect] = useState(null);
  const cardRef = useRef(null);
  const restoreFocusRef = useRef(null);

  const reducedMotion =
    document.documentElement.dataset.reducedMotion === "on" ||
    window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;

  // Skip steps whose target isn't currently rendered.
  const usableSteps = useMemo(
    () => steps.filter((s) => !s.target || document.querySelector(s.target)),
    [steps],
  );
  const step = usableSteps[index];
  const last = index === usableSteps.length - 1;

  const measure = useCallback(() => {
    if (!step?.target) {
      setRect(null);
      return;
    }
    const el = document.querySelector(step.target);
    setRect(el ? el.getBoundingClientRect() : null);
  }, [step]);

  // Scroll the target into view, then measure; re-measure on scroll/resize.
  useEffect(() => {
    if (!step) return undefined;
    const el = step.target ? document.querySelector(step.target) : null;
    el?.scrollIntoView({ block: "center", behavior: reducedMotion ? "auto" : "smooth" });
    const t = setTimeout(measure, reducedMotion ? 0 : 260);

    let raf = null;
    const onMove = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = null; measure(); });
    };
    window.addEventListener("resize", onMove);
    window.addEventListener("scroll", onMove, true);
    return () => {
      clearTimeout(t);
      if (raf) cancelAnimationFrame(raf);
      window.removeEventListener("resize", onMove);
      window.removeEventListener("scroll", onMove, true);
    };
  }, [step, measure, reducedMotion]);

  // Focus management: move into the card, restore on close.
  useEffect(() => {
    restoreFocusRef.current = document.activeElement;
    return () => restoreFocusRef.current?.focus?.();
  }, []);
  useEffect(() => {
    cardRef.current?.focus();
  }, [index]);

  const back = useCallback(() => setIndex((i) => Math.max(0, i - 1)), []);
  // onClose updates the parent (settings), so it must not run inside a state
  // updater — React would warn about updating one component while rendering
  // another. Decide from the current index instead.
  const next = useCallback(() => {
    if (index + 1 >= usableSteps.length) onClose();
    else setIndex(index + 1);
  }, [index, usableSteps.length, onClose]);

  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight") next();
      else if (e.key === "ArrowLeft") back();
      else if (e.key === "Tab") {
        // Trap focus inside the card.
        const focusables = cardRef.current?.querySelectorAll("button");
        if (!focusables?.length) return;
        const list = Array.from(focusables);
        const first = list[0];
        const lastEl = list[list.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          lastEl.focus();
        } else if (!e.shiftKey && document.activeElement === lastEl) {
          e.preventDefault();
          first.focus();
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, next, back]);

  if (!step) return null;

  const spot = rect && {
    top: rect.top - PAD,
    left: rect.left - PAD,
    width: rect.width + PAD * 2,
    height: rect.height + PAD * 2,
  };

  // Card position: relative to the spotlight, clamped into the viewport.
  const cardStyle = {};
  if (spot) {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const cardW = Math.min(340, vw - 24);
    const placement = step.placement || (spot.top + spot.height > vh * 0.6 ? "top" : "bottom");
    let top;
    let left = Math.min(Math.max(12, spot.left), vw - cardW - 12);
    if (placement === "top") top = Math.max(12, spot.top - 12);
    else if (placement === "bottom") top = Math.min(vh - 12, spot.top + spot.height + 12);
    else top = Math.min(Math.max(12, spot.top), vh - 180);
    if (placement === "left") left = Math.max(12, spot.left - cardW - 12);
    if (placement === "right") left = Math.min(vw - cardW - 12, spot.left + spot.width + 12);
    cardStyle.top = `${top}px`;
    cardStyle.left = `${left}px`;
    cardStyle.width = `${cardW}px`;
    if (placement === "top") cardStyle.transform = "translateY(-100%)";
  }

  return (
    <div className={styles.layer} data-reduced={reducedMotion ? "on" : "off"}>
      {/* click-catcher behind everything; clicking outside advances nothing, just closes */}
      <div className={styles.backdrop} onClick={onClose} />
      {spot ? (
        <div
          className={styles.spot}
          style={{
            top: spot.top, left: spot.left,
            width: spot.width, height: spot.height,
          }}
        />
      ) : (
        <div className={styles.dim} />
      )}
      <div
        ref={cardRef}
        className={`${styles.card} ${spot ? "" : styles.cardCentered}`}
        style={spot ? cardStyle : undefined}
        role="dialog"
        aria-modal="true"
        aria-label={`Tour step ${index + 1} of ${usableSteps.length}: ${step.title}`}
        tabIndex={-1}
      >
        <div className={styles.count} aria-hidden="true">
          {index + 1} / {usableSteps.length}
        </div>
        <h3 className={styles.title}>{step.title}</h3>
        <p className={styles.body} aria-live="polite">{step.body}</p>
        <div className={styles.controls}>
          <button type="button" className={styles.skip} onClick={onClose}>
            Skip tour
          </button>
          <span className={styles.controlSpacer} />
          {index > 0 && (
            <button type="button" className={styles.nav} onClick={back}>
              Back
            </button>
          )}
          <button type="button" className={styles.navPrimary} onClick={next}>
            {last ? "Done" : "Next"}
          </button>
        </div>
      </div>
    </div>
  );
}
