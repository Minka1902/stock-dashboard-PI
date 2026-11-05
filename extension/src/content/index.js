// Content script entry.
//
// Never calls fetch: a request from here would carry the visited page's origin
// and be blocked by the dashboard's CORS. Everything goes through the background
// worker, which owns caching and credentials.

import { ext } from "../lib/browser.js";
import { MSG } from "../lib/messages.js";
import { buildMatcher } from "./detect.js";
import { annotate, paintBadge, teardown, PAGE_LIMIT } from "./annotate.js";
import { convictionTier } from "../lib/boom.js";
import * as card from "./card.js";

const SCAN_DEBOUNCE_MS = 500;
const HOVER_INTENT_MS = 150;

const budget = { used: 0 };
const badgesByTicker = new Map(); // ticker -> Set<HTMLElement>
let matcher = null;
let observer = null;
let scanTimer = null;
let lastHref = location.href;
let running = false;

async function send(type, payload = {}) {
  try {
    return await ext.runtime.sendMessage({ type, ...payload });
  } catch {
    // Background asleep or extension reloading — callers treat this as "no data".
    return { ok: false, status: 0, detail: "background unavailable" };
  }
}

function trackBadge(el, ticker) {
  if (!badgesByTicker.has(ticker)) badgesByTicker.set(ticker, new Set());
  badgesByTicker.get(ticker).add(el);

  let timer = null;
  const open = () => {
    const score = scoreCache.get(ticker) ?? null;
    card.show(el, ticker, score, {
      onOpen: (t) => send(MSG.OPEN_TICKER, { ticker: t }),
      onAdd: (t) => send(MSG.ADD_WATCH, { ticker: t }),
    });
  };
  el.addEventListener("mouseenter", () => {
    timer = setTimeout(open, HOVER_INTENT_MS);
  });
  el.addEventListener("mouseleave", () => {
    clearTimeout(timer);
    card.scheduleHide();
  });
  el.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    clearTimeout(timer);
    open();
  });
  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      open();
    }
  });
}

const scoreCache = new Map(); // ticker -> BoomScore | null (null = known-absent)

async function paint(tickers) {
  const unknown = tickers.filter((t) => !scoreCache.has(t));
  if (unknown.length) {
    const res = await send(MSG.SCORES, { tickers: unknown });
    const scores = res?.ok ? res.data.scores || {} : {};
    // Cache misses as null too, so we don't re-ask for untracked tickers.
    for (const t of unknown) scoreCache.set(t, scores[t] ?? null);
  }
  for (const t of tickers) {
    const row = scoreCache.get(t) ?? null;
    const tone = row ? convictionTier(row.score).tone : null;
    for (const el of badgesByTicker.get(t) ?? []) paintBadge(el, row?.score ?? null, tone);
  }
}

function scan(root = document.body) {
  if (!running || !matcher || budget.used >= PAGE_LIMIT) return;
  // Our own insertions would otherwise re-trigger the observer.
  observer?.disconnect();
  let found;
  try {
    found = annotate(root, matcher, trackBadge, budget);
  } finally {
    connectObserver();
  }
  if (found.tickers.length) paint(found.tickers);
}

function connectObserver() {
  if (!running) return;
  observer?.observe(document.body, { childList: true, subtree: true });
}

function scheduleScan() {
  clearTimeout(scanTimer);
  scanTimer = setTimeout(() => {
    // SPA route changes (X, Reddit) replace the view without a page load.
    if (location.href !== lastHref) {
      lastHref = location.href;
      budget.used = 0;
    }
    scan();
  }, SCAN_DEBOUNCE_MS);
}

async function start() {
  if (running) return;
  const gate = await send(MSG.SITE_ENABLED, { host: location.hostname });
  if (!gate?.ok || !gate.data.enabled) return;

  const res = await send(MSG.SYMBOLS);
  // symbols === null is the degraded, still-useful mode: cashtags only.
  matcher = buildMatcher(res?.ok ? res.data.symbols : null);

  running = true;
  observer = new MutationObserver(scheduleScan);
  scan();
  connectObserver();
  window.addEventListener("popstate", scheduleScan);
}

function stop() {
  running = false;
  clearTimeout(scanTimer);
  observer?.disconnect();
  observer = null;
  window.removeEventListener("popstate", scheduleScan);
  card.hide();
  badgesByTicker.clear();
  budget.used = 0;
  teardown();
}

// React to the popup's per-site switch without needing a reload.
ext.storage.onChanged.addListener((changes, area) => {
  if (area !== "sync") return;
  if (!changes.sites && !changes.overlayEnabled) return;
  send(MSG.SITE_ENABLED, { host: location.hostname }).then((gate) => {
    const enabled = gate?.ok && gate.data.enabled;
    if (enabled && !running) start();
    else if (!enabled && running) stop();
  });
});

start();
