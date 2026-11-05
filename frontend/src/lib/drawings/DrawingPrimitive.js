/**
 * A lightweight-charts v5 series primitive that renders user-drawn annotations.
 *
 * Shapes are anchored to (time, price) rather than pixels, so they stay pinned
 * to the data through pan, zoom and timeframe changes. Times snap to bar times,
 * which is both stable across data refreshes and what you want for a trendline.
 *
 * Shape JSON (every field after `points` is optional, so drawings saved before
 * any of them existed still render exactly as they did):
 *   { id, kind, points: [{time, price, off?}], text?,
 *     color?, colorKey?, width?, dash?, fillOpacity?, locked?, fontSize? }
 *
 * `off` is a bar offset from `time` — fractional for the freehand brush, and
 * whole bars for a point placed in the empty space right of the last bar
 * (lightweight-charts can only map times that exist in the data).
 * `colorKey` is a palette key (tools.js PALETTE_KEYS) resolved through the
 * theme; an explicit `color` string wins over it.
 */
import {
  box, channelParallel, distToSegment, extendLine, fibExtension, fibRetracement,
  fmtPct, fmtPrice, formatSpan, inBox, inEllipse, measureStats, positionStats,
} from "./geometry";
import { DASHES } from "./tools";
import { withAlpha } from "../themeColors";

// Theme colours arrive through setColors() (resolved from the --draw-* and
// --chart-* tokens by ChartPro via lib/themeColors). These neutral fallbacks
// only paint if a shape is drawn before the host has supplied them.
const DEFAULT_COLORS = {
  stroke: "rgb(128, 128, 128)",
  selected: "rgb(96, 150, 220)",
  fill: "rgba(128, 128, 128, 0.12)",
  label: "rgb(20, 20, 20)",
  up: "rgb(60, 170, 110)",
  down: "rgb(210, 80, 80)",
  info: "rgb(96, 150, 220)",
  compare: "rgb(160, 110, 220)",
  muted: "rgb(140, 140, 140)",
  text: "rgb(230, 230, 230)",
  surface: "rgb(30, 30, 30)",
};
const HANDLE_R = 4;
export const HIT_TOLERANCE = 7; // px — generous enough for a trackpad
const FONT_FAMILY = "ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace";
const DEFAULT_WIDTH = 1.8;
const DEFAULT_FILL_ALPHA = 0.12;

export class DrawingPrimitive {
  constructor() {
    this._shapes = [];
    this._selectedId = null;
    this._draft = null;      // in-progress shape while drawing
    this._hidden = false;
    this._series = null;
    this._chart = null;
    this._requestUpdate = null;
    this._colors = DEFAULT_COLORS;
    this._size = { width: 0, height: 0 };
    this._textBoxes = new Map(); // id -> label box, measured at draw time
    // IPrimitivePaneView: `renderer` and `zOrder` are methods, not properties.
    // The array identity is kept stable — the library caches on it.
    const renderer = { draw: (target) => this._draw(target) };
    this._paneViews = [{
      renderer: () => renderer,
      zOrder: () => "top",
    }];
  }

  // --- ISeriesPrimitive ---
  attached({ chart, series, requestUpdate }) {
    this._chart = chart;
    this._series = series;
    this._requestUpdate = requestUpdate;
    this._dead = false;
  }

  detached() {
    // Everything below bails on this flag. ChartPro rebuilds its series on any
    // pref change, and lightweight-charts throws "Object is disposed" the
    // moment a removed chart or series is touched — including from a render
    // pass still in flight when the teardown happens.
    this._dead = true;
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
  }

  paneViews() { return this._paneViews; }

  updateAllViews() { /* geometry is derived at draw time */ }

  // --- state ---
  setShapes(shapes) { this._shapes = shapes || []; this.redraw(); }
  setSelected(id) { this._selectedId = id; this.redraw(); }
  setDraft(shape) { this._draft = shape; this.redraw(); }
  setHidden(hidden) { this._hidden = !!hidden; this.redraw(); }
  /** { stroke, selected, fill, label, up, down, … } as canvas-safe colour strings. */
  setColors(colors) { this._colors = { ...DEFAULT_COLORS, ...(colors || {}) }; this.redraw(); }
  redraw() {
    if (this._dead) return;
    try { this._requestUpdate?.(); } catch { this._dead = true; }
  }

