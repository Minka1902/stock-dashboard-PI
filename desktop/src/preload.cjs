// Preload: the only bridge between the main process and the dashboard page.
//
// contextIsolation stays on and nodeIntegration off — the renderer is the real
// web app, and it should have exactly two extra powers: being told to navigate,
// and (on the waiting page) being told about backend health.

const { contextBridge, ipcRenderer } = require("electron");

// Duplicated from NAV_EVENT in frontend/src/lib/nav.js. Keep in sync — it is
// what subscribeToRoute() listens on via useSyncExternalStore, so dispatching
// it re-renders the SPA with no reload.
const NAV_EVENT = "app:navigate";

ipcRenderer.on("desktop:navigate", (_e, path) => {
  if (typeof path !== "string" || !path.startsWith("/")) return;
  if (window.location.pathname + window.location.search === path) return;
  window.history.pushState(null, "", path);
  window.dispatchEvent(new Event(NAV_EVENT));
});

contextBridge.exposeInMainWorld("signalDesktop", {
  isDesktop: true,
  // Used by renderer/waiting.html only.
  onHealth: (cb) => ipcRenderer.on("desktop:health", (_e, state) => cb(state)),
  retry: () => ipcRenderer.send("desktop:retry"),
  openLogs: () => ipcRenderer.send("desktop:open-logs"),
  restartService: () => ipcRenderer.send("desktop:restart-service"),
});
