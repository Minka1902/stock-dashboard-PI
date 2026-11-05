/**
 * Positioning for popups portaled to <body> (menus, tooltips).
 *
 * Both Popover and Tooltip render into the root stacking context to escape
 * `backdrop-filter` stacking contexts and `overflow: hidden` panels (see
 * Popover.jsx), which means their position has to be computed from the
 * anchor's viewport rect rather than inherited from the layout. Everything
 * here works in viewport coordinates, for `position: fixed`.
 */

/** Minimum distance kept between a popup and the viewport edge. */
export const VIEWPORT_MARGIN = 8;

/** Width a drop-down is assumed to want before it flips its alignment. */
const ROOMY = 360;

/**
 * Drop-down placement used by Popover: below the anchor, aligned to its start
 * or end edge, with a max height that leaves a gutter at the bottom so a long
 * menu scrolls internally instead of running off the window.
 */
export function dropdownStyle(anchorEl, align, gap) {
  const r = anchorEl.getBoundingClientRect();
  const top = r.bottom + gap;
  // The popup keeps its anchored edge; its width is capped so the far edge
  // stays on screen too (a 360px menu anchored near the middle of a phone
  // used to open half off the left edge).
  // An end-aligned popup whose anchor sits near the LEFT of a narrow window
  // (a phone's wrapped top bar) would get only the sliver to its left, so it
  // flips to start-aligned when that side has more room than the other.
  const roomEnd = r.right - VIEWPORT_MARGIN;
  const roomStart = window.innerWidth - r.left - VIEWPORT_MARGIN;
  const side = align === "end" && roomEnd < ROOMY && roomStart > roomEnd ? "start"
    : align === "start" && roomStart < ROOMY && roomEnd > roomStart ? "end"
      : align;
  const edge = side === "end"
    ? Math.max(VIEWPORT_MARGIN, window.innerWidth - r.right)
    : Math.max(VIEWPORT_MARGIN, r.left);
  return {
    position: "fixed",
    top,
    maxHeight: `calc(100vh - ${Math.round(top)}px - 16px)`,
    maxWidth: `calc(100vw - ${Math.round(edge)}px - ${VIEWPORT_MARGIN}px)`,
    zIndex: "var(--z-popover)",
    ...(side === "end" ? { right: edge } : { left: edge }),
  };
}

const OPPOSITE = { top: "bottom", bottom: "top", left: "right", right: "left" };

const clamp = (v, min, max) => Math.min(Math.max(v, min), Math.max(min, max));

/**
 * Place a floating box of `size` on `side` of `rect`, flipping to the opposite
 * side when it would not fit, and sliding along the cross axis to stay inside
 * the viewport. Pure (viewport size is passed in) so it is trivially testable.
 *
 * @param rect  anchor DOMRect-like { top, left, right, bottom, width, height }
 * @param size  { width, height } of the floating box (untransformed)
 * @param side  preferred side: "top" | "bottom" | "left" | "right"
 * @param gap   distance between anchor and box
 * @param view  { width, height } of the viewport
 * @returns { top, left, side, arrow } — `side` is where it actually went and
 *          `arrow` is the arrow's offset along the cross axis, in px from the
 *          box's left (top/bottom) or top (left/right) edge.
 */
export function placeFloating(rect, size, side = "top", gap = 8, view, margin = VIEWPORT_MARGIN) {
  const vw = view?.width ?? window.innerWidth;
  const vh = view?.height ?? window.innerHeight;
  const fits = (s) => {
    if (s === "top") return rect.top - gap - size.height >= margin;
    if (s === "bottom") return rect.bottom + gap + size.height <= vh - margin;
    if (s === "left") return rect.left - gap - size.width >= margin;
    return rect.right + gap + size.width <= vw - margin;
  };

  let placed = OPPOSITE[side] ? side : "top";
  if (!fits(placed) && fits(OPPOSITE[placed])) placed = OPPOSITE[placed];
  // A side placement that fits neither way (narrow window) falls back to
  // above/below, where there is usually more room.
  if ((placed === "left" || placed === "right") && !fits(placed)) {
    placed = fits("top") ? "top" : "bottom";
  }

  const cx = rect.left + rect.width / 2;
  const cy = rect.top + rect.height / 2;
  let top;
  let left;
  if (placed === "top" || placed === "bottom") {
    top = placed === "top" ? rect.top - gap - size.height : rect.bottom + gap;
    left = cx - size.width / 2;
  } else {
    left = placed === "left" ? rect.left - gap - size.width : rect.right + gap;
    top = cy - size.height / 2;
  }
  left = clamp(left, margin, vw - size.width - margin);
  top = clamp(top, margin, vh - size.height - margin);

  const arrow = placed === "top" || placed === "bottom"
    ? clamp(cx - left, 10, size.width - 10)
    : clamp(cy - top, 8, size.height - 8);

  return { top: Math.round(top), left: Math.round(left), side: placed, arrow: Math.round(arrow) };
}
