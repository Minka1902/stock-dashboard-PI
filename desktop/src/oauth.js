// The OAuth popup.
//
// Sign-in links render as a plain <a href> to /api/auth/oauth/<p>/start, which
// 302s out to the provider. Left alone in the main window that is a one-way
// trip to github.com with no way back, so the flow gets its own window.
//
// Critically it uses the DEFAULT session: the oauth_state cookie set by
// routes_oauth.py and the session cookie set on callback must land in the same
// jar the main window reads, or the round trip silently loses the login.

import { BrowserWindow } from "electron";

import { APP_ORIGIN, ICON, PRELOAD } from "./config.js";

// Google returns disallowed_useragent for OAuth in embedded browsers, and
// Electron's default UA carries an "Electron/x.y.z" token. This is the standard
// mitigation. If it ever stops working, email + password + TOTP still does.
const CHROME_UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";

export function openOauthWindow(parent, startUrl, onDone) {
  const win = new BrowserWindow({
    width: 520,
    height: 720,
    parent,
    modal: false,
    icon: ICON,
    autoHideMenuBar: true,
    title: "Sign in",
    webPreferences: {
      preload: PRELOAD,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      // No `partition` on purpose — default session, same cookie jar.
    },
  });

  win.webContents.setUserAgent(CHROME_UA);

  let settled = false;
  const settle = (url) => {
    if (settled) return;
    let u;
    try { u = new URL(url); } catch { return; }
    // Still mid-flow while we're at the provider, or on our own oauth routes.
    if (u.origin !== APP_ORIGIN) return;
    if (u.pathname.startsWith("/api/auth/oauth/")) return;

    // Back on the app origin => the callback ran and set the session cookie
    // (or bounced to /?oauth_error=..., which is also app-origin and also
    // means "done"). routes_oauth.py redirects to CORS_ORIGINS[0].
    settled = true;
    if (!win.isDestroyed()) win.destroy();
    onDone();
  };

  win.webContents.on("will-redirect", (_e, url) => settle(url));
  win.webContents.on("did-navigate", (_e, url) => settle(url));
  win.on("closed", () => { if (!settled) onDone(); });

  win.loadURL(startUrl);
  return win;
}
