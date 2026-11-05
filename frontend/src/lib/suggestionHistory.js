/** Shared formatting for suggestion outcomes (calendar view + analysis strip). */

/** Outcome → tone, with a dead band so noise doesn't read as a win or a loss. */
export function outcomeTone(pct) {
  if (pct == null) return "pending";
  if (pct >= 2) return "up";
  if (pct <= -2) return "down";
  return "flat";
}

export function pctLabel(pct) {
  if (pct == null) return "pending";
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

/**
 * Outcome tone -> theme token for the recharts markers. Resolved to rgb() by
 * lib/themeColors (useThemeColors) so the dots follow the active theme.
 */
export const TONE_TOKEN = {
  up: "--positive", down: "--negative", flat: "--text-faint", pending: "--text-muted",
};
