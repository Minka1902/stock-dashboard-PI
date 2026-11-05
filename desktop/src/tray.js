// Tray icon, menu and unread indicator.

import { Menu, Tray, nativeImage } from "electron";

import { TRAY_ICON } from "./config.js";
import { badgeText } from "./alerts/seen.js";

// Mirrors the Ctrl/Cmd+K command palette in frontend/src/App.jsx. Keep in sync.
//
// Deliberately omitted: "Refresh all sources" (there is only
// POST /api/refresh/{source} — no bulk route — so it would mean duplicating
// EXTERNAL_SOURCES from useDashboardData.js here), and the theme/dyslexia
// toggles (they live in SettingsContext with no main-process representation).
// All three are one Ctrl+K away in the app itself.
const NAV_ITEMS = [
  ["Market Sentiment", "/"],
  ["Suggestions", "/suggestions"],
  ["Portfolio", "/portfolio"],
  ["Trading Bot", "/trading-bot"],
  ["Watchlist", "/watchlist"],
  ["Trades", "/trades"],
  ["News", "/news"],
  ["Boom Score", "/boom-score"],
  ["Economic Calendar", "/econ-calendar"],
  ["X Watch", "/x"],
];

export class AppTray {
  constructor(handlers) {
    this.handlers = handlers;
    this.state = { unread: 0, reachable: false, health: null, service: "unknown" };
    this.tray = new Tray(nativeImage.createFromPath(TRAY_ICON));
    this.tray.on("click", () => this.handlers.onToggleWindow());
    this.render();
  }

  update(patch) {
    this.state = { ...this.state, ...patch };
    this.render();
  }

  render() {
    const { unread, reachable, health, service } = this.state;

    const badge = badgeText(unread);
    this.tray.setToolTip(
      !reachable ? "Signal — backend unreachable"
        : badge ? `Signal — ${badge} unread`
          : "Signal");

    const backendLabel = !reachable
      ? `Backend: unreachable (service ${service})`
      : `Backend: ${health?.status ?? "ok"} · up ${formatUptime(health?.uptime_seconds)}`;

    this.tray.setContextMenu(Menu.buildFromTemplate([
      { label: "Open Signal", click: () => this.handlers.onShowWindow() },
      { type: "separator" },
      ...NAV_ITEMS.map(([label, path]) => ({
        label, click: () => this.handlers.onNavigate(path),
      })),
      { type: "separator" },
      { label: unread ? `Alerts: ${unread} unread` : "Alerts: none unread", enabled: false },
      { label: "Mark all read", enabled: unread > 0, click: () => this.handlers.onMarkAllRead() },
      { type: "separator" },
      { label: backendLabel, enabled: false },
      { label: "Restart backend service…", click: () => this.handlers.onRestartService() },
      { label: "Open logs folder", click: () => this.handlers.onOpenLogs() },
      { type: "separator" },
      {
        label: "Start Signal at login",
        type: "checkbox",
        checked: this.handlers.getOpenAtLogin(),
        click: (item) => this.handlers.onSetOpenAtLogin(item.checked),
      },
      { label: "Quit Signal", click: () => this.handlers.onQuit() },
    ]));
  }

  destroy() {
    this.tray?.destroy();
  }
}

function formatUptime(seconds) {
  const s = Number(seconds) || 0;
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}
