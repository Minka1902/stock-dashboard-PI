// Market regime presentation, shared by the TopBar chip and the Risk tab card.
// Labels mirror backend/app/regime.py LABELS.

export const REGIME_LABEL = {
  risk_on: "Risk-on",
  neutral: "Neutral",
  risk_off: "Risk-off",
  unknown: "Unknown",
};

export const REGIME_TONE = { risk_on: "pos", neutral: "flat", risk_off: "neg", unknown: "muted" };

export const GATE_OPTIONS = [
  { value: "off", label: "Off", title: "Ignore the regime" },
  { value: "downgrade", label: "Downgrade", title: "In risk-off, show new buys as watch with the reason" },
  { value: "pause", label: "Pause", title: "In risk-off, suppress new buys like the kill switch" },
];

export const RISK_TAB_ROUTE = { kind: "view", view: "trading-bot", tab: "risk" };
