// Authenticated requests from the main process.
//
// Node's global fetch does NOT share Electron's cookie jar, so a plain
// fetch("/api/alerts") gets 401 forever even with an authenticated window.
// net.fetch bound to defaultSession does — and it defaults to
// credentials: "omit", so that has to be explicit too.

import { net, session } from "electron";

import { APP_ORIGIN, REQUEST_TIMEOUT_MS } from "./config.js";

async function request(method, path, body) {
  const res = await net.fetch(`${APP_ORIGIN}${path}`, {
    method,
    credentials: "include",
    session: session.defaultSession,
    cache: "no-store",
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    ...(body !== undefined && {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  });
  const payload = await res.json().catch(() => null);
  if (!res.ok) {
    // FastAPI's HTTPException detail is the user-facing reason (e.g. the 409
    // from /api/update/apply saying why an update can't run).
    const detail = typeof payload?.detail === "string" ? payload.detail : null;
    const err = new Error(detail || `${method} ${path} -> ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return payload;
}

export const apiGet = (path) => request("GET", path);
export const apiPost = (path, body) => request("POST", path, body);
