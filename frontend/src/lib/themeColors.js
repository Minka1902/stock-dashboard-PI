import { useMemo, useSyncExternalStore } from "react";

/**
 * Theme tokens for code that paints outside CSS.
 *
 * The palettes in index.css are written in oklch, and two consumers can't take
 * that as-is:
 *  - lightweight-charts parses colour strings itself (it derives alpha variants
 *    for labels and the crosshair) and can't be relied on to take oklch() —
 *    the reason ChartPro used to hardcode hex;
 *  - canvas code that does arithmetic on a colour (adding alpha) needs numbers.
 *
 * So a token is resolved by setting it as `color` on a hidden probe element
 * and reading the computed value back. Current Chromium returns computed
 * colours in their *authored* space — `oklch(0.84 0.17 158)`, not rgb — and a
 * canvas `fillStyle` keeps that string too, so neither normalises it for us.
 * Reading a pixel back from a 1x1 canvas does, but quantises translucent
 * colours badly (a 5.5%-alpha grid line comes back with 8-bit rgb divided by
 * alpha). Hence the explicit oklch -> sRGB conversion below, with the pixel
 * read-back kept only as a last resort for a colour syntax we don't parse.
 *
 * Everything re-resolves when <html data-theme> changes: `useThemeColors`
 * subscribes to that attribute with a MutationObserver.
 */

// ---------------------------------------------------------------------------
// Pure colour maths (no DOM) — exported for tests and for withAlpha callers.
// ---------------------------------------------------------------------------

const clamp01 = (x) => Math.min(1, Math.max(0, x));

/** Linear-light sRGB channel -> gamma-encoded 0..1. */
function encodeSrgb(x) {
  const v = Math.abs(x) <= 0.0031308 ? 12.92 * x : Math.sign(x) * (1.055 * Math.abs(x) ** (1 / 2.4) - 0.055);
  return clamp01(v);
}

/** OKLab (L 0..1) -> sRGB bytes. Out-of-gamut values are clipped per channel. */
export function oklabToRgb(L, a, b) {
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.2914855480 * b;
  const l = l_ ** 3;
  const m = m_ ** 3;
  const s = s_ ** 3;
  const r = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s;
  const g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s;
  const bl = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s;
  return [r, g, bl].map((c) => Math.round(encodeSrgb(c) * 255));
}

/** OKLCH (L 0..1, C, H degrees) -> sRGB bytes. */
export function oklchToRgb(L, C, H) {
  const h = (H * Math.PI) / 180;
  return oklabToRgb(L, C * Math.cos(h), C * Math.sin(h));
}

/** "0.5" | "50%" | "none" -> number, with `pctScale` for what 100% means. */
function num(tok, pctScale = 1) {
  if (tok == null || tok === "none") return 0;
  if (tok.endsWith("%")) return (parseFloat(tok) / 100) * pctScale;
  if (tok.endsWith("deg")) return parseFloat(tok);
  return parseFloat(tok);
}

/** Split "fn(a b c / d)" or "fn(a, b, c, d)" into [fn, [a,b,c], alpha]. */
function splitFn(str) {
  const m = /^([a-z-]+)\((.*)\)$/i.exec(str.trim());
  if (!m) return null;
  const [body, alphaPart] = m[2].split("/");
  const parts = body.trim().split(/[\s,]+/).filter(Boolean);
  let alpha = alphaPart != null ? num(alphaPart.trim()) : 1;
  // legacy comma form: rgba(r, g, b, a)
  if (alphaPart == null && parts.length === 4) alpha = num(parts.pop());
  return { fn: m[1].toLowerCase(), parts, alpha: Number.isFinite(alpha) ? alpha : 1 };
}

/**
 * Parse a computed CSS colour into { r, g, b, a } (bytes + 0..1 alpha), or
 * null if the syntax isn't one we handle.
 */
export function parseColor(str) {
  if (!str) return null;
  const s = str.trim();
  if (s === "transparent") return { r: 0, g: 0, b: 0, a: 0 };
  if (s[0] === "#") {
    let hex = s.slice(1);
    if (hex.length === 3 || hex.length === 4) hex = [...hex].map((c) => c + c).join("");
    if (hex.length !== 6 && hex.length !== 8) return null;
    const n = (i) => parseInt(hex.slice(i, i + 2), 16);
    return { r: n(0), g: n(2), b: n(4), a: hex.length === 8 ? n(6) / 255 : 1 };
  }
  const f = splitFn(s);
  if (!f) return null;
  const { fn, parts, alpha } = f;
  const a = clamp01(alpha);
  if ((fn === "rgb" || fn === "rgba") && parts.length >= 3) {
    const [r, g, b] = parts.map((p) => Math.round(clamp01(num(p, 255) / 255) * 255));
    return { r, g, b, a };
  }
  if (fn === "oklch" && parts.length >= 3) {
    const [r, g, b] = oklchToRgb(num(parts[0], 1), num(parts[1], 0.4), num(parts[2]));
    return { r, g, b, a };
  }
  if (fn === "oklab" && parts.length >= 3) {
    const [r, g, b] = oklabToRgb(num(parts[0], 1), num(parts[1], 0.4), num(parts[2], 0.4));
    return { r, g, b, a };
  }
  if (fn === "color" && parts[0] === "srgb" && parts.length >= 4) {
    const [r, g, b] = parts.slice(1, 4).map((p) => Math.round(clamp01(num(p, 1)) * 255));
    return { r, g, b, a };
  }
  return null;
}

