/**
 * Central motion policy for the dashboard.
 *
 * Every animejs / motion usage in the app routes through this module so that
 * `settings.reducedMotion` (→ <html data-reduced-motion="on">) and the OS-level
 * `prefers-reduced-motion` are honored in exactly one place. When reduced motion
 * is requested, declarative variants collapse to zero-duration and imperative
 * count-ups snap straight to their target value.
 */
import { animate, utils } from "animejs";

/** True when the app setting OR the OS preference asks to reduce motion. */
export function prefersReducedMotion() {
  if (typeof document !== "undefined" &&
      document.documentElement.dataset.reducedMotion === "on") {
    return true;
  }
  if (typeof window !== "undefined" && window.matchMedia) {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }
  return false;
}

// A transition that becomes instant under reduced motion.
function transition(base) {
  return prefersReducedMotion() ? { duration: 0 } : base;
}

/** Fade + slight upward rise — the default enter for cards/sections. */
export const fadeRise = {
  hidden: { opacity: 0, y: prefersReducedMotion() ? 0 : 12 },
  visible: {
    opacity: 1,
    y: 0,
    transition: transition({ duration: 0.32, ease: [0.22, 1, 0.36, 1] }),
  },
};

/** Parent that staggers its children in on first data arrival. */
export const staggerContainer = {
  hidden: {},
  visible: {
    transition: prefersReducedMotion()
      ? { staggerChildren: 0 }
      : { staggerChildren: 0.04, delayChildren: 0.02 },
  },
};

/** Child of `staggerContainer` — rows, list items, KPI cards. */
export const staggerItem = {
  hidden: { opacity: 0, y: prefersReducedMotion() ? 0 : 8 },
  visible: {
    opacity: 1,
    y: 0,
    transition: transition({ duration: 0.28, ease: [0.22, 1, 0.36, 1] }),
  },
};

/**
 * Animate a number in a DOM element from its current value to `to`.
 *
 * @param {HTMLElement} el     target element (its textContent is written)
 * @param {number}      to     final value
 * @param {object}      opts
 *   - from      {number}  start value (default 0)
 *   - duration  {number}  ms (default 900)
 *   - decimals  {number}  fixed decimals (default 0)
 *   - format    {(n:number)=>string}  custom formatter (wins over decimals)
 * @returns the animejs instance, or null when snapped instantly.
 */
export function countUp(el, to, opts = {}) {
  if (!el) return null;
  const { from = 0, duration = 900, decimals = 0, format } = opts;
  const render = (n) => (format ? format(n) : Number(n).toFixed(decimals));

  if (prefersReducedMotion()) {
    el.textContent = render(to);
    return null;
  }

  const state = { v: from };
  return animate(state, {
    v: to,
    duration,
    ease: "outExpo",
    modifier: utils.round(decimals > 0 ? decimals : 0),
    onUpdate: () => { el.textContent = render(state.v); },
  });
}
