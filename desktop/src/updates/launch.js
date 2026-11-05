// The update check that runs once per launch.
//
// It drives the backend's existing updater (app/updater.py + windows/update.ps1)
// rather than pulling anything itself: the service runs the update as
// LocalSystem, so there is no UAC prompt, and pip / the frontend build / the
// restart / the rollback all come for free.

import { dialog } from "electron";

import { apiGet, apiPost } from "../api.js";
import { STALE_RUN_MS, UPDATE_POLL_MS } from "../config.js";
import { probeHealth } from "../window.js";
import { pendingUpdate } from "./pending.js";

const MAX_LISTED_COMMITS = 5;
const TITLE = "Signal update";

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** A hidden parent (launched --hidden at login) would hide the dialog too. */
function showBox(win, options) {
  const opts = { title: TITLE, noLink: true, ...options };
  return win?.isVisible() ? dialog.showMessageBox(win, opts) : dialog.showMessageBox(opts);
}

function describe(update) {
  const listed = update.commits.slice(0, MAX_LISTED_COMMITS).map((c) => `• ${c.subject}`);
  const more = update.behind - listed.length;
  if (more > 0) listed.push(`…and ${more} more`);
  listed.push("", "The backend restarts during the update and this window reloads when it's done.");
  return listed.join("\n");
}

/**
 * Ask GitHub (through the backend) whether main has moved, and offer to update.
 *
 * @returns {Promise<{status: "retry"|"done"|"applied", commit?: string}>}
 *   "retry" while signed out or unreachable, so the caller tries again later;
 *   "applied" carries the commit the update started from.
 */
export async function checkForUpdate(win) {
  let payload;
  try {
    payload = await apiGet("/api/update/status?refresh=true");
  } catch (err) {
    if (err.status === 401 || err.status === undefined) return { status: "retry" };
    console.error("update check failed:", err);
    return { status: "done" };
  }

  const update = pendingUpdate(payload);
  if (!update) return { status: "done" };

  if (update.kind === "blocked") {
    await showBox(win, {
      type: "info",
      message: `An update is available (${plural(update.behind, "new commit")}).`,
      detail: update.reason,
      buttons: ["OK"],
    });
    return { status: "done" };
  }

  const version = update.latestVersion ? ` — v${update.latestVersion}` : "";
  const { response } = await showBox(win, {
    type: "info",
    message: `An update is available: ${plural(update.behind, "new commit")}${version}.`,
    detail: describe(update),
    buttons: ["Update now", "Later"],
    defaultId: 0,
    cancelId: 1,
  });
  if (response !== 0) return { status: "done" };

  try {
    await apiPost("/api/update/apply");
  } catch (err) {
    await showBox(win, {
      type: "error",
      message: "The update could not start.",
      detail: err.message,
      buttons: ["OK"],
    });
    return { status: "done" };
  }
  return { status: "applied", commit: payload.current?.commit };
}

/**
 * Wait for the restarted backend to report a commit other than `previous`.
 *
 * @returns {Promise<boolean>} true on a new commit; false when the updater
 *   failed or rolled back (the Info → Updates page shows why) or went stale.
 */
export async function waitForNewCommit(previous) {
  const deadline = Date.now() + STALE_RUN_MS;
  while (Date.now() < deadline) {
    await sleep(UPDATE_POLL_MS);
    const health = await probeHealth();
    if (!health) continue; // restarting
    if (previous && health.commit && health.commit !== previous) return true;
    const state = await apiGet("/api/update/status").then((p) => p?.apply?.state, () => null);
    if (state === "failed" || state === "rolled_back") return false;
  }
  return false;
}