/** { r, g, b, a } -> "rgb(r, g, b)" or "rgba(r, g, b, a)" — canvas-safe. */
export function formatRgb({ r, g, b, a = 1 }) {
  if (a >= 1) return `rgb(${r}, ${g}, ${b})`;
  return `rgba(${r}, ${g}, ${b}, ${Math.round(a * 1000) / 1000})`;
}

/**
 * The same colour with its alpha multiplied by `alpha` — for the translucent
 * fills and volume bars that used to be built by appending "88" to a hex.
 */
export function withAlpha(color, alpha) {
  const c = parseColor(color);
  if (!c) return color;
  return formatRgb({ ...c, a: c.a * alpha });
}

// ---------------------------------------------------------------------------
// DOM resolution
// ---------------------------------------------------------------------------

let probe = null;
let pixelCtx = null;

function getProbe() {
  if (probe && probe.isConnected) return probe;
  probe = document.createElement("span");
  probe.setAttribute("aria-hidden", "true");
  probe.style.cssText = "position:absolute;width:0;height:0;overflow:hidden;visibility:hidden;pointer-events:none";
  document.body.appendChild(probe);
  return probe;
}

/** Last resort: let the canvas rasterise it and read the pixel back. */
function readPixel(css) {
  try {
    if (!pixelCtx) {
      const c = document.createElement("canvas");
      c.width = 1;
      c.height = 1;
      pixelCtx = c.getContext("2d", { willReadFrequently: true });
    }
    pixelCtx.clearRect(0, 0, 1, 1);
    pixelCtx.fillStyle = css;
    pixelCtx.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = pixelCtx.getImageData(0, 0, 1, 1).data;
    return { r, g, b, a: a / 255 };
  } catch {
    return null;
  }
}

/**
 * Resolve one CSS custom property (e.g. "--chart-up") to a canvas-safe
 * rgb()/rgba() string in the current theme. Returns `fallback` when the
 * token is undefined or unparsable, so a missing token never throws.
 */
export function resolveToken(name, fallback = "rgb(128, 128, 128)") {
  if (typeof document === "undefined") return fallback;
  const el = getProbe();
  // An undefined var() makes `color` fall back to inherited — detect that by
  // checking the raw custom property first.
  const raw = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  if (!raw) return fallback;
  el.style.color = "";
  el.style.color = `var(${name})`;
  const computed = getComputedStyle(el).color;
  const parsed = parseColor(computed) || readPixel(computed);
  return parsed ? formatRgb(parsed) : fallback;
}

// Resolved per (theme, token). Tokens only change with the theme, so this
// saves a style recalc for every chart that mounts under the same theme.
const cache = new Map();

/** Resolve a { key: "--token" } map for the given theme snapshot. */
export function resolveTokens(tokens, theme) {
  const out = {};
  for (const [key, name] of Object.entries(tokens)) {
    const ck = `${theme}|${name}`;
    let v = cache.get(ck);
    if (v == null) {
      v = resolveToken(name, null);
      if (v != null) cache.set(ck, v);
    }
    out[key] = v ?? "rgb(128, 128, 128)";
  }
  return out;
}

// ---------------------------------------------------------------------------
// React binding
// ---------------------------------------------------------------------------

function subscribe(onChange) {
  if (typeof MutationObserver === "undefined") return () => {};
  const mo = new MutationObserver(onChange);
  mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => mo.disconnect();
}

const themeSnapshot = () => document.documentElement.dataset.theme || "";
const serverSnapshot = () => "";

/** The current <html data-theme> value, re-rendering when it changes. */
export function useThemeName() {
  return useSyncExternalStore(subscribe, themeSnapshot, serverSnapshot);
}

/**
 * `tokens` is a { key: "--css-token" } map (keep it a module constant). The
 * result maps each key to an rgb()/rgba() string for the current theme, and is
 * a new object exactly when the theme changes — so it is safe as an effect
 * dependency for re-applying colours.
 */
export function useThemeColors(tokens) {
  const theme = useThemeName();
  const key = JSON.stringify(tokens);
  return useMemo(() => resolveTokens(JSON.parse(key), theme), [key, theme]);
}
