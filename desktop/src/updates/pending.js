// What the launch-time update check should offer, from a
// GET /api/update/status payload (backend/app/routes_update.py::_payload).
// Pure, so it is unit-tested without Electron.

import { STALE_RUN_MS } from "../config.js";

export const ADMIN_ONLY_REASON = "Only an administrator can apply it (Info → Updates).";
export const FALLBACK_BLOCKED_REASON = "It can't be applied to this install.";

/** Mirrors updater.is_apply_running: a stale "running" file is a dead updater. */
export function isApplyRunning(apply, now = Date.now()) {
  if (apply?.state !== "running") return false;
  const updated = Date.parse(apply.updated_at || apply.started_at);
  return Number.isFinite(updated) && now - updated < STALE_RUN_MS;
}

/**
 * @returns {null
 *   | {kind: "apply", behind: number, latestVersion: string|null, commits: object[]}
 *   | {kind: "blocked", behind: number, reason: string}}
 */
export function pendingUpdate(payload, now = Date.now()) {
  const check = payload?.check;
  if (!check?.ok || !check.update_available) return null;
  if (isApplyRunning(payload.apply, now)) return null;

  const behind = Number(check.behind) || 0;
  if (!payload.viewer_is_admin) return { kind: "blocked", behind, reason: ADMIN_ONLY_REASON };
  if (!check.can_apply) {
    return { kind: "blocked", behind, reason: check.blocked_reason || FALLBACK_BLOCKED_REASON };
  }
  return {
    kind: "apply",
    behind,
    latestVersion: check.latest_version && check.latest_version !== check.current_version
      ? check.latest_version : null,
    commits: check.commits ?? [],
  };
}
