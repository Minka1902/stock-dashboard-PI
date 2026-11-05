/**
 * Pure geometry and arithmetic for the drawing layer — no chart, no DOM.
 * DrawingPrimitive renders and hit-tests with these; useDrawings reshapes
 * with them. All screen maths is in media (CSS) pixels.
 */
import { FIB_EXT_LEVELS, FIB_LEVELS } from "./tools";

/** Distance from point p to segment ab. */
export function distToSegment(p, a, b) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  if (dx === 0 && dy === 0) return Math.hypot(p.x - a.x, p.y - a.y);
  const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / (dx * dx + dy * dy)));
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

/**
 * The segment from a through b extended to the box [0,w]x[0,h]: forward past
 * b only (`both` false — a ray) or in both directions (an extended line).
 * Returns [start, end]; a degenerate (a == b) line is returned unchanged.
 */
export function extendLine(a, b, w, h, both = false) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  if (dx === 0 && dy === 0) return [a, b];
  // Parametric p = a + t*(b-a); find the t range that stays inside the box.
  const ts = [];
  if (dx !== 0) ts.push((0 - a.x) / dx, (w - a.x) / dx);
  if (dy !== 0) ts.push((0 - a.y) / dy, (h - a.y) / dy);
  const inside = (t) => {
    const x = a.x + t * dx;
    const y = a.y + t * dy;
    return x >= -1 && x <= w + 1 && y >= -1 && y <= h + 1;
  };
  const valid = ts.filter(inside);
  const tMax = Math.max(1, ...valid);
  const tMin = both ? Math.min(0, ...valid) : 0;
  return [
    { x: a.x + tMin * dx, y: a.y + tMin * dy },
    { x: a.x + tMax * dx, y: a.y + tMax * dy },
  ];
}

/** Retracement levels between two anchors: level 0 at `b`, 1 at `a`. */
export function fibRetracement(aPrice, bPrice, levels = FIB_LEVELS) {
  return levels.map((level) => ({ level, price: bPrice - (bPrice - aPrice) * level }));
}

/** Trend-based extension: the A→B move projected from C. */
export function fibExtension(aPrice, bPrice, cPrice, levels = FIB_EXT_LEVELS) {
  return levels.map((level) => ({ level, price: cPrice + (bPrice - aPrice) * level }));
}

/**
 * Span between two anchor times, as a short human string. Intraday times are
 * epoch seconds; daily+ times are "YYYY-MM-DD" strings.
 */
export function formatSpan(t0, t1) {
  const secs = (v) => (typeof v === "number" ? v : Date.parse(`${v}T00:00:00Z`) / 1000);
  const d = Math.abs(secs(t1) - secs(t0));
  if (!Number.isFinite(d)) return "";
  const days = d / 86400;
  if (days >= 1) return `${Math.round(days)}d`;
  const hours = Math.floor(d / 3600);
  const mins = Math.round((d % 3600) / 60);
  return hours ? `${hours}h ${mins}m` : `${mins}m`;
}

/** Δprice, Δ% and direction between two anchors (for the measure tool). */
export function measureStats(p0, p1) {
  const delta = p1.price - p0.price;
  const pct = p0.price ? (delta / p0.price) * 100 : null;
  return { delta, pct, up: delta >= 0 };
}

/**
 * Long/short position stats. Points are [entry, target, stop]. Reward and
 * risk are per share; `rr` is reward/risk (null when risk is zero).
 */
export function positionStats(kind, points) {
  const [entry, target, stop] = points.map((p) => p.price);
  const sign = kind === "short" ? -1 : 1;
  const reward = (target - entry) * sign;
  const risk = (entry - stop) * sign;
  return {
    entry, target, stop, reward, risk,
    rewardPct: entry ? (reward / entry) * 100 : null,
    riskPct: entry ? (risk / entry) * 100 : null,
    rr: risk > 0 ? reward / risk : null,
  };
}

/**
 * The initial [entry, target, stop] for a position drawn with two clicks:
 * entry at the first, the second sets the right edge and the reward size.
 * The stop starts at half the reward (a 2:1 plan) — a starting point to drag,
 * not a recommendation.
 */
export function initialPosition(kind, entry, second) {
  const reward = Math.abs(second.price - entry.price) || Math.abs(entry.price) * 0.02;
  const sign = kind === "short" ? -1 : 1;
  const end = { time: second.time, off: second.off };
  return [
    entry,
    { ...end, price: entry.price + sign * reward },
    { ...end, price: entry.price - sign * reward / 2 },
  ];
}

/**
 * Parallel channel in screen space: the baseline a→b, and its copy shifted
 * vertically so it passes through c. Returns [a2, b2] for the parallel line.
 */
export function channelParallel(a, b, c) {
  const dx = b.x - a.x;
  const yAtC = dx === 0 ? a.y : a.y + ((b.y - a.y) * (c.x - a.x)) / dx;
  const dy = c.y - yAtC;
  return [{ x: a.x, y: a.y + dy }, { x: b.x, y: b.y + dy }];
}

/** A point is inside the ellipse inscribed in the box of a and b (with slack). */
export function inEllipse(p, a, b, slack = 0) {
  const cx = (a.x + b.x) / 2;
  const cy = (a.y + b.y) / 2;
  const rx = Math.abs(b.x - a.x) / 2 + slack;
  const ry = Math.abs(b.y - a.y) / 2 + slack;
  if (rx <= 0 || ry <= 0) return false;
  return ((p.x - cx) / rx) ** 2 + ((p.y - cy) / ry) ** 2 <= 1;
}

/** Axis-aligned box of a and b. */
export function box(a, b) {
  return {
    x: Math.min(a.x, b.x), y: Math.min(a.y, b.y),
    w: Math.abs(b.x - a.x), h: Math.abs(b.y - a.y),
  };
}

export function inBox(p, r, slack = 0) {
  return p.x >= r.x - slack && p.x <= r.x + r.w + slack
    && p.y >= r.y - slack && p.y <= r.y + r.h + slack;
}

/** Price with sensible precision for its magnitude. */
export function fmtPrice(v) {
  if (v == null || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  return v.toFixed(a >= 1000 ? 2 : a >= 1 ? 2 : 4);
}

export function fmtPct(v) {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}
