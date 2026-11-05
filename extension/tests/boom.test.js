import { test } from "node:test";
import assert from "node:assert/strict";

import { convictionTier, activeChips, CHIP_META } from "../src/lib/boom.js";

// These thresholds are copied from frontend/src/components/BoomScorePanel.jsx:42-48.
// Pinning the exact boundaries is what catches a silent drift between the two copies.
test("conviction tiers break at 76 / 51 / 26 / 0", () => {
  assert.equal(convictionTier(100).label, "Strong Setup");
  assert.equal(convictionTier(76).label, "Strong Setup");
  assert.equal(convictionTier(75).label, "High Conviction");
  assert.equal(convictionTier(51).label, "High Conviction");
  assert.equal(convictionTier(50).label, "Interesting");
  assert.equal(convictionTier(26).label, "Interesting");
  assert.equal(convictionTier(25).label, "Watching");
  assert.equal(convictionTier(0).label, "Watching");
  assert.equal(convictionTier(-1).label, "Bearish Signals");
  assert.equal(convictionTier(-90).label, "Bearish Signals");
});

test("activeChips reads the persisted component booleans", () => {
  const row = { ticker: "NVDA", golden_cross: true, death_cross: false, wsb_rising: true };
  const keys = activeChips(row).map((c) => c.key);
  assert.deepEqual(keys.sort(), ["golden_cross", "wsb_rising"]);
});

test("activeChips puts bullish signals before bearish ones", () => {
  const row = { extreme_greed: true, golden_cross: true };
  assert.deepEqual(activeChips(row).map((c) => c.tone), ["bull", "bear"]);
});

test("activeChips respects the limit and handles a missing row", () => {
  const row = { golden_cross: true, wsb_rising: true, near_52w_high: true, macd_crossover: true, rsi_recovery: true };
  assert.equal(activeChips(row, 3).length, 3);
  assert.deepEqual(activeChips(null), []);
});

test("every chip carries the fields the UI renders", () => {
  for (const [key, meta] of Object.entries(CHIP_META)) {
    assert.ok(meta.label, `${key} needs a label`);
    assert.ok(["bull", "bear"].includes(meta.tone), `${key} has an odd tone`);
    assert.ok(["S", "M", "L"].includes(meta.horizon), `${key} has an odd horizon`);
    assert.ok(meta.tip, `${key} needs a tip — signals must explain themselves`);
  }
});
