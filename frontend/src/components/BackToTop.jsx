import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import Icon from "./Icon";
import Tooltip from "./Tooltip";
import { prefersReducedMotion } from "../lib/motionConfig";
import styles from "./BackToTop.module.css";

/**
 * Floating "back to top" control for the single scroll container (App's
 * `.scroll` div — the window itself never scrolls). Appears after the user has
 * scrolled past `threshold` px and returns the container to the top on click.
 */
export default function BackToTop({ scrollRef, threshold = 600 }) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = scrollRef?.current;
    if (!el) return undefined;
    let ticking = false;
    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        setVisible(el.scrollTop > threshold);
        ticking = false;
      });
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
    return () => el.removeEventListener("scroll", onScroll);
  }, [scrollRef, threshold]);

  const toTop = () => {
    scrollRef?.current?.scrollTo({
      top: 0,
      behavior: prefersReducedMotion() ? "auto" : "smooth",
    });
  };

  return (
    <AnimatePresence>
      {visible && (
        <Tooltip key="top" side="left" content="Back to top">
        <motion.button
          type="button"
          className={styles.btn}
          aria-label="Back to top"
          onClick={toTop}
          initial={{ opacity: 0, y: prefersReducedMotion() ? 0 : 16 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: prefersReducedMotion() ? 0 : 16 }}
          transition={{ duration: prefersReducedMotion() ? 0 : 0.22, ease: [0.22, 1, 0.36, 1] }}
        >
          <Icon name="chevronUp" size={20} />
        </motion.button>
        </Tooltip>
      )}
    </AnimatePresence>
  );
}