  /** Called by the host when it tears down a series we may still be bound to. */
  kill() { this._dead = true; }

  /** (time, price, off?) -> screen px. Null when off-data, or after disposal. */
  toScreen(pt) {
    if (this._dead || !this._chart || !this._series || !pt) return null;
    try {
      const ts = this._chart.timeScale();
      let x = ts.timeToCoordinate(pt.time);
      const y = this._series.priceToCoordinate(pt.price);
      if (x == null || y == null) return null;
      if (pt.off) {
        const logical = ts.coordinateToLogical(x);
        if (logical == null) return null;
        x = ts.logicalToCoordinate(logical + pt.off);
        if (x == null) return null;
      }
      return { x, y };
    } catch {
      return null; // chart or series disposed mid-flight
    }
  }

  /** Screen positions of a shape's drag handles, in handle-index order. */
  handlesOf(shape, pts) {
    switch (shape.kind) {
      case "text":
      case "brush":
        return [];
      default:
        return pts;
    }
  }

  /**
   * The shape whose outline is within tolerance of (x, y), topmost first, plus
   * which handle was hit (for dragging). Named `findShape`, not `hitTest`:
   * lightweight-charts calls a primitive's own `hitTest` expecting its
   * PrimitiveHoveredItem contract, which this is not.
   */
  findShape(x, y) {
    if (this._dead || this._hidden) return null;
    const p = { x, y };
    const tol = HIT_TOLERANCE;
    const { width: W, height: H } = this._size;
    for (let i = this._shapes.length - 1; i >= 0; i -= 1) {
      const shape = this._shapes[i];
      const pts = shape.points.map((pt) => this.toScreen(pt));
      if (!pts.length || pts.some((q) => q == null)) continue;

      if (shape.id === this._selectedId) {
        const hs = this.handlesOf(shape, pts);
        for (let h = 0; h < hs.length; h += 1) {
          if (Math.hypot(p.x - hs[h].x, p.y - hs[h].y) <= tol + 2) return { shape, handle: h };
        }
      }
      if (this._hitsBody(shape, pts, p, tol, W, H)) return { shape, handle: null };
      // An unselected shape's endpoints still grab (quick reshape).
      const hs = this.handlesOf(shape, pts);
      for (let h = 0; h < hs.length; h += 1) {
        if (Math.hypot(p.x - hs[h].x, p.y - hs[h].y) <= tol + 2) return { shape, handle: h };
      }
    }
    return null;
  }

  /** Back-compat alias for callers that predate findShape. */
  hitShape(x, y) { return this.findShape(x, y); }

