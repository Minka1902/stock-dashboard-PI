// Promise wrappers over extension storage, plus the settings/cache schema.
//
// `sync` holds user settings (small, roams between profiles). `local` holds
// caches and poller bookkeeping (larger, device-specific, must not roam).
//
// MV3 service workers are torn down whenever they go idle, so NOTHING may live in
// a module-level variable across events. Every cache here is storage-backed.

import { ext } from "./browser.js";

export const DEFAULTS = {
  apiBase: "http://localhost:8000",
  pollMinutes: 3, // matches the backend's 180s refresh cadence
  notifyHighSeverity: true,
  overlayEnabled: true,
  theme: "dark",
  // Per-host overlay switches, keyed by hostname: { "x.com": false }.
  // Absent means enabled.
  sites: {},
};

export async function getSettings() {
  const got = await ext.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...got };
}

export async function setSettings(patch) {
  await ext.storage.sync.set(patch);
}

export async function getLocal(keys) {
  return ext.storage.local.get(keys);
}

export async function setLocal(patch) {
  return ext.storage.local.set(patch);
}

/**
 * Read a TTL-bounded cache entry, or null when missing/stale.
 * Entries are stored as { v: value, t: epochMillis }.
 */
export async function readCache(key, ttlMs) {
  const got = await getLocal(key);
  const entry = got?.[key];
  if (!entry || typeof entry.t !== "number") return null;
  if (Date.now() - entry.t > ttlMs) return null;
  return entry.v;
}

export async function writeCache(key, value) {
  await setLocal({ [key]: { v: value, t: Date.now() } });
}

/**
 * Normalize a user-entered dashboard URL into a bare origin.
 * Returns null when the input isn't a usable http(s) origin — this is the guard
 * that keeps `javascript:` and friends out of the API client and out of tabs.create.
 */
export function normalizeBase(input) {
  const raw = String(input ?? "").trim();
  if (!raw) return null;
  let url;
  try {
    url = new URL(raw.includes("://") ? raw : `http://${raw}`);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  return url.origin;
}

/** The host-permission pattern the API client needs for a given base origin. */
export function originPattern(base) {
  return `${base}/*`;
}

/** Deep link into the dashboard's standalone analyze tab (frontend/src/lib/nav.js). */
export function stockUrl(base, ticker) {
  return `${base}/#/stock/${encodeURIComponent(ticker)}`;
}
