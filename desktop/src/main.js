// Signal desktop shell — main process.
//
// The backend is a Windows service (see windows/README.md); this app is a
// window plus a tray icon over it. It never starts the backend itself, and it
// survives the backend going away and coming back.

import path from "node:path";

import { Notification, app, ipcMain, session, shell } from "electron";

import { APP_ORIGIN, LOG_DIR, POLL_MS, TRAY_ICON } from "./config.js";
import { apiPost } from "./api.js";
import { AlertPoller } from "./alerts/poller.js";
import { JsonStore } from "./alerts/store.js";
import { AppTray } from "./tray.js";
import { openOauthWindow } from "./oauth.js";
import {
  applyNavigationPolicy, createMainWindow, isAppUrl, probeHealth, waitForHealth,
  WAITING_PAGE,
} from "./window.js";
import { queryService, restartServiceElevated } from "./service.js";
import { checkForUpdate, waitForNewCommit } from "./updates/launch.js";

// Required for Windows toast identity. Must be set before app is ready.
app.setAppUserModelId("com.signal.dashboard");

// A second launch (Start Menu shortcut while tray-resident) must focus the
// existing window, not start a second poller with duplicate notifications.
if (!app.requestSingleInstanceLock()) {
  app.quit();
}

const startHidden = process.argv.includes("--hidden");

let win = null;
let tray = null;
let poller = null;
let store = null;
let onAppOrigin = false;   // is the window showing the dashboard, or waiting.html?
let healthAbort = null;
let updateCheck = "pending"; // "pending" | "running" | "done" — once per launch

app.isQuitting = false;

// ---------------------------------------------------------------- lifecycle --

app.whenReady().then(async () => {
  store = new JsonStore(path.join(app.getPath("userData"), "settings.json"), {
    seenKeys: [],
    seeded: false,
    notifyHighSeverity: true,
    trayHintShown: false,
  });

  win = createMainWindow({ show: !startHidden });
  applyNavigationPolicy(win.webContents, { onOauth: handleOauth });

  win.on("close", (e) => {
    // Close means "hide", because the tray badge and the alert toasts are the
    // point of this app. Real quit goes through the tray menu.
    if (app.isQuitting) return;
    e.preventDefault();
    win.hide();
    if (!store.get("trayHintShown")) {
      store.set({ trayHintShown: true });
      if (Notification.isSupported()) {
        new Notification({
          title: "Signal is still running",
          body: "It lives in the tray. Right-click the icon to quit.",
          icon: TRAY_ICON,
        }).show();
      }
    }
  });

  // A hard navigation failure means the backend went away mid-session. The
  // in-app "Backend unreachable" banner covers transient fetch failures while
  // the document stays loaded; this is for when the document itself is gone.
  win.webContents.on("did-fail-load", (_e, code, desc, url, isMainFrame) => {
    if (!isMainFrame) return;
    if (code === -3) return; // ERR_ABORTED — a navigation we cancelled ourselves
    if (!isAppUrl(url)) return;
    console.error("main frame failed to load:", code, desc, url);
    void showWaitingThenLoad();
  });

  tray = new AppTray({
    onShowWindow: showWindow,
    onToggleWindow: () => (win.isVisible() && win.isFocused() ? win.hide() : showWindow()),
    onNavigate: navigate,
    onMarkAllRead: markAllRead,
    onRestartService: handleRestartService,
    onOpenLogs: () => shell.openPath(LOG_DIR),
    onQuit: () => { app.isQuitting = true; app.quit(); },
    getOpenAtLogin: () => app.getLoginItemSettings().openAtLogin,
    onSetOpenAtLogin: (openAtLogin) => {
      app.setLoginItemSettings({ openAtLogin, args: ["--hidden"] });
      tray.render();
    },
  });

  // Tokens rotate on every auth state upgrade (auth.py), so a new cookie is
  // the main process's only signal that the user just signed in — the SPA's
  // login flow changes no URL.
  session.defaultSession.cookies.on("changed", (_e, _cookie, _cause, removed) => {
    if (!removed) void checkUpdateOnce();
  });

  poller = new AlertPoller(store, onPollState, navigate);

  await showWaitingThenLoad();
  poller.start();
  void refreshServiceState();
  setInterval(refreshServiceState, POLL_MS);
});

app.on("second-instance", () => showWindow());

// Do NOT quit when the window closes — the tray and the poller are the app.
app.on("window-all-closed", () => {});

