import { useId, useLayoutEffect, useRef } from "react";
import { motion } from "motion/react";
import CollapseToggle from "./CollapseToggle";
import { prefersReducedMotion } from "../lib/motionConfig";
import styles from "./CollapsibleSection.module.css";

const EASE = [0.22, 1, 0.36, 1];

/**
 * A pane with a caption header that collapses its body.
 *
 * - The chevron (CollapseToggle) is the keyboard / screen-reader control: it
 *   carries aria-expanded and aria-controls pointing at the body region.
 * - The caption and the empty stretch of the header are a mouse convenience
 *   that toggle too. The `right` slot sits outside that hit area, so using a
 *   control there (e.g. a Segmented filter) never collapses the pane.
 * - The body stays MOUNTED while collapsed: height animates to 0 and the
 *   region is made `inert` + visibility:hidden, so nothing inside is focusable
 *   or announced. Keeping it mounted means the chart canvas is never torn down
 *   and panes that fetch on mount don't refetch on every expand.
 * - Height animates in/out of "auto" with motion; under reduced motion (app
 *   setting or OS preference) the transition is instant.
 */
export default function CollapsibleSection({
  caption,
  right = null,
  collapsed = false,
  onToggle,
  className = "",
  bodyClassName = "",
  children,
}) {
  const uid = useId();
  const sectionRef = useRef(null);
  const bodyRef = useRef(null);
  // Collapsing makes the body inert; if focus was inside it the browser drops
  // it to <body> and a keyboard user loses their place. Hand it to this
  // section's own toggle instead — before paint, whichever control collapsed
  // it (chevron, caption, "Collapse all").
  useLayoutEffect(() => {
    if (!collapsed) return;
    const body = bodyRef.current;
    const active = document.activeElement;
    if (body && active && active !== document.body && body.contains(active)) {
      sectionRef.current?.querySelector("button[aria-controls]")?.focus();
    }
  }, [collapsed]);
  const captionId = `${uid}-caption`;
  const bodyId = `${uid}-body`;
  const transition = prefersReducedMotion()
    ? { duration: 0 }
    : { duration: 0.26, ease: EASE };

  return (
    <section
      ref={sectionRef}
      className={`${styles.section} ${className}`}
      data-collapsed={collapsed ? "yes" : "no"}
    >
      <div className={styles.head}>
        <CollapseToggle
          collapsed={collapsed}
          onClick={onToggle}
          label={typeof caption === "string" ? caption : "section"}
          controls={bodyId}
        />
        {/* Click target only — keyboard users reach the same action through the
            chevron button, so this is not a second tab stop. */}
        <span className={styles.hit} onClick={onToggle}>
          <span id={captionId} className="caption">{caption}</span>
        </span>
        {right && <div className={styles.right}>{right}</div>}
      </div>
      <motion.div
        ref={bodyRef}
        id={bodyId}
        role="region"
        aria-labelledby={captionId}
        className={styles.clip}
        inert={collapsed}
        initial={false}
        animate={collapsed
          ? { height: 0, transitionEnd: { visibility: "hidden" } }
          : { height: "auto", visibility: "visible" }}
        transition={transition}
      >
        <div className={`${styles.body} ${bodyClassName}`}>{children}</div>
      </motion.div>
    </section>
  );
}