  _hitsBody(shape, pts, p, tol, W, H) {
    const [a, b, c] = pts;
    switch (shape.kind) {
      case "trendline":
      case "arrow":
        return pts.length >= 2 && distToSegment(p, a, b) <= tol;
      case "tray":
      case "extline": {
        if (pts.length < 2) return false;
        const [s, e] = extendLine(a, b, W, H, shape.kind === "extline");
        return distToSegment(p, s, e) <= tol;
      }
      case "hline":
        return Math.abs(p.y - a.y) <= tol;
      case "ray":
        return Math.abs(p.y - a.y) <= tol && p.x >= a.x - tol;
      case "vline":
        return Math.abs(p.x - a.x) <= tol;
      case "channel": {
        if (pts.length < 2) return false;
        if (distToSegment(p, a, b) <= tol) return true;
        if (!c) return false;
        const [a2, b2] = channelParallel(a, b, c);
        if (distToSegment(p, a2, b2) <= tol) return true;
        // inside the band
        const minX = Math.min(a.x, b.x);
        const maxX = Math.max(a.x, b.x);
        if (p.x < minX || p.x > maxX || a.x === b.x) return false;
        const t = (p.x - a.x) / (b.x - a.x);
        const y1 = a.y + t * (b.y - a.y);
        const y2 = a2.y + t * (b2.y - a2.y);
        return p.y >= Math.min(y1, y2) && p.y <= Math.max(y1, y2);
      }
      case "fib": {
        if (pts.length < 2) return false;
        const r = box(a, b);
        return inBox(p, r, tol);
      }
      case "fibext": {
        if (pts.length < 3) return pts.length >= 2 && distToSegment(p, a, b) <= tol;
        if (distToSegment(p, a, b) <= tol || distToSegment(p, b, c) <= tol) return true;
        const levels = fibExtension(shape.points[0].price, shape.points[1].price, shape.points[2].price);
        const ys = levels.map((l) => this._series?.priceToCoordinate(l.price)).filter((v) => v != null);
        if (!ys.length) return false;
        const x2 = c.x + Math.max(40, Math.abs(b.x - a.x));
        return inBox(p, { x: c.x, y: Math.min(...ys), w: x2 - c.x, h: Math.max(...ys) - Math.min(...ys) }, tol);
      }
      case "zone":
      case "measure":
        return pts.length >= 2 && inBox(p, box(a, b), tol);
      case "ellipse":
        return pts.length >= 2 && inEllipse(p, a, b, tol);
      case "brush":
        for (let i = 1; i < pts.length; i += 1) {
          if (distToSegment(p, pts[i - 1], pts[i]) <= tol) return true;
        }
        return pts.length === 1 && Math.hypot(p.x - a.x, p.y - a.y) <= tol;
      case "long":
      case "short": {
        if (pts.length < 3) return false;
        const x1 = Math.min(a.x, b.x);
        const x2 = Math.max(a.x, b.x);
        const ys = [a.y, b.y, c.y];
        return inBox(p, { x: x1, y: Math.min(...ys), w: x2 - x1, h: Math.max(...ys) - Math.min(...ys) }, tol);
      }
      case "text": {
        const r = this._textBoxes.get(shape.id);
        return r ? inBox(p, r, 3) : Math.abs(p.x - a.x) <= 90 && Math.abs(p.y - a.y) <= 12;
      }
      case "callout": {
        const r = this._textBoxes.get(shape.id);
        if (r && inBox(p, r, 3)) return true;
        return pts.length >= 2 && distToSegment(p, a, b) <= tol;
      }
      default:
        return false;
    }
  }

  // --- rendering ---
  _colorOf(shape) {
    const theme = this._colors;
    return shape.color || (shape.colorKey && theme[shape.colorKey]) || theme.stroke;
  }

  _fillOf(shape, color, fallbackAlpha = DEFAULT_FILL_ALPHA) {
    if (shape.fillOpacity != null) return withAlpha(color, shape.fillOpacity);
    // Legacy zones (no colour of their own) keep the themed --draw-fill.
    if (!shape.color && !shape.colorKey) return this._colors.fill;
    return withAlpha(color, fallbackAlpha);
  }