app.on("before-quit", () => {
  app.isQuitting = true;
  poller?.stop();
  tray?.destroy();
});

// ------------------------------------------------------------------ startup --

/**
 * Show the local waiting page, then swap to the dashboard once the backend
 * answers. waiting.html is file:// and makes no API calls, so it never touches
 * cookies or CORS — the file:// limitations that rule out serving the SPA this
 * way don't apply to a static page.
 */
async function showWaitingThenLoad() {
  onAppOrigin = false;
  healthAbort?.abort();
  healthAbort = new AbortController();

  await win.loadFile(WAITING_PAGE);
  send("desktop:health", { state: "starting" });

  const health = await waitForHealth({ signal: healthAbort.signal });
  if (healthAbort.signal.aborted) return;

  if (!health) {
    send("desktop:health", {
      state: "down",
      service: await queryService(),
      origin: APP_ORIGIN,
      logDir: LOG_DIR,
    });
    return;
  }

  await win.loadURL(APP_ORIGIN);
  onAppOrigin = true;
  tray?.update({ reachable: true, health });
  void checkUpdateOnce();
}

/**
 * The launch update check. It needs a signed-in session, so a 401 leaves it
 * pending and the next trigger (sign-in cookie, poll tick) tries again.
 */
async function checkUpdateOnce() {
  if (updateCheck !== "pending" || !onAppOrigin) return;
  updateCheck = "running";
  const { status, commit } = await checkForUpdate(win);
  updateCheck = status === "retry" ? "pending" : "done";
  if (status !== "applied") return;

  // Info → Updates resumes following a running update on its own.
  navigate("/info");
  if (await waitForNewCommit(commit)) {
    // The pull also changed desktop/, which this process loaded at startup.
    app.relaunch();
    app.isQuitting = true;
    app.quit();
  }
}

// -------------------------------------------------------------------- state --

function onPollState({ unread, authed, reachable }) {
  tray?.update({ unread, reachable });
  if (authed) void checkUpdateOnce();
}

async function refreshServiceState() {
  const [health, service] = await Promise.all([probeHealth(), queryService()]);
  tray?.update({ health, service, reachable: Boolean(health) });
  // The backend came back while we were sitting on the waiting page.
  if (health && !onAppOrigin) void showWaitingThenLoad();
}

// ----------------------------------------------------------------- actions --

function showWindow() {
  if (!win) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
}

/**
 * Navigate the dashboard. When it's already loaded this goes through the
 * preload bridge and the SPA re-renders with no reload; otherwise we have to
 * load the URL outright.
 */
function navigate(routePath) {
  showWindow();
  if (onAppOrigin) {
    win.webContents.send("desktop:navigate", routePath);
  } else {
    void win.loadURL(`${APP_ORIGIN}${routePath}`).then(() => { onAppOrigin = true; });
  }
}

async function markAllRead() {
  try {
    await apiPost("/api/alerts/read", { all: true });
  } catch (err) {
    console.error("mark all read failed:", err);
  }
  void poller.poll();
  if (onAppOrigin) win.webContents.reload();
}

function handleOauth(startUrl) {
  openOauthWindow(win, startUrl, () => {
    // useAuth's mount effect re-runs getMe(), so the reload is the whole
    // integration — the state machine picks up at totp_required/totp_setup.
    if (onAppOrigin) win.webContents.reload();
    showWindow();
  });
}

function handleRestartService() {
  restartServiceElevated();
  // The elevated process is detached and the user may cancel the UAC prompt,
  // so the only honest thing to do is go back to waiting on health.
  void showWaitingThenLoad();
}

function send(channel, payload) {
  if (win && !win.isDestroyed()) win.webContents.send(channel, payload);
}

// --------------------------------------------------------------------- ipc ---

ipcMain.on("desktop:retry", () => { void showWaitingThenLoad(); });
ipcMain.on("desktop:open-logs", () => { void shell.openPath(LOG_DIR); });
ipcMain.on("desktop:restart-service", () => handleRestartService());

// Backstop for any webContents the explicit policy misses (an iframe, a
// webview): never let one silently become a new Electron window.
app.on("web-contents-created", (_e, contents) => {
  if (contents.getType() === "window") return; // already policed
  contents.setWindowOpenHandler(({ url }) => {
    if (!isAppUrl(url)) shell.openExternal(url);
    return { action: "deny" };
  });
});
