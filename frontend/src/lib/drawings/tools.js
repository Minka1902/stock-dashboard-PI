/**
 * The drawing-tool catalogue: one entry per shape kind the chart can draw.
 *
 * `points` is how many clicks finish the shape (0 = freehand drag). `key` is
 * the persisted `shape.kind`, so it must never be renamed — "trendline",
 * "ray" (a horizontal ray, historically labelled "Horizontal level"), "zone"
 * and "text" predate this file and live in saved drawings.
 *
 * `shortcut` is an Alt+<key> accelerator (shown in tooltips). Alt+E and Alt+F are left
 * alone because Chromium on Windows binds them to its own menu.
 */
export const TOOLS = [
  // lines
  { key: "trendline", label: "Trend line", group: "lines", points: 2, shortcut: "T" },
  { key: "tray", label: "Ray", group: "lines", points: 2 },
  { key: "extline", label: "Extended line", group: "lines", points: 2 },
  { key: "hline", label: "Horizontal line", group: "lines", points: 1, shortcut: "H" },
  { key: "ray", label: "Horizontal ray", group: "lines", points: 1, shortcut: "J" },
  { key: "vline", label: "Vertical line", group: "lines", points: 1, shortcut: "V" },
  { key: "arrow", label: "Arrow", group: "lines", points: 2, shortcut: "A" },
  { key: "channel", label: "Parallel channel", group: "lines", points: 3, shortcut: "C" },
  // fibonacci
  { key: "fib", label: "Fib retracement", group: "fib", points: 2, shortcut: "G" },
  { key: "fibext", label: "Trend-based fib extension", group: "fib", points: 3 },
  // shapes
  { key: "zone", label: "Rectangle", group: "shapes", points: 2, shortcut: "R" },
  { key: "ellipse", label: "Ellipse", group: "shapes", points: 2, shortcut: "O" },
  { key: "brush", label: "Brush", group: "shapes", points: 0, shortcut: "B" },
  // annotation
  { key: "text", label: "Text", group: "annotate", points: 1, shortcut: "X" },
  { key: "callout", label: "Callout", group: "annotate", points: 2 },
  // measure / position
  { key: "measure", label: "Measure", group: "measure", points: 2, shortcut: "M" },
  { key: "long", label: "Long position", group: "measure", points: 2, shortcut: "L" },
  { key: "short", label: "Short position", group: "measure", points: 2, shortcut: "S" },
];

export const TOOL_BY_KEY = Object.fromEntries(TOOLS.map((t) => [t.key, t]));

export const GROUPS = [
  { key: "lines", label: "Lines" },
  { key: "fib", label: "Fibonacci" },
  { key: "shapes", label: "Shapes & brush" },
  { key: "annotate", label: "Text & callouts" },
  { key: "measure", label: "Measure & positions" },
];

export const TOOL_BY_SHORTCUT = Object.fromEntries(
  TOOLS.filter((t) => t.shortcut).map((t) => [t.shortcut, t.key]),
);

/** Kinds that have an area a fill-opacity control applies to. */
export const FILLABLE = new Set(["zone", "ellipse", "channel", "fib", "fibext", "callout", "measure", "long", "short"]);
/** Kinds whose primary content is text. */
export const TEXTUAL = new Set(["text", "callout"]);
/** Kinds whose line style (width / dash) is meaningful. */
export const STROKED = new Set([
  "trendline", "tray", "extline", "hline", "ray", "vline", "arrow", "channel",
  "fib", "fibext", "zone", "ellipse", "brush", "callout",
]);

/** Points a click-built kind needs; unknown (future) kinds default to two. */
export function pointsNeeded(kind) {
  const t = TOOL_BY_KEY[kind];
  return t ? t.points : 2;
}

/** Fibonacci ratios, TradingView's defaults. */
export const FIB_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
export const FIB_EXT_LEVELS = [0, 0.382, 0.618, 1, 1.272, 1.618, 2, 2.618];

/**
 * Palette keys a shape can pick. Stored as the key (not an rgb string) so a
 * drawing follows the theme; the renderer resolves it through the chart's
 * --chart-* / --draw-* tokens. A shape's explicit `color` still wins, which
 * keeps drawings saved before this change rendering as they were.
 */
export const PALETTE_KEYS = ["stroke", "up", "down", "info", "compare", "muted", "text"];

export const DASHES = { solid: [], dashed: [6, 4], dotted: [1.5, 3] };
export const WIDTHS = [1, 2, 3, 4];

/** Fill opacity each fillable kind renders with until the user sets one. */
export const DEFAULT_FILL = {
  zone: 0.12, ellipse: 0.12, channel: 0.08, fib: 0.06, fibext: 0.06,
  callout: 0.9, measure: 0.14, long: 0.16, short: 0.16,
};
