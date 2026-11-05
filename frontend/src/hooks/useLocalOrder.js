import { useCallback, useMemo, useState } from "react";

function readOrder(key) {
  if (!key) return [];
  try {
    const raw = window.localStorage.getItem(key);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((v) => typeof v === "string") : [];
  } catch {
    return []; // corrupt entry: fall back to server order rather than crash
  }
}

/**
 * A user-defined ordering for a list, remembered in localStorage.
 *
 * Stored per key as a plain array of ids. Deliberately NOT inside the shared
 * `settings` blob: that blob is rewritten (and mirrored onto the <html> dataset)
 * on every change, and list order churns far more often than preferences do.
 *
 * Merge rules, so a saved order never hides or reorders real data:
 *  - ids still present keep their saved position;
 *  - ids we've never seen go to the FRONT, matching the server's default of
 *    newest-added-first — a newly added ticker must not vanish to the bottom;
 *  - ids that no longer exist are dropped when the order is next written.
 *
 * @param key      storage key, e.g. `watchlistOrder:v1:3`. null disables persistence.
 * @param items    the source list, in server order
 * @param getId    item -> stable id
 */
export function useLocalOrder(key, items, getId) {
  const [order, setOrder] = useState(() => readOrder(key));

  // Re-read when the active list changes, using React's documented "adjust
  // state when a prop changes" pattern. An effect would work too, but it would
  // render once with the previous list's order before correcting itself —
  // visibly reshuffling the rows on every tab switch.
  const [loadedKey, setLoadedKey] = useState(key);
  if (key !== loadedKey) {
    setLoadedKey(key);
    setOrder(readOrder(key));
  }

  const persist = useCallback((next) => {
    setOrder(next);
    if (!key) return;
    try {
      window.localStorage.setItem(key, JSON.stringify(next));
    } catch {
      // Quota or private mode — ordering just won't survive a reload.
    }
  }, [key]);

  const ordered = useMemo(() => {
    if (order.length === 0) return items;
    const byId = new Map(items.map((it) => [getId(it), it]));
    const known = [];
    for (const id of order) {
      const item = byId.get(id);
      if (item) { known.push(item); byId.delete(id); }
    }
    // Whatever the saved order didn't mention is new — keep it visible up top.
    return [...byId.values(), ...known];
  }, [items, order, getId]);

  const isCustom = order.length > 0;

  /** Persist an explicit arrangement (from a drag, or a keyboard move). */
  const setOrderedItems = useCallback((next) => {
    persist(next.map(getId));
  }, [persist, getId]);

  /** Move one item by an offset, clamped. Returns the new array, or null. */
  const moveBy = useCallback((id, delta) => {
    const ids = ordered.map(getId);
    const from = ids.indexOf(id);
    if (from < 0) return null;
    const to = Math.min(ids.length - 1, Math.max(0, from + delta));
    if (to === from) return null;
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    persist(ids);
    return ids;
  }, [ordered, getId, persist]);

  const reset = useCallback(() => {
    setOrder([]);
    if (key) {
      try { window.localStorage.removeItem(key); } catch { /* nothing to do */ }
    }
  }, [key]);

  return { ordered, isCustom, setOrderedItems, moveBy, reset };
}
