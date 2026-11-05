/** Presentation for alert types, shared by the bell and the analysis pane. */
import { RISK_TAB_ROUTE } from "./regime";

export const TYPE_ICON = {
  boom_cross: "spark",
  golden_cross: "trending",
  insider_cluster: "trending",
  earnings_soon: "calendar",
  congress_buy: "layers",
  breakout_setup: "trending",
  breakout_confirmed: "spark",
  breakdown_warning: "gauge",
  false_breakout: "x",
  topping_formation: "gauge",
  recommendation_change: "star",
  exit: "shield",
  risk: "shield",
};

export function alertIcon(type) {
  return TYPE_ICON[type] || "bell";
}

/** Human label for an alert type, derived rather than a second hand-kept map. */
export function alertTypeLabel(type) {
  return String(type || "")
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/**
 * Where an alert leads: its stock's page, or — for an account-level alert
 * with no ticker (a kill-switch trip) — the Trading Bot page's Risk tab.
 */
export function alertRoute(alert) {
  return alert.ticker
    ? { kind: "stock", ticker: alert.ticker, alertKey: alert.dedup_key }
    : RISK_TAB_ROUTE;
}
