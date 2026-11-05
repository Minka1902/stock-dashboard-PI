import { useSyncExternalStore } from "react";
import { getUpdateStatus } from "../api";

// The server itself re-checks GitHub every 6h and caches for 1h, so polling
// faster than this only re-reads the same cached answer.
const POLL_MS = 30 * 60 * 1000;

/*
 * One shared store rather than per-component state: the account menu (nav
 * dot) and the Info page's Updates section both read it, and it should cost
 * one request per poll, not one per consumer.
 */
let state = { data: null, error: null, loading: false };
const listeners = new Set();
let timer = null;
let inflight = null;

function emit(patch) {
  state = { ...state, ...patch };
  listeners.forEach((l) => l());
}

/** Re-read /api/update/status. `force` makes the server `git fetch` now. */
export function refreshUpdateStatus(force = false) {
  if (inflight && !force) return inflight;
  emit({ loading: true });
  const p = getUpdateStatus(force)
    .then((data) => { emit({ data, error: null, loading: false }); return data; })
    .catch((e) => { emit({ error: e.message, loading: false }); throw e; })
    .finally(() => { if (inflight === p) inflight = null; });
  inflight = p;
  return p;
}

/** Push a payload we already have (e.g. the 202 body from apply). */
export function setUpdateStatus(data) {
  emit({ data, error: null });
}

function subscribe(listener) {
  listeners.add(listener);
  if (listeners.size === 1) {
    refreshUpdateStatus().catch(() => {});
    timer = setInterval(() => {
      if (!document.hidden) refreshUpdateStatus().catch(() => {});
    }, POLL_MS);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0) { clearInterval(timer); timer = null; }
  };
}

const snapshot = () => state;

/** { data, error, loading } — data is the /api/update/status payload. */
export function useUpdateStatus() {
  return useSyncExternalStore(subscribe, snapshot);
}

export function isUpdateAvailable(data) {
  return Boolean(data?.check?.ok && data.check.update_available);
}
