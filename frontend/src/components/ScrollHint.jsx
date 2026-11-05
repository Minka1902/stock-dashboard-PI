import { useEffect, useRef, useState } from "react";
import styles from "./ScrollHint.module.css";

/**
 * A horizontal scroller that shows a fade on whichever edge still has
 * content. Scrollbars are hidden app-wide, so without it a wide table on a
 * phone looks complete when it isn't. `className` styles the scroller itself
 * (it keeps its own overflow-x: auto).
 */
export default function ScrollHint({ className, children }) {
  const ref = useRef(null);
  const [edges, setEdges] = useState({ left: false, right: false });

  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const update = () => {
      const more = el.scrollWidth - el.clientWidth;
      const left = more > 2 && el.scrollLeft > 2;
      const right = more > 2 && el.scrollLeft < more - 2;
      setEdges((prev) => (prev.left === left && prev.right === right ? prev : { left, right }));
    };
    update();
    el.addEventListener("scroll", update, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => {
      el.removeEventListener("scroll", update);
      ro.disconnect();
    };
  }, []);

  return (
    <div className={styles.frame} data-left={edges.left ? "yes" : "no"} data-right={edges.right ? "yes" : "no"}>
      <div ref={ref} className={className}>{children}</div>
    </div>
  );
}
