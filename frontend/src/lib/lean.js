/**
 * The composite sentiment "lean", in one place.
 *
 * sentiment.build_summary emits BUY / SELL / NEUTRAL, but the UI used to key
 * on a different vocabulary (GREEDY / FEARFUL / RISK_ON / RISK_OFF), so a real
 * BUY or SELL fell through to the raw string with no colour and no read line.
 * The backend's words are the source of truth; the older keys are kept mapped
 * so nothing regresses if they reappear.
 */

export const LEAN_LABEL = {
  BUY: "Buy",
  SELL: "Sell",
  NEUTRAL: "Neutral",
  // legacy vocabulary, mapped rather than dropped
  GREEDY: "Greedy",
  FEARFUL: "Fearful",
  RISK_ON: "Risk on",
  RISK_OFF: "Risk off",
};

/** One line of plain language per lean — what it means, not what to do. */
export const LEAN_READ = {
  BUY: "More indicators are at levels that have historically favoured buyers than sellers.",
  SELL: "More indicators are at levels that have historically favoured sellers than buyers.",
  NEUTRAL: "No crowd extreme in either direction. Nothing here is forcing a move.",
  GREEDY: "Crowd is greedy. Historically a time to trim, not chase.",
  FEARFUL: "Crowd is fearful. Historically where opportunities hide.",
  RISK_ON: "Risk appetite is on. Momentum has the tailwind.",
  RISK_OFF: "Risk is coming off. Defence over offence.",
};

/** Tone for colouring: "pos" | "neg" | "" (neutral). */
export const LEAN_TONE = {
  BUY: "pos",
  SELL: "neg",
  NEUTRAL: "",
  FEARFUL: "pos",
  RISK_ON: "pos",
  GREEDY: "neg",
  RISK_OFF: "neg",
};

export function leanLabel(lean) {
  return LEAN_LABEL[lean] || lean || "Neutral";
}

export function leanTone(lean) {
  return LEAN_TONE[lean] ?? "";
}
