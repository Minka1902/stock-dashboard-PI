// Routing: real History API paths (/news, /stock/NVDA), no hash.
//
// Deliberately hand-rolled rather than react-router. The whole surface is a flat
// list of views plus one parameterised route; a router library would buy nested
// routes, loaders and data APIs that this app does not use, in exchange for
// restructuring App.jsx around <Routes>. The server side already cooperates:
// the SPA catch-all in app/main.py returns index.html for any non-/api path.

import { DEFAULT_VIEW, isViewKey } from "./routes";

const STOCK_SEGMENT = "stock";
const TICKER_RE = /^[A-Za-z0-9.-]{1,10}$/;

/** Legacy hash form, kept solely so old tabs and bookmarks can be migrated. */
export const STOCK_HASH_RE = /^#\/stock\/([A-Za-z0-9.-]{1,10})$/;

/** Fired after a programmatic navigation; pushState/replaceState emit no event. */
const NAV_EVENT = "app:navigate";

/**
 * Parse a location into a route.
 *
 * `from` is the view the user navigated out of, and is present only when the
 * stock page was opened in this tab. Its absence is how `leaveStock` knows the
 * page owns a tab of its own and should close it rather than navigate.
 *
 * `tab` selects a sub-view inside a page (e.g. /trading-bot?tab=risk); the page
 * owns its list of tabs and falls back to its first one for an unknown value.
 *
 * @returns {{kind: "stock", ticker: string, alertKey: string|null, from: string|null}
 *          |{kind: "view", view: string, known: boolean, tab: string|null}}
 */
export function parseRoute(pathname = window.location.pathname, search = window.location.search) {
  const segments = pathname.split("/").filter(Boolean);
  const params = new URLSearchParams(search);

  if (segments[0] === STOCK_SEGMENT && segments[1]) {
    const raw = decodeURIComponent(segments[1]);
    if (TICKER_RE.test(raw)) {
      const from = params.get("from");
      return {
        kind: "stock",
        ticker: raw.toUpperCase(),
        alertKey: params.get("alert"),
        from: from && isViewKey(from) ? from : null,
      };
    }
  }

  const tab = params.get("tab");
  if (segments.length === 0) return { kind: "view", view: DEFAULT_VIEW, known: true, tab };
  const view = segments[0];
  // `known: false` lets the app rewrite a junk URL to "/" instead of rendering
  // a blank screen for a typo'd path.
  return { kind: "view", view: isViewKey(view) ? view : DEFAULT_VIEW, known: isViewKey(view), tab };
}

/** Build the path for a route. The inverse of parseRoute. */
export function routeToPath(route) {
  if (typeof route === "string") return route.startsWith("/") ? route : `/${route}`;
  if (route.kind === "stock") {
    const params = new URLSearchParams();
    if (route.alertKey) params.set("alert", route.alertKey);
    if (route.from) params.set("from", route.from);
    const q = params.toString();
    return `/${STOCK_SEGMENT}/${encodeURIComponent(route.ticker)}${q ? `?${q}` : ""}`;
  }
  const q = route.tab ? `?tab=${encodeURIComponent(route.tab)}` : "";
  return `${route.view === DEFAULT_VIEW ? "/" : `/${route.view}`}${q}`;
}

/** Navigate in the current tab and tell subscribers. */
export function navigateTo(route, { replace = false } = {}) {
  const path = routeToPath(route);
  if (path === window.location.pathname + window.location.search) return;
  if (replace) window.history.replaceState(null, "", path);
  else window.history.pushState(null, "", path);
  window.dispatchEvent(new Event(NAV_EVENT));
}

/**
 * Open a ticker's analysis view in a new tab. Called from direct user gestures
 * (clicks) so popup blockers don't interfere.
 *
 * Deliberately NOT "noopener": a tab may only close itself if it is
 * script-closable, and severing the opener relationship also gave up that
 * right — which is why an earlier attempt at self-closing silently did
 * nothing. The target is same-origin and built here, so there is no
 * reverse-tabnabbing exposure to trade away. No `from` param is set; its
 * absence is what marks this page as owning its tab (see `leaveStock`).
 */
export function openTickerTab(ticker) {
  window.open(routeToPath({ kind: "stock", ticker }), "_blank");
}

/**
 * Leave the stock page the way it was entered.
 *
 * Opened in this tab (`from` is set) → go back to that view. Opened as its own
 * tab → close the tab. `window.close()` is silently ignored when the browser
 * refuses, so fall back to the dashboard rather than stranding the user on a
 * page whose only exit did nothing.
 */
export function leaveStock(from, fallback = { kind: "view", view: DEFAULT_VIEW }) {
  if (from && isViewKey(from)) {
    navigateTo({ kind: "view", view: from }, { replace: true });
    return;
  }
  window.close();
  // Still here a tick later means close() was refused.
  window.setTimeout(() => navigateTo(fallback, { replace: true }), 100);
}

/**
 * Rewrite a legacy #/stock/TICKER URL to /stock/TICKER.
 *
 * Called once before first paint. Without it, every bookmark and still-open tab
 * from the hash era would land on the dashboard instead of its stock.
 */
export function migrateHashUrl() {
  const match = STOCK_HASH_RE.exec(window.location.hash);
  if (!match) return false;
  window.history.replaceState(
    null, "", routeToPath({ kind: "stock", ticker: match[1].toUpperCase() }));
  return true;
}

// ---- useSyncExternalStore plumbing ----

export function subscribeToRoute(callback) {
  window.addEventListener("popstate", callback);
  window.addEventListener(NAV_EVENT, callback);
  return () => {
    window.removeEventListener("popstate", callback);
    window.removeEventListener(NAV_EVENT, callback);
  };
}

/**
 * A string, not a parsed object, on purpose: getSnapshot must return an
 * Object.is-stable value while the store is unchanged, and a fresh object every
 * call would re-render forever.
 */
export function getRouteSnapshot() {
  return window.location.pathname + window.location.search;
}
