// Shared constants for the desktop shell.
//
// The window loads the BACKEND'S OWN ORIGIN, never file://. That is the
// load-bearing decision of this whole app:
//   * frontend/src/lib/nav.js uses History API paths with no hash, so
//     pushState("/stock/NVDA") throws a SecurityError under file://.
//   * vite.config.js sets no `base`, so assets emit as absolute /assets/...
//     which under file:// resolve to the filesystem root.
//   * auth is a single httpOnly SameSite=Lax cookie on the backend origin
//     (backend/app/auth.py) — there is no token in JS to relay.
// Same-origin makes all three non-issues, with zero frontend changes.
//
// 127.0.0.1 rather than "localhost" on purpose: uvicorn binds IPv4 only and
// Windows resolves localhost to ::1 first, so a localhost origin can fail to
// connect outright. They are also separate cookie jars — mixing the two
// loses the session mid-OAuth. windows/install-service.ps1 sets the service's
// STOCKS_CORS_ORIGINS and STOCKS_OAUTH_REDIRECT_BASE to match this exactly.

import path from "node:path";
import { fileURLToPath } from "node:url";

export const PORT = Number(process.env.SIGNAL_PORT) || 8000;
export const APP_ORIGIN = `http://127.0.0.1:${PORT}`;

export const SERVICE_NAME = "SignalDashboard";

/** Poll cadence for /api/alerts. Matches the extension's 3-minute default and
 *  the backend's own STOCKS_REFRESH_SECONDS (180s) recompute cycle. */
export const POLL_MS = 180_000;

/** Mirrors api.js REQUEST_TIMEOUT_MS. */
export const REQUEST_TIMEOUT_MS = 20_000;

/** Windows Action Center is even less forgiving of a burst than Chrome is. */
export const MAX_NOTIFICATIONS_PER_POLL = 3;

export const HEALTH_TIMEOUT_MS = 60_000;
export const HEALTH_INTERVAL_MS = 1_000;

/** How often the launch update check re-reads /api/health while an update runs. */
export const UPDATE_POLL_MS = 2_000;

/** Mirrors backend/app/updater.py STALE_RUN_SECONDS: a "running" update older
 *  than this is a dead updater, and the launch check stops waiting on it. */
export const STALE_RUN_MS = 30 * 60_000;

const here = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(here, "..");
export const REPO_ROOT = path.resolve(ROOT, "..");

export const PRELOAD = path.join(here, "preload.cjs");
export const WAITING_PAGE = path.join(ROOT, "renderer", "waiting.html");
export const ICON = path.join(ROOT, "assets", "icon-128.png");
export const TRAY_ICON = path.join(ROOT, "assets", "icon-32.png");

export const SERVICE_PS1 = path.join(REPO_ROOT, "windows", "service-control.ps1");
export const LOG_DIR = path.join(
  process.env.ProgramData || "C:\\ProgramData", "SignalDashboard", "logs");
