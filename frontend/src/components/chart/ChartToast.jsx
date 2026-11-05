import { useEffect, useRef } from "react";
import { AnimatePresence, motion } from "motion/react";
import { animate } from "animejs";
import { prefersReducedMotion } from "../../lib/motionConfig";
import { TOAST_MS } from "./useChartToast";
import styles from "./ChartTools.module.css";

/** The time-left bar, driven by animejs; static under reduced motion. */
function Countdown() {
  const ref = useRef(null);
  useEffect(() => {
    if (!ref.current || prefersReducedMotion()) return undefined;
    const anim = animate(ref.current, { scaleX: [1, 0], duration: TOAST_MS, ease: "linear" });
    return () => anim.pause();
  }, []);
  return <span ref={ref} className={styles.toastBar} aria-hidden="true" />;
}

/** A polite status toast pinned to the bottom of the chart. */
export default function ChartToast({ toast, onDismiss }) {
  const reduced = prefersReducedMotion();
  return (
    <div className={styles.toastSlot} role="status" aria-live="polite">
      <AnimatePresence>
        {toast && (
          <motion.div
            key={toast.id}
            className={styles.toast}
            data-tone={toast.tone}
            initial={reduced ? { opacity: 0 } : { opacity: 0, y: 12, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={reduced ? { opacity: 0 } : { opacity: 0, y: 8, scale: 0.97 }}
            transition={{ duration: reduced ? 0 : 0.2, ease: [0.22, 1, 0.36, 1] }}
            onClick={onDismiss}
          >
            <span>{toast.text}</span>
            <Countdown />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
