// "Which alerts are new to me?" — pure logic, unit tested.
//
// GET /api/alerts returns the 100 newest rows with no cursor, and the rows are
// GLOBAL: every user on the backend sees the same alert stream, with only `read`
// differing per user (backend db.py get_alerts). So the server cannot tell us
// what's new since our last poll — the client has to remember.
//
// We keep a FIFO of seen dedup_keys, capped well above the server's page size so
// a key can never be evicted while the server would still return it.

export const SEEN_CAP = 500;

/** Alerts whose dedup_key we have never seen before, newest-first order preserved. */
export function diffNew(seenKeys, alerts) {
  const seen = new Set(seenKeys || []);
  return (alerts || []).filter((a) => a?.dedup_key && !seen.has(a.dedup_key));
}

/**
 * Append keys to the FIFO and trim to `cap`, dropping the oldest.
 * Existing keys are moved to the end so an alert that keeps being returned stays warm.
 */
export function pushSeen(seenKeys, keys, cap = SEEN_CAP) {
  const incoming = new Set(keys || []);
  const kept = (seenKeys || []).filter((k) => !incoming.has(k));
  const merged = [...kept, ...incoming];
  return merged.length > cap ? merged.slice(merged.length - cap) : merged;
}

/**
 * Recover the ticker from a dedup_key.
 * Keys are built as `type|TICKER|date` in backend/app/alerts.py, but nothing
 * guarantees that shape forever — callers must handle null.
 */
export function tickerFromKey(key) {
  const parts = String(key || "").split("|");
  const candidate = parts[1];
  return /^[A-Z0-9.-]{1,10}$/.test(candidate || "") ? candidate : null;
}

/** Badge text for an unread count: "" when zero, "99+" past two digits. */
export function badgeText(unread) {
  const n = Number(unread) || 0;
  if (n <= 0) return "";
  return n > 99 ? "99+" : String(n);
}
