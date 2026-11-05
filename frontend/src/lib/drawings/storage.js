import { getDrawings, saveDrawings } from "../../api";

/**
 * Drawings persist locally first, then sync to the server.
 *
 * Writing to localStorage synchronously means drawing never blocks on the
 * network and nothing is lost if the request fails — the local copy stands and
 * is retried on the next load. When both copies exist the newer `updated_at`
 * wins, so a drawing made on another device isn't clobbered by a stale cache.
 */

const key = (ticker) => `drawings:${String(ticker || "").toUpperCase()}`;

export function readLocal(ticker) {
  try {
    const raw = localStorage.getItem(key(ticker));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed?.shapes)) return null;
    return parsed; // { shapes, updated_at, dirty }
  } catch {
    return null; // private mode or corrupt blob
  }
}

export function writeLocal(ticker, shapes, { dirty = false, updatedAt = null } = {}) {
  try {
    localStorage.setItem(key(ticker), JSON.stringify({
      shapes,
      updated_at: updatedAt || new Date().toISOString(),
      dirty,
    }));
  } catch { /* storage full or blocked — the in-memory copy still works */ }
}

/**
 * Load a ticker's drawings, preferring whichever copy is newer. A pending local
 * copy (dirty: the last server write failed) always wins and is re-pushed.
 */
export async function loadDrawings(ticker) {
  const local = readLocal(ticker);
  let remote;
  try {
    remote = await getDrawings(ticker);
  } catch {
    // offline or server down — local is all we have, and that's fine
    return { shapes: local?.shapes || [], synced: false };
  }

  if (!local) return { shapes: remote.shapes || [], synced: true };

  if (local.dirty) {
    // We never managed to push this; do it now, keeping the local copy either way.
    const ok = await pushDrawings(ticker, local.shapes);
    return { shapes: local.shapes, synced: ok };
  }

  const localAt = Date.parse(local.updated_at) || 0;
  const remoteAt = Date.parse(remote.updated_at) || 0;
  if (localAt > remoteAt) {
    const ok = await pushDrawings(ticker, local.shapes);
    return { shapes: local.shapes, synced: ok };
  }
  writeLocal(ticker, remote.shapes || [], { updatedAt: remote.updated_at });
  return { shapes: remote.shapes || [], synced: true };
}

/** Push to the server. Returns false (and marks the local copy dirty) on failure. */
export async function pushDrawings(ticker, shapes) {
  try {
    const saved = await saveDrawings(ticker, shapes);
    writeLocal(ticker, shapes, { updatedAt: saved.updated_at });
    return true;
  } catch {
    writeLocal(ticker, shapes, { dirty: true });
    return false;
  }
}

/** Save: local immediately (never blocks), server best-effort. */
export function persistDrawings(ticker, shapes) {
  writeLocal(ticker, shapes, { dirty: true });
  return pushDrawings(ticker, shapes);
}