  _draw(target) {
    if (this._dead) return;
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      this._size = mediaSize;
      if (this._hidden) return;
      const all = this._draft ? [...this._shapes, this._draft] : this._shapes;
      for (const shape of all) {
        const pts = shape.points.map((pt) => this.toScreen(pt));
        if (!pts.length || pts.some((p) => p == null)) continue;
        const selected = shape.id === this._selectedId;
        ctx.save();
        try {
          this._drawShape(ctx, shape, pts, mediaSize, shape === this._draft);
          if (selected) this._drawHandles(ctx, shape, pts);
        } catch {
          // a malformed shape must never take the whole layer down
        }
        ctx.restore();
      }
    });
  }

  _drawHandles(ctx, shape, pts) {
    const theme = this._colors;
    ctx.setLineDash([]);
    ctx.lineWidth = 1.5;
    const hs = this.handlesOf(shape, pts);
    for (const q of hs) {
      ctx.beginPath();
      ctx.arc(q.x, q.y, HANDLE_R, 0, Math.PI * 2);
      ctx.fillStyle = theme.surface;
      ctx.fill();
      ctx.strokeStyle = theme.selected;
      ctx.stroke();
    }
    if (!hs.length) {
      // text & brush: mark the selection with a dashed bounding box
      const r = this._textBoxes.get(shape.id) || this._bounds(pts);
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = theme.selected;
      ctx.strokeRect(r.x - 3, r.y - 3, r.w + 6, r.h + 6);
    }
  }

  _bounds(pts) {
    const xs = pts.map((p) => p.x);
    const ys = pts.map((p) => p.y);
    return { x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) };
  }

  /** A small filled tag with text, anchored at (x, y) by `align`. */
  _tag(ctx, text, x, y, { bg, fg, align = "left", font = `11px ${FONT_FAMILY}` } = {}) {
    ctx.save();
    ctx.setLineDash([]);
    ctx.font = font;
    const w = ctx.measureText(text).width + 10;
    const h = 16;
    const left = align === "right" ? x - w : align === "center" ? x - w / 2 : x;
    ctx.globalAlpha = 0.92;
    ctx.fillStyle = bg;
    ctx.fillRect(left, y - h / 2, w, h);
    ctx.globalAlpha = 1;
    ctx.fillStyle = fg;
    ctx.textBaseline = "middle";
    ctx.fillText(text, left + 5, y + 0.5);
    ctx.restore();
    return { x: left, y: y - h / 2, w, h };
  }

  _drawShape(ctx, shape, pts, size, isDraft) {
    const theme = this._colors;
    const color = this._colorOf(shape);
    const width = shape.width ?? DEFAULT_WIDTH;
    const dash = isDraft ? [4, 4] : (DASHES[shape.dash] || []);
    ctx.lineWidth = width;
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.setLineDash(dash);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    const [a, b, c] = pts;
    const line = (p, q) => { ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(q.x, q.y); ctx.stroke(); };

    switch (shape.kind) {
      case "trendline":
        if (b) line(a, b);
        break;
      case "tray":
      case "extline": {
        if (!b) break;
        const [s, e] = extendLine(a, b, size.width, size.height, shape.kind === "extline");
        line(s, e);
        break;
      }
      case "arrow": {
        if (!b) break;
        line(a, b);
        const ang = Math.atan2(b.y - a.y, b.x - a.x);
        const len = 8 + width * 2;
        ctx.setLineDash([]);
        ctx.beginPath();
        ctx.moveTo(b.x, b.y);
        ctx.lineTo(b.x - len * Math.cos(ang - Math.PI / 7), b.y - len * Math.sin(ang - Math.PI / 7));
        ctx.lineTo(b.x - len * Math.cos(ang + Math.PI / 7), b.y - len * Math.sin(ang + Math.PI / 7));
        ctx.closePath();
        ctx.fill();
        break;
      }
      case "hline":
        line({ x: 0, y: a.y }, { x: size.width, y: a.y });
        this._tag(ctx, fmtPrice(shape.points[0].price), size.width - 4, a.y, { bg: color, fg: theme.label, align: "right" });
        break;
      case "ray": {
        line(a, { x: size.width, y: a.y });
        // price tag so the level is readable without the crosshair
        this._tag(ctx, fmtPrice(shape.points[0].price), a.x, a.y, { bg: color, fg: theme.label });
        break;
      }
      case "vline":
        line({ x: a.x, y: 0 }, { x: a.x, y: size.height });
        break;
      case "channel": {
        if (!b) break;
        line(a, b);
        if (!c) break;
        const [a2, b2] = channelParallel(a, b, c);
        ctx.fillStyle = this._fillOf(shape, color, 0.08);
        ctx.beginPath();
        ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.lineTo(b2.x, b2.y); ctx.lineTo(a2.x, a2.y);
        ctx.closePath();
        ctx.fill();
        line(a2, b2);
        // dotted midline
        ctx.setLineDash([2, 4]);
        ctx.lineWidth = 1;
        line({ x: (a.x + a2.x) / 2, y: (a.y + a2.y) / 2 }, { x: (b.x + b2.x) / 2, y: (b.y + b2.y) / 2 });
        break;
      }
      case "fib": {
        if (!b) break;
        const levels = fibRetracement(shape.points[0].price, shape.points[1].price);
        this._drawLevels(ctx, shape, levels, Math.min(a.x, b.x), Math.max(a.x, b.x), color);
        ctx.setLineDash([3, 3]);
        ctx.lineWidth = 1;
        line(a, b);
        break;
      }
      case "fibext": {
        if (!b) break;
        ctx.setLineDash([3, 3]);
        ctx.lineWidth = 1;
        line(a, b);
        if (!c) break;
        line(b, c);
        const levels = fibExtension(shape.points[0].price, shape.points[1].price, shape.points[2].price);
        this._drawLevels(ctx, shape, levels, c.x, c.x + Math.max(40, Math.abs(b.x - a.x)), color);
        break;
      }
      case "zone": {
        if (!b) break;
        const r = box(a, b);
        ctx.fillStyle = this._fillOf(shape, color);
        ctx.fillRect(r.x, r.y, r.w, r.h);
        ctx.strokeRect(r.x, r.y, r.w, r.h);
        break;
      }
      case "ellipse": {
        if (!b) break;
        const r = box(a, b);
        ctx.beginPath();
        ctx.ellipse(r.x + r.w / 2, r.y + r.h / 2, Math.max(0.5, r.w / 2), Math.max(0.5, r.h / 2), 0, 0, Math.PI * 2);
        ctx.fillStyle = this._fillOf(shape, color);
        ctx.fill();
        ctx.stroke();
        break;
      }
      case "brush": {
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        for (let i = 1; i < pts.length; i += 1) ctx.lineTo(pts[i].x, pts[i].y);
        if (pts.length === 1) ctx.lineTo(a.x + 0.1, a.y);
        ctx.stroke();
        break;
      }
      case "measure": {
        if (!b) break;
        const st = measureStats(shape.points[0], shape.points[1]);
        const tone = st.up ? theme.up : theme.down;
        const r = box(a, b);
        ctx.fillStyle = withAlpha(tone, shape.fillOpacity ?? 0.14);
        ctx.fillRect(r.x, r.y, r.w, r.h);
        ctx.strokeStyle = tone;
        ctx.lineWidth = 1;
        ctx.setLineDash([]);
        const midX = r.x + r.w / 2;
        line({ x: midX, y: a.y }, { x: midX, y: b.y });
        line({ x: a.x, y: (a.y + b.y) / 2 }, { x: b.x, y: (a.y + b.y) / 2 });
        const bars = this._barsBetween(shape.points[0], shape.points[1]);
        const span = formatSpan(shape.points[0].time, shape.points[1].time);
        const text = `${st.delta >= 0 ? "+" : ""}${fmtPrice(st.delta)} (${fmtPct(st.pct)}) · ${bars} bars${span ? ` · ${span}` : ""}`;
        const ty = st.up ? r.y - 12 : r.y + r.h + 12;
        this._tag(ctx, text, midX, ty, { bg: tone, fg: theme.label, align: "center" });
        break;
      }
      case "long":
      case "short": {
        if (!b || !c) break;
        const st = positionStats(shape.kind, shape.points);
        const x1 = Math.min(a.x, b.x);
        const x2 = Math.max(a.x, b.x, a.x + 24);
        const alpha = shape.fillOpacity ?? 0.16;
        ctx.setLineDash([]);
        ctx.fillStyle = withAlpha(theme.up, alpha);
        ctx.fillRect(x1, Math.min(a.y, b.y), x2 - x1, Math.abs(b.y - a.y));
        ctx.fillStyle = withAlpha(theme.down, alpha);
        ctx.fillRect(x1, Math.min(a.y, c.y), x2 - x1, Math.abs(c.y - a.y));
        ctx.lineWidth = 1;
        ctx.strokeStyle = theme.up; line({ x: x1, y: b.y }, { x: x2, y: b.y });
        ctx.strokeStyle = theme.down; line({ x: x1, y: c.y }, { x: x2, y: c.y });
        ctx.strokeStyle = color; ctx.lineWidth = 1.5; line({ x: x1, y: a.y }, { x: x2, y: a.y });
        const mid = (x1 + x2) / 2;
        const tAbove = b.y < c.y;
        this._tag(ctx, `Target ${fmtPrice(st.target)} (${fmtPct(st.rewardPct)})`, mid, b.y + (tAbove ? -11 : 11), { bg: theme.up, fg: theme.label, align: "center" });
        this._tag(ctx, `Stop ${fmtPrice(st.stop)} (${fmtPct(st.riskPct == null ? null : -st.riskPct)})`, mid, c.y + (tAbove ? 11 : -11), { bg: theme.down, fg: theme.label, align: "center" });
        const rr = st.rr == null ? "—" : st.rr.toFixed(2);
        this._tag(ctx, `${shape.kind === "long" ? "Long" : "Short"} ${fmtPrice(st.entry)} · R:R ${rr}`, mid, a.y, { bg: color, fg: theme.label, align: "center" });
        break;
      }
      case "text": {
        const fs = shape.fontSize || 12;
        ctx.setLineDash([]);
        ctx.font = `600 ${fs}px ${FONT_FAMILY}`;
        const text = shape.text || "";
        const w = ctx.measureText(text).width;
        ctx.fillText(text, a.x + 6, a.y - 4);
        ctx.beginPath();
        ctx.arc(a.x, a.y, 2.5, 0, Math.PI * 2);
        ctx.fill();
        this._textBoxes.set(shape.id, { x: a.x - 3, y: a.y - 4 - fs, w: w + 12, h: fs + 8 });
        break;
      }
      case "callout": {
        if (!b) break;
        const fs = shape.fontSize || 12;
        ctx.setLineDash([]);
        ctx.font = `600 ${fs}px ${FONT_FAMILY}`;
        const lines = String(shape.text || "").split("\n");
        const w = Math.max(24, ...lines.map((l) => ctx.measureText(l).width)) + 14;
        const h = lines.length * (fs + 4) + 8;
        const r = { x: b.x, y: b.y - h / 2, w, h };
        line(a, { x: b.x, y: b.y });
        ctx.beginPath();
        ctx.arc(a.x, a.y, 2.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = this._fillOf(shape, color, 0.9);
        ctx.beginPath();
        ctx.roundRect ? ctx.roundRect(r.x, r.y, r.w, r.h, 4) : ctx.rect(r.x, r.y, r.w, r.h);
        ctx.fill();
        ctx.lineWidth = 1;
        ctx.stroke();
        const fillAlpha = shape.fillOpacity ?? 0.9;
        ctx.fillStyle = fillAlpha > 0.5 ? theme.label : color;
        ctx.textBaseline = "top";
        lines.forEach((l, i) => ctx.fillText(l, r.x + 7, r.y + 5 + i * (fs + 4)));
        this._textBoxes.set(shape.id, r);
        break;
      }
      default:
        // Unknown kind from a newer client: draw its anchors rather than
        // nothing, so it can still be found and deleted.
        for (const q of pts) { ctx.beginPath(); ctx.arc(q.x, q.y, 3, 0, Math.PI * 2); ctx.fill(); }
    }
  }

  /** Horizontal fib levels between x1 and x2, with tinted bands and labels. */
  _drawLevels(ctx, shape, levels, x1, x2, color) {
    const ys = levels.map((l) => this._series?.priceToCoordinate(l.price));
    const alpha = shape.fillOpacity ?? 0.06;
    for (let i = 1; i < levels.length; i += 1) {
      if (ys[i] == null || ys[i - 1] == null) continue;
      ctx.fillStyle = withAlpha(color, alpha * (i % 2 ? 1 : 0.55));
      ctx.fillRect(x1, Math.min(ys[i], ys[i - 1]), x2 - x1, Math.abs(ys[i] - ys[i - 1]));
    }
    ctx.setLineDash(DASHES[shape.dash] || []);
    ctx.lineWidth = shape.width ?? 1;
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.font = `10px ${FONT_FAMILY}`;
    ctx.textBaseline = "bottom";
    levels.forEach((l, i) => {
      const y = ys[i];
      if (y == null) return;
      ctx.beginPath();
      ctx.moveTo(x1, y);
      ctx.lineTo(x2, y);
      ctx.stroke();
      ctx.fillText(`${l.level} (${fmtPrice(l.price)})`, x1 + 3, y - 2);
    });
  }

  /** Whole bars between two anchors, via the time scale's logical indices. */
  _barsBetween(p0, p1) {
    try {
      const ts = this._chart.timeScale();
      const l0 = ts.coordinateToLogical(ts.timeToCoordinate(p0.time)) + (p0.off || 0);
      const l1 = ts.coordinateToLogical(ts.timeToCoordinate(p1.time)) + (p1.off || 0);
      return Math.round(Math.abs(l1 - l0));
    } catch {
      return 0;
    }
  }
}
