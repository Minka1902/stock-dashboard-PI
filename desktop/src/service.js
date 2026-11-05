// Reading and (indirectly) controlling the Windows service.
//
// Read is unelevated: the default service security descriptor grants
// SERVICE_QUERY_STATUS to Authenticated Users, so `sc query` works for anyone.
//
// Write is not, and is not worked around here. Loosening the service SDACL with
// `sc sdset` so an unelevated app could stop it would be a privilege-escalation
// hole in a LocalSystem service — so a restart goes through a visible UAC
// prompt instead. The menu item is labelled with an ellipsis to signal that.

import { spawn, execFile } from "node:child_process";

import { SERVICE_NAME, SERVICE_PS1 } from "./config.js";

/** @returns {Promise<"running"|"stopped"|"pending"|"absent"|"unknown">} */
export function queryService() {
  return new Promise((resolve) => {
    if (process.platform !== "win32") return resolve("absent");
    execFile("sc.exe", ["query", SERVICE_NAME], { windowsHide: true }, (err, stdout) => {
      if (err) return resolve(stdout?.includes("1060") ? "absent" : "unknown");
      const m = /STATE\s+:\s+\d+\s+(\w+)/.exec(stdout || "");
      const state = (m?.[1] || "").toUpperCase();
      if (state === "RUNNING") return resolve("running");
      if (state === "STOPPED") return resolve("stopped");
      if (state.endsWith("PENDING")) return resolve("pending");
      resolve("unknown");
    });
  });
}

/**
 * Ask Windows to restart the service, elevating via UAC.
 *
 * The outcome is unobservable from here (the elevated process is detached, and
 * the user may cancel the prompt) — callers must re-enter the health loop
 * rather than assume success.
 */
export function restartServiceElevated() {
  if (process.platform !== "win32") return;
  const inner = [
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SERVICE_PS1, "-Restart",
  ].map((a) => `'${a.replace(/'/g, "''")}'`).join(",");

  spawn("powershell.exe", [
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
    `Start-Process powershell.exe -Verb RunAs -ArgumentList ${inner}`,
  ], { windowsHide: true, detached: true, stdio: "ignore" }).unref();
}
