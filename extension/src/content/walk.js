// Collecting the text nodes that are safe to annotate.
//
// Two rules keep this from breaking pages:
//   1. Collect first, mutate later. Mutating during a TreeWalker traversal
//      invalidates the walk and produces skipped or doubled matches.
//   2. Reject anything the user might be typing into, anything semantically
//      code, and anything we already touched.

import { mayContainTicker } from "./detect.js";

/** Elements whose text must never be rewritten. */
const SKIP_TAGS = new Set([
  "SCRIPT", "STYLE", "NOSCRIPT", "TEXTAREA", "INPUT", "SELECT", "OPTION",
  "CODE", "PRE", "KBD", "SAMP", "SVG", "CANVAS", "TITLE", "HEAD",
  "SD-BADGE",
]);

/** Marker attribute stamped on containers we've already processed. */
export const DONE_ATTR = "data-sd-done";
export const SKIP_ATTR = "data-sd-skip";

const MAX_ANCESTOR_HOPS = 12;

/** True when this text node sits somewhere we must not touch. */
export function isSkippable(node, root = document.body) {
  let el = node.parentElement;
  for (let i = 0; el && i < MAX_ANCESTOR_HOPS; i++) {
    if (SKIP_TAGS.has(el.tagName)) return true;
    if (el.isContentEditable) return true;
    if (el.hasAttribute(SKIP_ATTR) || el.hasAttribute(DONE_ATTR)) return true;
    if (el.getAttribute("aria-hidden") === "true") return true;
    if (el === root) break;
    el = el.parentElement;
  }
  return false;
}

/**
 * Collect candidate text nodes under `root`.
 * @param {number} limit - hard cap, so a pathological page can't stall the tab.
 */
export function collectTextNodes(root, limit = 2000) {
  if (!root || !root.ownerDocument) return [];
  const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!mayContainTicker(node.nodeValue)) return NodeFilter.FILTER_REJECT;
      if (isSkippable(node, root)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });

  const out = [];
  for (let n = walker.nextNode(); n && out.length < limit; n = walker.nextNode()) {
    out.push(n);
  }
  return out;
}
