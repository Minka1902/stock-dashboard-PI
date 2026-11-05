// The hover card.
//
// Exactly ONE card element exists per page, reused for every badge — creating one
// per badge would put hundreds of shadow roots on a Reddit thread. It lives in a
// shadow root attached to documentElement so page CSS can't restyle it, and it is
// positioned fixed at the viewport level so it escapes overflow:hidden ancestors.

import { convictionTier, activeChips } from "../lib/boom.js";

let host = null;
let card = null;
let hideTimer = null;

function ensure() {
  if (card) return card;
  host = document.createElement("div");
  host.setAttribute("data-sd-skip", "1");
  const shadow = host.attachShadow({ mode: "open" });
  shadow.innerHTML = `<style>${CARD_CSS}</style><div class="card" hidden></div>`;
  document.documentElement.appendChild(host);
  card = shadow.querySelector(".card");
  card.addEventListener("mouseenter", () => clearTimeout(hideTimer));
  card.addEventListener("mouseleave", () => scheduleHide());
  return card;
}

export function scheduleHide(delay = 180) {
  clearTimeout(hideTimer);
  hideTimer = setTimeout(hide, delay);
}

export function hide() {
  if (card) card.hidden = true;
}

/**
 * Show the card for a ticker.
 *
 * @param {object|null} score - the BoomScore row, or null when the ticker isn't
 *   tracked. We never invent a score: an untracked ticker gets actions, not a number.
 * @param {{onAdd: Function, onOpen: Function}} handlers
 */
export function show(anchor, ticker, score, handlers) {
  const el = ensure();
  clearTimeout(hideTimer);

  const tier = score ? convictionTier(score.score) : null;
  const chips = activeChips(score, 4);

  el.innerHTML = `
    <div class="head">
      <span class="tk">${escapeHtml(ticker)}</span>
      ${score ? `<span class="score t-${tier.tone}">${score.score}</span>` : ""}
    </div>
    ${score ? `<div class="tier">${escapeHtml(tier.label)}</div>` : `<div class="tier muted">Not in your watchlist or portfolio</div>`}
    ${
      chips.length
        ? `<div class="chips">${chips
            .map((c) => `<span class="chip ${c.tone}" title="${escapeHtml(c.tip)}">${escapeHtml(c.label)}</span>`)
            .join("")}</div>`
        : ""
    }
    <div class="acts">
      <button class="act" data-act="open">Open in dashboard</button>
      <button class="act" data-act="add">Add to watchlist</button>
    </div>
    <div class="msg" hidden></div>
  `;

  el.querySelector('[data-act="open"]').addEventListener("click", () => handlers.onOpen(ticker));
  el.querySelector('[data-act="add"]').addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "Adding…";
    const res = await handlers.onAdd(ticker);
    const msg = el.querySelector(".msg");
    msg.hidden = false;
    msg.textContent = res?.ok ? "Added to watchlist" : res?.detail || "Could not add";
    msg.className = `msg ${res?.ok ? "good" : "bad"}`;
    btn.textContent = res?.ok ? "Added" : "Add to watchlist";
    btn.disabled = Boolean(res?.ok);
  });

  el.hidden = false;
  position(el, anchor);
}

function position(el, anchor) {
  const r = anchor.getBoundingClientRect();
  // Measure after unhiding so we know the real size before deciding to flip.
  const cw = el.offsetWidth || 240;
  const ch = el.offsetHeight || 140;
  const pad = 8;
  let left = r.left;
  let top = r.bottom + 6;
  if (left + cw > window.innerWidth - pad) left = window.innerWidth - cw - pad;
  if (left < pad) left = pad;
  // Flip above the badge when there isn't room below.
  if (top + ch > window.innerHeight - pad) top = Math.max(pad, r.top - ch - 6);
  el.style.left = `${left}px`;
  el.style.top = `${top}px`;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

const CARD_CSS = `
.card {
  position: fixed;
  z-index: 2147483647;
  width: 250px;
  padding: 10px 12px;
  border-radius: 10px;
  background: #17141f;
  border: 1px solid #3a3550;
  box-shadow: 0 8px 28px rgba(0,0,0,.5);
  color: #e8e4f5;
  font: 12px/1.45 ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif;
}
.card[hidden] { display: none; }
.head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.tk { font: 700 13px ui-monospace, SFMono-Regular, Menlo, monospace; letter-spacing: .02em; }
.score { font: 700 13px ui-monospace, monospace; padding: 1px 6px; border-radius: 5px; }
.t-high { background:#2f7d5b; } .t-mid { background:#3b6ea5; }
.t-low { background:#6b5a2f; } .t-faint { background:#3a3550; } .t-neg { background:#8a3b3b; }
.tier { margin-top: 2px; font-size: 11px; color: #a49dc0; }
.tier.muted { font-style: italic; }
.chips { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
.chip {
  font: 600 10px ui-monospace, monospace; padding: 2px 5px; border-radius: 4px;
  border: 1px solid transparent; cursor: help;
}
.chip.bull { background:#1e3a2c; border-color:#2f7d5b; color:#8fe0b8; }
.chip.bear { background:#3a1e1e; border-color:#8a3b3b; color:#e8a0a0; }
.acts { display: flex; gap: 6px; margin-top: 10px; }
.act {
  flex: 1; padding: 5px 6px; font: 500 11px inherit; cursor: pointer;
  border-radius: 6px; border: 1px solid #4b4468; background: #221e2e; color: #e8e4f5;
}
.act:hover:not(:disabled) { background: #2c2740; }
.act:disabled { opacity: .55; cursor: default; }
.msg { margin-top: 7px; font-size: 11px; }
.msg.good { color: #8fe0b8; } .msg.bad { color: #e8a0a0; }
`;
