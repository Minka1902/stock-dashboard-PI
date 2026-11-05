// One shared EventSource for the whole tab (GET /api/stream), open while
// anything listens and the tab is visible: browsers allow ~6 HTTP/1.1
// connections per origin, and every stock page opens in its own tab, so a
// stream per background tab would starve ordinary requests. The server ends
// each stream after a few minutes and on restarts; reconnects back off
// 1s → 30s and reset once the server says hello.
import { streamUrl } from "../api";

const MAX_BACKOFF_MS = 30_000;

const listeners = new Map();
const statusListeners = new Set();
let source = null;
let attached = new Set();
let timer = null;
let retry = 0;
let connected = false;

function setConnected(value) {
  if (connected === value) return;
  connected = value;
  statusListeners.forEach((fn) => fn(value));
}

function dispatch(e) {
  let data = {};
  try {
    data = JSON.parse(e.data);
  } catch {
    return;
  }
  listeners.get(e.type)?.forEach((fn) => fn(data));
}

function attach(kind) {
  if (!source || attached.has(kind)) return;
  source.addEventListener(kind, dispatch);
  attached.add(kind);
}

function wanted() {
  return statusListeners.size > 0 || [...listeners.values()].some((s) => s.size > 0);
}

function open() {
  timer = null;
  source = new EventSource(streamUrl(), { withCredentials: true });
  attached = new Set();
  source.addEventListener("hello", () => { retry = 0; setConnected(true); });
  listeners.forEach((_, kind) => attach(kind));
  source.onerror = () => {
    source.close();
    source = null;
    setConnected(false);
    if (!wanted() || document.hidden) return;
    timer = setTimeout(open, Math.min(MAX_BACKOFF_MS, 1000 * 2 ** retry));
    retry += 1;
  };
}

function ensureOpen() {
  if (!source && !timer && !document.hidden && typeof EventSource !== "undefined") open();
}

function close() {
  clearTimeout(timer);
  timer = null;
  source?.close();
  source = null;
  setConnected(false);
}

function closeIfIdle() {
  if (!wanted()) close();
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) close();
  else if (wanted()) {
    retry = 0;
    ensureOpen();
  }
});

/** Call `fn(data)` for every `kind` event; returns the unsubscribe. */
export function subscribe(kind, fn) {
  if (!listeners.has(kind)) listeners.set(kind, new Set());
  listeners.get(kind).add(fn);
  ensureOpen();
  attach(kind);
  return () => {
    listeners.get(kind).delete(fn);
    closeIfIdle();
  };
}

/** Call `fn(connected)` now and on every change; returns the unsubscribe. */
export function onStatus(fn) {
  statusListeners.add(fn);
  ensureOpen();
  fn(connected);
  return () => {
    statusListeners.delete(fn);
    closeIfIdle();
  };
}
