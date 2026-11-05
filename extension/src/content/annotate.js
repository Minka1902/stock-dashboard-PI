// Inserting badges into a live page.
//
// The badge is ADDITIVE: we never rewrite, wrap or restyle the matched text. A
// Range is collapsed to the end of the match and an <sd-badge> is inserted after
// it, so the page's own text node keeps its content and its styling, and teardown
// is a clean remove(). Wrapping the match in our own element is what breaks
// layouts and selection on sites like X.

import { scanText } from "./detect.js";
import { collectTextNodes, DONE_ATTR } from "./walk.js";

export const BADGE_TAG = "sd-badge";

/** Per-pass and per-page budgets, so a pathological page can't stall the tab. */
export const PASS_LIMIT = 250;
export const PAGE_LIMIT = 800;

/**
 * Annotate `root`, returning the tickers found.
 *
 * @param {(el: HTMLElement, ticker: string) => void} onBadge - called for each
 *   inserted badge so the caller can wire hover behaviour.
 * @returns {{tickers: string[], inserted: number}}
 */
export function annotate(root, matcher, onBadge, budget = { used: 0 }) {
  const nodes = collectTextNodes(root);
  const tickers = new Set();
  let inserted = 0;

  for (const node of nodes) {
    if (inserted >= PASS_LIMIT || budget.used >= PAGE_LIMIT) break;
    // The node may have been detached by the page between collection and now.
    if (!node.isConnected) continue;

    const hits = scanText(node.nodeValue, matcher);
    if (!hits.length) continue;

    // Insert right-to-left so earlier offsets stay valid as we mutate.
    for (let i = hits.length - 1; i >= 0; i--) {
      if (inserted >= PASS_LIMIT || budget.used >= PAGE_LIMIT) break;
      const hit = hits[i];
      const badge = makeBadge(node.ownerDocument, hit.ticker);
      try {
        const range = node.ownerDocument.createRange();
        range.setStart(node, hit.end);
        range.setEnd(node, hit.end);
        range.insertNode(badge);
      } catch {
        continue; // offset went stale; skip this one rather than corrupt the node
      }
      tickers.add(hit.ticker);
      inserted++;
      budget.used++;
      onBadge?.(badge, hit.ticker);
    }

    // Mark the containing element so later passes skip this subtree.
    node.parentElement?.setAttribute(DONE_ATTR, "1");
  }

  return { tickers: [...tickers], inserted };
}

function makeBadge(doc, ticker) {
  const el = doc.createElement(BADGE_TAG);
  el.setAttribute("data-ticker", ticker);
  el.setAttribute("role", "button");
  el.setAttribute("tabindex", "0");
  el.setAttribute("aria-label", `${ticker} signals`);
  // Shadow root so the host page's CSS cannot reach the badge and vice versa.
  const shadow = el.attachShadow({ mode: "open" });
  shadow.innerHTML = `<style>${BADGE_CSS}</style><span class="dot" part="dot">·</span>`;
  return el;
}

/** Paint a badge once its score arrives (or mark it unknown). */
export function paintBadge(el, score, tone) {
  const dot = el.shadowRoot?.querySelector(".dot");
  if (!dot) return;
  if (score == null) {
    dot.textContent = "·";
    dot.className = "dot unknown";
    el.setAttribute("title", `${el.dataset.ticker} — not tracked yet`);
    return;
  }
  dot.textContent = String(score);
  dot.className = `dot t-${tone}`;
  el.setAttribute("title", `${el.dataset.ticker} — Boom Score ${score}`);
}

/** Remove every badge and clear the done-markers, restoring the page. */
export function teardown(doc = document) {
  for (const el of doc.querySelectorAll(BADGE_TAG)) el.remove();
  for (const el of doc.querySelectorAll(`[${DONE_ATTR}]`)) el.removeAttribute(DONE_ATTR);
}

const BADGE_CSS = `
:host {
  display: inline-block;
  vertical-align: baseline;
  margin: 0 0 0 3px;
  cursor: pointer;
  line-height: 1;
  /* The page's font could be anything; pin our own so badges look consistent. */
  font: 600 10px/1 ui-monospace, SFMono-Regular, Menlo, monospace;
}
.dot {
  display: inline-block;
  min-width: 14px;
  padding: 2px 4px;
  border-radius: 4px;
  text-align: center;
  color: #f4f2ff;
  background: #3a3550;
  border: 1px solid #4b4468;
  transition: filter 120ms ease-out;
}
.dot:hover { filter: brightness(1.25); }
.dot.unknown { opacity: 0.55; }
.dot.t-high  { background: #2f7d5b; border-color: #3fa878; }
.dot.t-mid   { background: #3b6ea5; border-color: #5089c4; }
.dot.t-low   { background: #6b5a2f; border-color: #93803f; }
.dot.t-faint { background: #3a3550; border-color: #4b4468; }
.dot.t-neg   { background: #8a3b3b; border-color: #b25050; }
`;
