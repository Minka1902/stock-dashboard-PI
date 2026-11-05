import { test } from "node:test";
import assert from "node:assert/strict";

import { STALE_RUN_MS } from "../src/config.js";
import {
  ADMIN_ONLY_REASON, FALLBACK_BLOCKED_REASON, isApplyRunning, pendingUpdate,
} from "../src/updates/pending.js";

const NOW = Date.parse("2026-09-28T12:00:00Z");
const iso = (msAgo) => new Date(NOW - msAgo).toISOString();

const commits = [{ sha: "b2", subject: "second" }, { sha: "a1", subject: "first" }];
const payload = ({ check = {}, apply = null, admin = true } = {}) => ({
  current: { version: "0.1.0", commit: "abc1234" },
  check: {
    ok: true,
    current_version: "0.1.0",
    latest_version: "0.2.0",
    update_available: true,
    can_apply: true,
    blocked_reason: null,
    behind: 2,
    commits,
    ...check,
  },
  apply,
  viewer_is_admin: admin,
});

test("no payload or no check means nothing to offer", () => {
  assert.equal(pendingUpdate(null, NOW), null);
  assert.equal(pendingUpdate({ check: null, viewer_is_admin: true }, NOW), null);
});

test("a check that failed (git or network) is never reported as an update", () => {
  assert.equal(pendingUpdate(payload({ check: { ok: false, error: "offline" } }), NOW), null);
});

test("up to date means nothing to offer", () => {
  assert.equal(pendingUpdate(payload({ check: { update_available: false, behind: 0 } }), NOW), null);
});

test("an admin with an applicable update is offered it", () => {
  assert.deepEqual(pendingUpdate(payload(), NOW), {
    kind: "apply", behind: 2, latestVersion: "0.2.0", commits,
  });
});

test("an unchanged version number is not shown as a new version", () => {
  const update = pendingUpdate(payload({ check: { latest_version: "0.1.0" } }), NOW);
  assert.equal(update.latestVersion, null);
});

test("a non-admin is told only an admin can apply it", () => {
  assert.deepEqual(pendingUpdate(payload({ admin: false }), NOW), {
    kind: "blocked", behind: 2, reason: ADMIN_ONLY_REASON,
  });
});

test("an admin on a blocked install gets the backend's reason", () => {
  const reason = "the checkout is on branch 'dev', not main";
  assert.deepEqual(pendingUpdate(payload({ check: { can_apply: false, blocked_reason: reason } }), NOW), {
    kind: "blocked", behind: 2, reason,
  });
  assert.equal(
    pendingUpdate(payload({ check: { can_apply: false } }), NOW).reason, FALLBACK_BLOCKED_REASON);
});

test("an update already running is not offered again", () => {
  const apply = { state: "running", updated_at: iso(60_000) };
  assert.equal(pendingUpdate(payload({ apply }), NOW), null);
});

test("a finished previous update does not block the offer", () => {
  for (const state of ["done", "rolled_back", "failed"]) {
    assert.equal(pendingUpdate(payload({ apply: { state, updated_at: iso(60_000) } }), NOW).kind, "apply");
  }
});

test("a stale running file is a dead updater, like updater.is_apply_running", () => {
  assert.equal(isApplyRunning({ state: "running", updated_at: iso(STALE_RUN_MS + 1) }, NOW), false);
  assert.equal(isApplyRunning({ state: "running", started_at: iso(1_000) }, NOW), true);
  assert.equal(isApplyRunning({ state: "running" }, NOW), false);
  assert.equal(isApplyRunning(null, NOW), false);
});
