import { useLayoutEffect, useState } from "react";
import { createPortal } from "react-dom";
import { AnimatePresence, motion } from "motion/react";
import { prefersReducedMotion } from "../lib/motionConfig";
import { dropdownStyle } from "../lib/floating";

/**
 * An anchored popup that is immune to its parent's stacking and clipping.
 *
 * Every menu in the app used to be `position: absolute` inside its trigger's
 * wrapper. That looks right and fails in two ways once the page has tables:
 *
 *  - The top band sets `backdrop-filter`, which creates a stacking context.
 *    A z-index inside it is only compared against its siblings, so a menu
 *    could never outrank a sticky table row — the row won on DOM order and
 *    sliced the menu in half.
 *  - Panels are `overflow: hidden` and table wrappers `overflow-x: auto`, so
 *    a row-level menu was clipped by its own container.
 *
 * Rendering to <body> escapes both. The trade-off is that position must be
 * computed rather than inherited, so it is recalculated on scroll (capturing,
 * to catch inner scrollers) and resize.
 *
 * Anchoring uses `right` rather than a translateX so it does not fight the
 * transform Motion animates. The placement math lives in lib/floating.js,
 * shared with Tooltip.
 */
export default function Popover({
  open,
  anchorRef,
  // Explicit rather than `ref`: forwarding a ref through a props spread is
  // version-dependent and failed silently here — the menu rendered, but
  // focus management and arrow-key roving broke because the ref stayed null.
  contentRef,
  align = "end",
  gap = 8,
  className = "",
  children,
  ...rest
}) {
  const [style, setStyle] = useState(null);

  useLayoutEffect(() => {
    if (!open) return undefined;
    const place = () => {
      if (anchorRef.current) setStyle(dropdownStyle(anchorRef.current, align, gap));
    };
    place();
    window.addEventListener("resize", place);
    // Capture phase: scroll does not bubble, and the anchor may sit inside a
    // scrollable panel rather than the document.
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, anchorRef, align, gap]);

  // The last position is deliberately kept while closing: AnimatePresence
  // keeps the element mounted through its exit animation, and it should fade
  // out where it was rather than jump. useLayoutEffect re-places it before the
  // next paint on reopen, so no stale frame is ever visible.

  const reduced = prefersReducedMotion();

  // The portal wraps AnimatePresence, not the other way round. Handing a raw
  // portal object to AnimatePresence gives it nothing it can recognise as an
  // animatable child, and it renders nothing at all. Portalling a stable
  // container and keeping the conditional inside AnimatePresence gives it a
  // plain motion element to track, so exit animations work.
  return createPortal(
    <AnimatePresence>
      {open && style && (
        <motion.div
          ref={contentRef}
          className={className}
          style={style}
          initial={reduced ? { opacity: 0 } : { opacity: 0, scale: 0.96, y: -6 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={reduced ? { opacity: 0 } : { opacity: 0, scale: 0.96, y: -6 }}
          transition={{ duration: reduced ? 0 : 0.16, ease: [0.22, 1, 0.36, 1] }}
          {...rest}
        >
          {children}
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
