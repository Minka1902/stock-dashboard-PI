// Standalone API client for the extension.
//
// Deliberately NOT an import of frontend/src/api.js, which is coupled to two
// things that don't exist here:
//   * import.meta.env.VITE_API_BASE (frontend/src/api.js:4) — the extension's base
//     URL is user-configured and lives in chrome.storage.sync instead.
//   * window.dispatchEvent("auth:expired") on 401 (frontend/src/api.js:40-41) —
//     there is no `window` in an MV3 service worker, so that line would throw
//     inside the alert poller.
//
// The parts worth keeping are mirrored: the 20s abort timeout and the
// ApiError { status } contract, where status 0 means "never reached the server".
//
// Only the background context calls this. Requests from a content script would
// carry the visited page's origin and be blocked by CORS; requests from the
// background carry the extension's origin, which both Chrome and Firefox treat
// as first-party for hosts in host_permissions — that is also what lets the
// SameSite=Lax session cookie ride along.

import { ext } from "./browser.js";
import { getSettings, normalizeBase, originPattern } from "./storage.js";

const REQUEST_TIMEOUT_MS = 20000;

/** status 0 = network/timeout, -1 = host permission not granted. */
export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export const PERMISSION_DENIED = -1;

export async function resolveBase() {
  const { apiBase } = await getSettings();
  return normalizeBase(apiBase);
}

async function hasPermission(base) {
  try {
    return await ext.permissions.contains({ origins: [originPattern(base)] });
  } catch {
    // Firefox throws on some patterns rather than returning false.
    return false;
  }
}

async function request(path, { method = "GET", body } = {}) {
  const base = await resolveBase();
  if (!base) throw new ApiError("no dashboard URL configured", PERMISSION_DENIED);
  // Without the host permission the request would fail as an opaque CORS error;
  // checking first turns that into an actionable "grant access" state.
  if (!(await hasPermission(base))) {
    throw new ApiError(`no access granted for ${base}`, PERMISSION_DENIED);
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let res;
  try {
    res = await fetch(`${base}${path}`, {
      method,
      credentials: "include", // the dashboard's httpOnly session cookie
      cache: "no-store",
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
  } catch (e) {
    throw new ApiError(
      e.name === "AbortError" ? `request timed out: ${path}` : e.message || `network error: ${path}`,
      0,
    );
  } finally {
    clearTimeout(timer);
  }

  if (!res.ok) {
    const detail = (await res.json().catch(() => ({})))?.detail;
    throw new ApiError(detail || `request failed: ${path}`, res.status);
  }
  return res.json();
}

// --- endpoints the extension actually uses -------------------------------

/**
 * Public endpoint (it is in the backend's _PUBLIC_PATHS), so it answers 200 even
 * when signed out. That is what lets the options page tell "backend unreachable"
 * apart from "backend fine, you're logged out".
 */
export const getHealth = () => request("/api/health");
export const getMe = () => request("/api/auth/me");
export const getAlerts = () => request("/api/alerts");
export const markAlertsRead = (payload) =>
  request("/api/alerts/read", { method: "POST", body: payload });
export const getBoomScores = () => request("/api/boom-scores");
export const getQuotes = () => request("/api/quotes");
export const getWatchlist = () => request("/api/watchlist");
export const addWatch = (ticker) =>
  request("/api/watchlist", { method: "POST", body: { ticker } });
export const getCompanyNames = () => request("/api/company-names");
export const analyze = (ticker) => request(`/api/analyze/${encodeURIComponent(ticker)}`);
