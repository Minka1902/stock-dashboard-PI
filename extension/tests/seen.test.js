import { test } from "node:test";
import assert from "node:assert/strict";

import { diffNew, pushSeen, tickerFromKey, badgeText, SEEN_CAP } from "../src/background/seen.js";

const alert = (key, extra = {}) => ({ dedup_key: key, ...extra });

test("everything is new against an empty seen set", () => {
  const out = diffNew([], [alert("a"), alert("b")]);
  assert.deepEqual(out.map((a) => a.dedup_key), ["a", "b"]);
});

test("nothing is new when all keys are seen", () => {
  assert.deepEqual(diffNew(["a", "b"], [alert("a"), alert("b")]), []);
});

test("partial overlap returns only the unseen keys", () => {
  const out = diffNew(["a"], [alert("a"), alert("b"), alert("c")]);
  assert.deepEqual(out.map((a) => a.dedup_key), ["b", "c"]);
});

test("alerts without a dedup_key are ignored rather than treated as new", () => {
  assert.deepEqual(diffNew([], [{ ticker: "AAPL" }]), []);
});

test("pushSeen appends new keys", () => {
  assert.deepEqual(pushSeen(["a"], ["b", "c"]), ["a", "b", "c"]);
});

test("pushSeen de-duplicates and moves repeats to the end", () => {
  assert.deepEqual(pushSeen(["a", "b"], ["a"]), ["b", "a"]);
});

test("pushSeen evicts oldest first at the cap", () => {
  const start = Array.from({ length: SEEN_CAP }, (_, i) => `k${i}`);
  const out = pushSeen(start, ["new"]);
  assert.equal(out.length, SEEN_CAP);
  assert.equal(out.at(-1), "new");
  assert.equal(out[0], "k1", "the oldest key should have been dropped");
});

test("the cap is comfortably above the server's 100-row page", () => {
  // If this ever drops to <=100 a key could be evicted while still returnable,
  // which would re-notify an old alert.
  assert.ok(SEEN_CAP > 100);
});

test("first-poll seeding leaves nothing new on the second poll", () => {
  const alerts = [alert("x"), alert("y")];
  const seeded = pushSeen([], alerts.map((a) => a.dedup_key));
  assert.deepEqual(diffNew(seeded, alerts), []);
});

test("tickerFromKey recovers the symbol from a backend dedup key", () => {
  assert.equal(tickerFromKey("boom_cross|NVDA|2026-07-29"), "NVDA");
  assert.equal(tickerFromKey("recommendation_change|BRK.B|2026-07-29"), "BRK.B");
});

test("tickerFromKey returns null for shapes it cannot parse", () => {
  assert.equal(tickerFromKey("garbage"), null);
  assert.equal(tickerFromKey("a|not a ticker|b"), null);
  assert.equal(tickerFromKey(""), null);
  assert.equal(tickerFromKey(null), null);
});

test("badgeText hides zero and clamps at 99+", () => {
  assert.equal(badgeText(0), "");
  assert.equal(badgeText(null), "");
  assert.equal(badgeText(7), "7");
  assert.equal(badgeText(99), "99");
  assert.equal(badgeText(100), "99+");
});
