// The main window: health-gated load, and the navigation policy.

import { BrowserWindow, net, session, shell } from "electron";

import {
  APP_ORIGIN, HEALTH_INTERVAL_MS, HEALTH_TIMEOUT_MS, ICON, PRELOAD, WAITING_PAGE,
} from "./config.js";

export const isAppUrl = (url) => {
  try { return new URL(url).origin === APP_ORIGIN; } catch { return false; }
};

const isOauthStart = (url) => {
  try {
    const u = new URL(url);
    return u.origin === APP_ORIGIN && /^\/api\/auth\/oauth\/[a-z]+\/start$/.test(u.pathname);
  } catch { return false; }
};

const openExternal = (url) => {
  try {
    const p = new URL(url).protocol;
    if (p === "http:" || p === "https:") shell.openExternal(url);
  } catch { /* not a url we can open */ }
};

/**
 * /api/health is public (_PUBLIC_PATHS in app/main.py) and returns 200 even
 * when degraded — by design, so a degraded subsystem doesn't read as "failed
 * to start". So gate on the status CODE only; `status` is reported separately.
 */
export async function probeHealth() {
  try {
    const res = await net.fetch(`${APP_ORIGIN}/api/health`, {
      session: session.defaultSession,
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

export async function waitForHealth({ signal } = {}) {
  const deadline = Date.now() + HEALTH_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (signal?.aborted) return null;
    const health = await probeHealth();
    if (health) return health;
    await new Promise((r) => setTimeout(r, HEALTH_INTERVAL_MS));
  }
  return null;
}

export function createMainWindow({ show = true } = {}) {
  const win = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 900,
    minHeight: 600,
    show,
    icon: ICON,
    backgroundColor: "#12101c", // matches the Iris Dusk theme; avoids a white flash
    autoHideMenuBar: true,
    title: "Signal",
    webPreferences: {
      preload: PRELOAD,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  applyNavigationPolicy(win.webContents);
  return win;
}

/**
 * Three rules, each earning its place against a real call site in the app:
 *
 *  - window.open to the app origin -> a real child window. openTickerTab() in
 *    frontend/src/lib/nav.js opens /stock/TICKER that way, and the PDF report
 *    link needs a real window for window.print() to work.
 *  - window.open elsewhere -> the user's actual browser. News headlines, SEC
 *    and USAspending links are all target="_blank" externals, and a chromeless
 *    Electron window with no address bar is the wrong place for them.
 *  - navigation to an OAuth /start -> a popup, because the redirect chain must
 *    come back to the app origin to set the session cookie in this jar.
 */
export function applyNavigationPolicy(wc, { onOauth } = {}) {
  wc.setWindowOpenHandler(({ url }) => {
    if (isAppUrl(url)) {
      return {
        action: "allow",
        overrideBrowserWindowOptions: {
          width: 1280,
          height: 900,
          icon: ICON,
          autoHideMenuBar: true,
          backgroundColor: "#12101c",
          webPreferences: {
            preload: PRELOAD,
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: true,
          },
        },
      };
    }
    openExternal(url);
    return { action: "deny" };
  });

  wc.on("will-navigate", (event, url) => {
    if (isOauthStart(url) && onOauth) {
      event.preventDefault();
      onOauth(url);
      return;
    }
    if (isAppUrl(url)) return; // same-origin SPA navigation, and the report
                               // download link (Content-Disposition: attachment
                               // turns it into a save dialog, which is correct)
    event.preventDefault();
    openExternal(url);
  });

  // Child windows inherit nothing, so give them the same policy.
  wc.on("did-create-window", (child) => applyNavigationPolicy(child.webContents, { onOauth }));
}

export { WAITING_PAGE };
