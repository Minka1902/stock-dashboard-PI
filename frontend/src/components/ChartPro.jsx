import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createChart, CandlestickSeries, BarSeries, HistogramSeries, LineSeries,
  AreaSeries, BaselineSeries, LineStyle, PriceScaleMode, createSeriesMarkers,
} from "lightweight-charts";
import { AnimatePresence, motion } from "motion/react";
import { animate } from "animejs";
import { getChart, getChartExtended } from "../api";
import {
  smaSeries, emaSeries, bollingerSeries, rsiSeries, macdSeries, vwapSeries,
  heikinAshi,
} from "../lib/indicators";
import { prefersReducedMotion } from "../lib/motionConfig";
import { currencyForSymbol, currencySymbol } from "../lib/format";
import { useDrawings } from "../lib/drawings/useDrawings";
import { SessionPrimitive } from "../lib/drawings/SessionPrimitive";
import { canvasToBlob, composeSnapshot, snapshotFileName } from "../lib/chartSnapshot";
import { useThemeColors, withAlpha } from "../lib/themeColors";
import DrawingRail from "./chart/DrawingRail";
import ShapeProperties from "./chart/ShapeProperties";
import TextEditor from "./chart/TextEditor";
import SnapshotMenu from "./chart/SnapshotMenu";
import DraftsMenu from "./chart/DraftsMenu";
import ChartToast from "./chart/ChartToast";
import Tooltip from "./Tooltip";
import GLOSSARY from "../lib/glossary";
import { useChartToast } from "./chart/useChartToast";
import styles from "./ChartPro.module.css";

// lightweight-charts renders to canvas and cannot parse oklch(), so the chart
// colours come from the --chart-* tokens in index.css resolved to rgb() by
// useThemeColors — and re-resolved whenever the app theme changes.
const CHART_TOKENS = {
  text: "--chart-text",
  grid: "--chart-grid",
  border: "--chart-border",
  crosshair: "--chart-crosshair",
  up: "--chart-up",           // positive
  down: "--chart-down",       // negative
  accent: "--chart-accent",
  info: "--chart-info",
  muted: "--chart-muted",
  compare: "--chart-compare", // SPY overlay / VWAP, distinct from every indicator
  labelBg: "--surface-3",     // crosshair axis-label background
};

// User drawings (lib/drawings) — same resolution, separate roles. The extra
// keys are the palette a shape can pick from (tools.js PALETTE_KEYS), stored
// on the shape as a key so it follows the theme.
const DRAW_TOKENS = {
  stroke: "--draw-stroke",
  selected: "--draw-selected",
  fill: "--draw-fill",
  label: "--draw-label",
  up: "--chart-up",
  down: "--chart-down",
  info: "--chart-info",
  compare: "--chart-compare",
  muted: "--chart-muted",
  text: "--text",
  surface: "--surface-3",
};

// The snapshot header strip (lib/chartSnapshot) paints on a canvas too.
const SNAP_TOKENS = {
  bg: "--surface",
  text: "--text",
  muted: "--text-muted",
  faint: "--text-faint",
  up: "--chart-up",
  down: "--chart-down",
  accent: "--accent",
  border: "--border",
};

// Tel Aviv (TASE) listings have no pre-market or after-hours session at all.
const isTase = (t) => /\.TA$/i.test(String(t || ""));
const CCY_NAMES = { USD: "US dollars", ILS: "Israeli shekels", EUR: "euros", GBP: "British pounds" };

const TIMEFRAMES = [
  { key: "1m", label: "1m", name: "1-minute bars" },
  { key: "5m", label: "5m", name: "5-minute bars" },
  { key: "15m", label: "15m", name: "15-minute bars" },
  { key: "1h", label: "1h", name: "1-hour bars" },
  { key: "1d", label: "D", name: "Daily bars" },
  { key: "1wk", label: "W", name: "Weekly bars" },
  { key: "1mo", label: "M", name: "Monthly bars" },
];
const INTRADAY = new Set(["1m", "5m", "15m", "1h"]);
const INTRADAY_REFRESH_MS = 30000;

const CHART_TYPES = [
  { key: "candles", label: "Candles" },
  { key: "hollow", label: "Hollow" },
  { key: "heikin", label: "Heikin-Ashi" },
  { key: "bars", label: "Bars" },
  { key: "line", label: "Line" },
  { key: "area", label: "Area" },
  { key: "baseline", label: "Baseline" },
];

// Compact per-type glyphs for the chart-type segmented control (18x18,
// currentColor). Filled marks use `fill`, outlines use `stroke`.
const TYPE_ICONS = {
  candles: (
    <g fill="currentColor" stroke="currentColor" strokeWidth="1.3">
      <line x1="5.5" y1="2.5" x2="5.5" y2="15.5" /><rect x="3.5" y="5" width="4" height="6.5" />
      <line x1="12.5" y1="4" x2="12.5" y2="14" /><rect x="10.5" y="7" width="4" height="5" />
    </g>
  ),
  hollow: (
    <g fill="none" stroke="currentColor" strokeWidth="1.3">
      <line x1="5.5" y1="2.5" x2="5.5" y2="15.5" /><rect x="3.5" y="5" width="4" height="6.5" />
      <line x1="12.5" y1="4" x2="12.5" y2="14" /><rect x="10.5" y="7" width="4" height="5" />
    </g>
  ),
  heikin: (
    <g stroke="currentColor" strokeWidth="1.3">
      <line x1="9" y1="2" x2="9" y2="16" fill="none" />
      <rect x="5.5" y="5.5" width="7" height="6" fill="currentColor" />
    </g>
  ),
  bars: (
    <g fill="none" stroke="currentColor" strokeWidth="1.3">
      <line x1="5.5" y1="3" x2="5.5" y2="15" /><line x1="2.5" y1="6" x2="5.5" y2="6" /><line x1="5.5" y1="11" x2="8.5" y2="11" />
      <line x1="12.5" y1="4" x2="12.5" y2="14" /><line x1="9.5" y1="9" x2="12.5" y2="9" /><line x1="12.5" y1="12" x2="15.5" y2="12" />
    </g>
  ),
  line: (
    <polyline points="2,13 6,8 9,11 16,4" fill="none" stroke="currentColor"
              strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
  ),
  area: (
    <g stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 13 L6 8 L9 11 L16 4 L16 16 L2 16 Z" fill="currentColor" fillOpacity="0.25" stroke="none" />
      <polyline points="2,13 6,8 9,11 16,4" fill="none" />
    </g>
  ),
  baseline: (
    <g stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
      <line x1="2" y1="9" x2="16" y2="9" strokeDasharray="2 2" strokeWidth="1" opacity="0.6" />
      <polyline points="2,12 6,10 9,6 16,4" fill="none" />
    </g>
  ),
};

function TypeIcon({ type }) {
  return (
    <svg viewBox="0 0 18 18" width="15" height="15" aria-hidden="true">
      {TYPE_ICONS[type]}
    </svg>
  );
}

const MA_DEFS = [
  { n: 20, key: "info" }, { n: 50, key: "accent" },
  { n: 150, key: "muted" }, { n: 200, key: "down" },
];
const EMA_DEFS = [{ n: 9, key: "up" }, { n: 21, key: "compare" }];

// `term` is the lib/glossary.js entry the toolbar tooltip explains it with.
const IND_DEFS = [
  { key: "ma", label: "SMA 20/50/150/200", term: "moving_average" },
  { key: "ema", label: "EMA 9/21", term: "ema" },
  { key: "bb", label: "Bollinger (20,2)", term: "bollinger" },
  { key: "vwap", label: "VWAP", intradayOnly: true, term: "vwap" },
  { key: "vol", label: "Volume" },
  { key: "rsi", label: "RSI (14)", term: "rsi" },
  { key: "macd", label: "MACD (12,26,9)", term: "macd" },
];

const DEFAULT_PREFS = {
  tf: "1d",
  type: "candles",
  inds: { ma: true, ema: false, bb: false, vwap: false, vol: true, rsi: false, macd: false },
  logScale: false,
  compare: false,
  overlays: true,
  // Extended hours are on by default for intraday timeframes (Task 3).
  prepost: true,
  v: 2,
};

const PREFS_KEY = "chartProPrefs";

function loadPrefs() {
  try {
    const raw = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
    // v1 stored prepost:false as its default, which would read as an explicit
    // "off" — re-default it once so EXT starts on as intended.
    const migrated = raw.v === 2 ? raw : { ...raw, prepost: true, v: 2 };
    return { ...DEFAULT_PREFS, ...migrated, inds: { ...DEFAULT_PREFS.inds, ...(migrated.inds || {}) } };
  } catch {
    return DEFAULT_PREFS;
  }
}

/**
 * Give every pane its height in one pass. Stretch factors are relative, so
 * setting all of them together (main = whatever is left) is order-independent
 * — unlike calling setHeight pane by pane while later panes are still being
 * created, which is what made the sub-panes jump on every rebuild.
 */
function applyPaneHeights(chart, subHeights, total) {
  const panes = chart.panes();
  if (!panes.length) return;
  const subs = subHeights.slice(0, panes.length - 1);
  const used = subs.reduce((a, b) => a + b, 0);
  const main = Math.max(120, total - used);
  try {
    panes[0].setStretchFactor(main);
    subs.forEach((h, i) => panes[i + 1]?.setStretchFactor(h));
  } catch { /* pane disposed mid-rebuild */ }
}

const SESSION_LABEL = { pre: "PRE", regular: "REG", post: "POST" };

function fmtClock(epochSecs) {
  const d = new Date(epochSecs * 1000);
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

/**
 * The chart-level (not per-series) options that depend on the theme. Applied
 * with chart.applyOptions right after creation and again on every theme
 * change, so the chart — and the user's zoom/pan — survives switching themes.
 */
function chartThemeOptions(c) {
  const line = { color: c.crosshair, labelBackgroundColor: c.labelBg };
  return {
    layout: { textColor: c.text, panes: { separatorColor: c.border } },
    grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
    rightPriceScale: { borderColor: c.border },
    timeScale: { borderColor: c.border },
    crosshair: { vertLine: line, horzLine: line },
  };
}

function monoFont() {
  const v = getComputedStyle(document.documentElement).getPropertyValue("--mono").trim();
  return v || "monospace";
}

function fmt(v, digits = 2) {
  return v == null ? "—" : Number(v).toFixed(digits);
}

function fmtVol(v) {
  if (v == null) return "—";
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return String(Math.round(v));
}

// Sub-pane heights in px. Deliberately compact: the indicators are a glance
// check, not the subject — every pixel here comes out of the price pane, which
// is capped by the viewport clamp below.
const PANE_HEIGHTS = { vol: 56, rsi: 64, macd: 72 };

// IPaneApi.priceScale() throws when the id isn't present on that pane, which
// can happen transiently while series are being rebuilt. Callers just want to
// skip the pane in that case.
function rightScaleOf(pane) {
  try {
    return pane.priceScale("right");
  } catch {
    return null;
  }
}

/**
 * Pro chart workspace (TradingView lightweight-charts v5): chart types,
 * intraday-to-monthly timeframes, indicator menu with RSI/MACD sub-panes,
 * log scale, SPY comparison, crosshair OHLCV legend, and the analysis
 * overlays (S/R, entry/stop/target, pattern markers) on the daily view.
 * Preferences persist in localStorage.
 */
export default function ChartPro({ ticker, analysis = null, height = 460 }) {
  const elRef = useRef(null);
  // rgb() strings for the current theme; a new object on every theme change.
  const colors = useThemeColors(CHART_TOKENS);
  const drawColors = useThemeColors(DRAW_TOKENS);
  const snapColors = useThemeColors(SNAP_TOKENS);
  const wrapRef = useRef(null);
  const flashRef = useRef(null);
  const barsRef = useRef([]);          // bars on screen, for drawing anchors
  const toast = useChartToast();
  // Session metadata from the last chart response ({has_extended, ...}).
  const [session, setSession] = useState(null);
  // Latest pre-market / after-hours print for the D/W/M price lines.
  const [extended, setExtended] = useState(null);
  // Currency of every price on this chart, from the chart response (TASE
  // agorot are already shekels by then); the symbol convention until it lands.
  const [chartCcy, setChartCcy] = useState(null);
  const ccy = chartCcy || currencyForSymbol(ticker) || "";
  const sym = currencySymbol(ccy);
  // Read by the series rebuild and the crosshair legend, so a theme change
  // doesn't have to re-run either of them.
  const colorsRef = useRef(colors);
  const recolorRef = useRef([]);       // (colors) => void, one per themed object
  const [prefs, setPrefs] = useState(loadPrefs);
  // Viewport-aware chart height: fill the space below the toolbar/legend down
  // to the bottom of the viewport, clamped to [280, `height`] so the chart +
  // toolbar + legend fit one screen without page scroll (Task 9).
  const [chartHeight, setChartHeight] = useState(height);
  const chartHeightRef = useRef(height);
  const subHeightsRef = useRef([]);    // px heights of VOL/RSI/MACD panes, in order
  const [bars, setBars] = useState(null);      // null = loading, [] = no data
  const [compareBars, setCompareBars] = useState(null);
  const [error, setError] = useState(null);
  const [legend, setLegend] = useState(null);
  // Brief overlay shown while a toolbar change re-renders the chart (Task 5).
  const [updating, setUpdating] = useState(false);

  // Persistent chart handles: the chart is created once (see the mount effect)
  // and only its *series* are torn down/rebuilt on data/indicator changes, so
  // the user's zoom/pan (visible logical range) survives toolbar interactions.
  const chartRef = useRef(null);
  const seriesRef = useRef([]);        // every removable series (main + overlays + panes)
  const overlayRef = useRef([]);       // {series,label,color} for the crosshair legend
  const datasetKeyRef = useRef(null);  // `${ticker}|${tf}` — only refit when this changes
  // Which `${ticker}|${tf}` the bars in state belong to, and which one was
  // asked for last. Switching timeframe renders once with the previous bars
  // still in state; building (and fitting) them under the new key left the
  // real bars to arrive into a stale view — daily bars squeezed into the
  // right half after a trip through 5m.
  const barsKeyRef = useRef(null);
  const wantKeyRef = useRef(null);
  const prevBarsRef = useRef(null);    // identity check: did the bar data actually change?
  const legendMapsRef = useRef({ bars: [], byTime: new Map(), idx: new Map() });
  // The price series drawings anchor to, plus a counter that changes whenever
  // it is rebuilt so the drawing primitive can re-attach.
  const mainSeriesRef = useRef(null);
  const [seriesEpoch, setSeriesEpoch] = useState(0);

  const setPref = useCallback((patch) => {
    setUpdating(true); // show the loader for the duration of the change (Task 5)
    setPrefs((p) => {
      const next = { ...p, ...patch, inds: { ...p.inds, ...(patch.inds || {}) } };
      try { localStorage.setItem(PREFS_KEY, JSON.stringify(next)); } catch { /* private mode */ }
      return next;
    });
  }, []);

  // The series rebuild is synchronous, so hold the loader a beat after a pref
  // change so the transition is perceptible rather than a jarring instant swap.
  useEffect(() => {
    if (!updating) return undefined;
    const id = setTimeout(() => setUpdating(false), 320);
    return () => clearTimeout(id);
  }, [updating]);

  const intraday = INTRADAY.has(prefs.tf);
  // Extended hours exist for this listing unless it's on TASE or the last
  // chart response said the exchange reports none (FX, most non-US venues).
  const extSupported = !isTase(ticker) && session?.has_extended !== false;
  const prepostOn = intraday && prefs.prepost && extSupported;

  // ---- data: bars for the active timeframe (auto-refresh while intraday) ----
  const loadBars = useCallback(async () => {
    const key = `${ticker}|${prefs.tf}`;
    wantKeyRef.current = key;
    try {
      const data = await getChart(ticker, prefs.tf, prepostOn);
      if (wantKeyRef.current !== key) return; // a newer timeframe/ticker won
      barsKeyRef.current = key;
      setBars(data.bars);
      if (data.session) setSession(data.session);
      if (data.currency) setChartCcy(data.currency);
      setError(null);
    } catch (e) {
      if (wantKeyRef.current !== key) return;
      setError(e.message || "chart data unavailable");
    }
  }, [ticker, prefs.tf, prepostOn]);

  // A new ticker starts with unknown session support.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSession(null);
    setChartCcy(null);
  }, [ticker]);

  // ---- D/W/M: the current pre-market / after-hours print (Task 3) ----
  // Polled while a daily+ view is open; null whenever there is no current
  // extended print (regular session, weekend with no post bars, TASE, …).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setExtended(null);
    if (intraday || isTase(ticker)) return undefined;
    let alive = true;
    const load = () => getChartExtended(ticker)
      .then((d) => { if (alive) setExtended(d?.supported ? d : null); })
      .catch(() => { if (alive) setExtended(null); });
    load();
    const id = setInterval(load, 60000);
    return () => { alive = false; clearInterval(id); };
  }, [ticker, intraday]);

  useEffect(() => {
    // Intentional: reset to the loading state, kick off the fetch, then poll
    // intraday timeframes (same pattern as useLiveQuotes).
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setBars(null);
    setError(null);
    loadBars();
    if (!INTRADAY.has(prefs.tf)) return undefined;
    const id = setInterval(loadBars, INTRADAY_REFRESH_MS);
    return () => clearInterval(id);
  }, [loadBars, prefs.tf]);

  useEffect(() => {
    // Intentional: clear the overlay synchronously when compare is switched off.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!prefs.compare) { setCompareBars(null); return; }
    let alive = true;
    getChart("SPY", prefs.tf)
      .then((d) => { if (alive) setCompareBars(d.bars); })
      .catch(() => { if (alive) setCompareBars([]); });
    return () => { alive = false; };
  }, [prefs.compare, prefs.tf]);

  const displayBars = useMemo(() => {
    if (!bars) return null;
    return prefs.type === "heikin" ? heikinAshi(bars) : bars;
  }, [bars, prefs.type]);

  // ---- chart lifecycle: create once, keep across every pref change ----
  useEffect(() => {
    const el = elRef.current;
    if (!el) return undefined;

    const chart = createChart(el, {
      height: chartHeight,
      width: el.clientWidth,
      layout: {
        background: { color: "transparent" },
        fontFamily: monoFont(),
        fontSize: 11,
        attributionLogo: false,
        panes: { enableResize: false },
      },
      timeScale: { rightOffset: 4, secondsVisible: false },
      crosshair: { mode: 0 },
    });
    // Colours are applied by the theme effect below, which runs right after
    // this one on mount and again on every theme change.
    chartRef.current = chart;

    // The crosshair handler reads live maps/overlays from refs so it never has
    // to be re-subscribed when the series are rebuilt.
    const onMove = (param) => {
      const { bars: lbars, byTime, idx } = legendMapsRef.current;
      if (!lbars.length) { setLegend(null); return; }
      let b, prev;
      if (param.time && byTime.has(param.time)) {
        const i = idx.get(param.time);
        b = lbars[i]; prev = i > 0 ? lbars[i - 1] : null;
      } else {
        b = lbars[lbars.length - 1]; prev = lbars[lbars.length - 2];
      }
      if (!b) { setLegend(null); return; }
      const changePct = prev && prev.close ? ((b.close - prev.close) / prev.close) * 100 : null;
      const overlays = [];
      for (const o of overlayRef.current) {
        const d = param.seriesData.get(o.series);
        // Stored as a palette key so the legend follows a theme change.
        if (d && d.value != null) {
          overlays.push({ label: o.label, color: colorsRef.current[o.colorKey], value: d.value });
        }
      }
      setLegend({ ...b, changePct, overlays });
    };
    chart.subscribeCrosshairMove(onMove);

    const ro = new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth }));
    ro.observe(el);

    // Resolve the pane under a pointer event, so axis gestures act on the pane
    // you are actually pointing at (VOL/RSI/MACD each own a price scale) rather
    // than always on the main price pane. Panes stack top-to-bottom; the
    // separators between them are a couple of px we don't bother modelling.
    const paneAt = (event) => {
      const panes = chart.panes();
      if (!panes.length) return null;
      const y = event.clientY - el.getBoundingClientRect().top;
      let acc = 0;
      for (const pane of panes) {
        acc += pane.getHeight();
        if (y <= acc) return pane;
      }
      return panes[panes.length - 1];
    };

    // True when the pointer is over the right-hand price axis rather than the
    // plot body. The axis width is shared by every pane.
    const overAxis = (event) => {
      const rect = el.getBoundingClientRect();
      const axisW = chart.priceScale("right").width() || 60; // fallback pre-measure
      return event.clientX >= rect.right - axisW;
    };

    // Wheel over a price axis scales THAT pane's price axis. The library's own
    // wheel handler always zooms the *time* scale regardless of cursor
    // position, so we intercept in the capture phase when the cursor is over an
    // axis, zoom that pane's price range via setVisibleRange, and stop the
    // event before it reaches the chart. Over the plot body we do nothing, so
    // the built-in time-zoom (and axis drag-scale) still work.
    const onAxisWheel = (event) => {
      if (!overAxis(event)) return; // not over the axis → let the chart zoom time
      const pane = paneAt(event);
      if (!pane) return;
      // Over the axis: always intercept so the built-in time-zoom never fires here.
      event.preventDefault();
      event.stopPropagation();
      const ps = rightScaleOf(pane);
      if (!ps) return;
      // Lock the scale so getVisibleRange() is populated (it's null under auto-scale).
      ps.setAutoScale(false);
      const range = ps.getVisibleRange();
      if (!range || range.to === range.from) return;
      const factor = event.deltaY < 0 ? 0.85 : 1 / 0.85; // wheel up = zoom in
      const mid = (range.from + range.to) / 2;
      const half = ((range.to - range.from) / 2) * factor;
      if (half > 0) ps.setVisibleRange({ from: mid - half, to: mid + half });
    };
    el.addEventListener("wheel", onAxisWheel, { capture: true, passive: false });

    // Double-click an axis to hand it back to auto-scale — the escape hatch
    // from a manual zoom, matching the convention in every charting package.
    const onAxisDblClick = (event) => {
      if (!overAxis(event)) return;
      const pane = paneAt(event);
      if (!pane) return;
      event.preventDefault();
      event.stopPropagation();
      rightScaleOf(pane)?.setAutoScale(true);
    };
    el.addEventListener("dblclick", onAxisDblClick, { capture: true });

    return () => {
      el.removeEventListener("wheel", onAxisWheel, { capture: true });
      el.removeEventListener("dblclick", onAxisDblClick, { capture: true });
      chart.unsubscribeCrosshairMove(onMove);
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      seriesRef.current = [];
      overlayRef.current = [];
      datasetKeyRef.current = null;
      prevBarsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- theme changes: recolour everything in place ----
  // Neither the chart nor its series are recreated: chart-level colours go
  // through chart.applyOptions, and every series / price line / marker set the
  // rebuild below created registered a recolour callback in recolorRef, which
  // re-applies its colours through its own applyOptions (or setData/setMarkers
  // for per-point colours). A rebuild would also work, but it re-lays-out the
  // sub-panes, so a theme switch would visibly jump the pane heights.
  // Declared before the rebuild effect so colorsRef is current when both run.
  useEffect(() => {
    colorsRef.current = colors;
    const chart = chartRef.current;
    if (!chart) return;
    chart.applyOptions(chartThemeOptions(colors));
    for (const recolor of recolorRef.current) {
      try { recolor(colors); } catch { /* series already removed */ }
    }
  }, [colors]);

  // ---- height changes: just resize, never rebuild ----
  useEffect(() => {
    chartHeightRef.current = chartHeight;
    const chart = chartRef.current;
    if (!chart) return;
    chart.applyOptions({ height: chartHeight });
    applyPaneHeights(chart, subHeightsRef.current, chartHeight);
  }, [chartHeight]);

  // ---- viewport-aware sizing: measure the space under the canvas and clamp ----
  const hasData = !!(bars && bars.length);
  useEffect(() => {
    const measure = () => {
      const el = elRef.current;
      if (!el) return;
      const top = el.getBoundingClientRect().top;
      const reserve = 48; // legend/key footer breathing room below the canvas
      const avail = window.innerHeight - top - reserve;
      setChartHeight(Math.max(280, Math.min(height, Math.round(avail))));
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [height, hasData]);

  // ---- options-only changes: price-scale mode/margins + intraday time axis ----
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const priceMode = prefs.compare
      ? PriceScaleMode.Percentage
      : prefs.logScale ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal;
    chart.priceScale("right").applyOptions({
      mode: priceMode,
      // Volume now lives in its own pane (Task 11), so the price pane keeps a
      // symmetric margin instead of reserving the bottom for the overlay.
      scaleMargins: { top: 0.08, bottom: 0.08 },
    });
    chart.timeScale().applyOptions({ timeVisible: intraday });
  }, [prefs.compare, prefs.logScale, intraday]);

  // ---- series (re)build: tears down only series, preserves the view ----
  const { ma, ema, bb, vwap, vol, rsi, macd } = prefs.inds;
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !displayBars || displayBars.length === 0) return;

    const showOverlays = prefs.overlays && prefs.tf === "1d" && analysis && !prefs.compare;
    const datasetKey = `${ticker}|${prefs.tf}`;
    if (barsKeyRef.current !== datasetKey) return; // previous timeframe's bars
    const isNewDataset = datasetKeyRef.current !== datasetKey;
    const prevBars = prevBarsRef.current;
    const dataChanged = displayBars !== prevBars;
    const prevLen = prevBars ? prevBars.length : 0;
    // Capture the user's view before we touch the series (skip on a new dataset).
    const savedRange = isNewDataset ? null : chart.timeScale().getVisibleLogicalRange();

    // tear down previous series only (chart, panes-config, view untouched)
    for (const s of seriesRef.current) { try { chart.removeSeries(s); } catch { /* already gone */ } }
    seriesRef.current = [];
    overlayRef.current = [];
    const track = (s) => { seriesRef.current.push(s); return s; };

    // Theme colours. Everything coloured below is created with `c`, and
    // registers how to recolour itself in `recolor`, which the theme effect
    // replays with the new palette — so a theme switch never rebuilds series.
    const c = colorsRef.current;
    const recolor = [];
    recolorRef.current = recolor;
    /** addSeries + track, with `colorFn(palette)` supplying the colour options. */
    const addThemed = (type, opts, colorFn, pane) => {
      const s = track(chart.addSeries(type, { ...opts, ...colorFn(c) }, pane));
      recolor.push((cc) => s.applyOptions(colorFn(cc)));
      return s;
    };
    /** createPriceLine, recoloured the same way. */
    const themedPriceLine = (s, opts, colorKey) => {
      const line = s.createPriceLine({ ...opts, color: c[colorKey] });
      recolor.push((cc) => line.applyOptions({ color: cc[colorKey] }));
    };
    /** Per-point colours live in the data, so those series re-set their data. */
    const themedData = (s, dataFn) => {
      s.setData(dataFn(c));
      recolor.push((cc) => s.setData(dataFn(cc)));
    };

    // main series by chart type
    let main;
    const upDown = (p) => ({
      upColor: p.up, downColor: p.down, wickUpColor: p.up, wickDownColor: p.down,
    });
    const ohlcData = displayBars.map((b) => ({
      time: b.time, open: b.open, high: b.high, low: b.low, close: b.close,
    }));
    // Extended-session bars (intraday + EXT) are drawn dimmer, per point, so
    // the regular session reads as the main event (Task 3).
    const dimExt = prepostOn && displayBars.some((b) => b.session === "pre" || b.session === "post");
    const EXT_ALPHA = 0.42;
    const extColors = (b, p, hollow) => {
      if (!(b.session === "pre" || b.session === "post")) return {};
      const tone = withAlpha(b.close >= b.open ? p.up : p.down, EXT_ALPHA);
      return hollow
        ? { borderColor: tone, wickColor: tone, ...(b.close >= b.open ? {} : { color: tone }) }
        : { color: tone, borderColor: tone, wickColor: tone };
    };
    const ohlcThemed = (hollow) => (p) => displayBars.map((b, i) => ({ ...ohlcData[i], ...extColors(b, p, hollow) }));
    const closeData = displayBars.map((b) => ({ time: b.time, value: b.close }));
    if (prefs.type === "hollow") {
      main = addThemed(CandlestickSeries, { borderVisible: true }, (p) => ({
        ...upDown(p), upColor: "transparent", borderUpColor: p.up, borderDownColor: p.down,
      }));
      if (dimExt) themedData(main, ohlcThemed(true)); else main.setData(ohlcData);
    } else if (prefs.type === "bars") {
      main = addThemed(BarSeries, { thinBars: false }, (p) => ({ upColor: p.up, downColor: p.down }));
      if (dimExt) themedData(main, ohlcThemed(false)); else main.setData(ohlcData);
    } else if (prefs.type === "line") {
      main = addThemed(LineSeries, { lineWidth: 2 }, (p) => ({ color: p.accent }));
      main.setData(closeData);
    } else if (prefs.type === "area") {
      main = addThemed(AreaSeries, { lineWidth: 2 }, (p) => ({
        lineColor: p.accent,
        topColor: withAlpha(p.accent, 0.28), bottomColor: withAlpha(p.accent, 0.02),
      }));
      main.setData(closeData);
    } else if (prefs.type === "baseline") {
      main = addThemed(BaselineSeries, {
        baseValue: { type: "price", price: displayBars[0].close },
      }, (p) => ({
        topLineColor: p.up, bottomLineColor: p.down,
        topFillColor1: withAlpha(p.up, 0.22), topFillColor2: withAlpha(p.up, 0.02),
        bottomFillColor1: withAlpha(p.down, 0.02), bottomFillColor2: withAlpha(p.down, 0.22),
      }));
      main.setData(closeData);
    } else {
      main = addThemed(CandlestickSeries, { borderVisible: false }, upDown);
      if (dimExt) themedData(main, ohlcThemed(false)); else main.setData(ohlcData);
    }

    // The price series is what user drawings anchor to (see useDrawings).
    mainSeriesRef.current = main;
    barsRef.current = displayBars;

    // Shaded background behind pre-market / after-hours bars.
    if (dimExt) {
      const sessionLayer = new SessionPrimitive();
      const sessionColors = (p) => ({ pre: withAlpha(p.info, 0.08), post: withAlpha(p.compare, 0.08) });
      sessionLayer.setColors(sessionColors(c));
      main.attachPrimitive(sessionLayer);
      sessionLayer.setBars(displayBars);
      recolor.push((cc) => sessionLayer.setColors(sessionColors(cc)));
    }

    // A tracked line series in palette colour `colorKey`; `label` (if given)
    // registers it for the crosshair legend.
    const addLine = (data, colorKey, label, width = 1, style) => {
      if (data.length < 2) return;
      const s = addThemed(LineSeries, {
        lineWidth: width, priceLineVisible: false,
        lastValueVisible: false, crosshairMarkerVisible: true,
        ...(style != null ? { lineStyle: style } : {}),
      }, (p) => ({ color: p[colorKey] }));
      s.setData(data);
      if (label) overlayRef.current.push({ series: s, label, colorKey });
    };

    // overlays computed from the *raw* bars (indicator math on real OHLC)
    if (ma) for (const def of MA_DEFS) addLine(smaSeries(bars, def.n), def.key, `SMA ${def.n}`, def.n >= 150 ? 2 : 1);
    if (ema) for (const def of EMA_DEFS) addLine(emaSeries(bars, def.n), def.key, `EMA ${def.n}`, 1, LineStyle.Dotted);
    if (bb) {
      const bands = bollingerSeries(bars);
      addLine(bands.upper, "muted", "BB upper", 1, LineStyle.Dashed);
      addLine(bands.middle, "muted", "BB mid", 1);
      addLine(bands.lower, "muted", "BB lower", 1, LineStyle.Dashed);
    }
    if (vwap && intraday) addLine(vwapSeries(bars), "compare", "VWAP", 2);

    // SPY comparison (percent scale set in the options effect)
    if (prefs.compare && compareBars && compareBars.length > 1) {
      const cmp = addThemed(LineSeries, { lineWidth: 2, priceLineVisible: false, title: "SPY" },
        (p) => ({ color: p.compare }));
      cmp.setData(compareBars.map((b) => ({ time: b.time, value: b.close })));
    }

    // sub-panes — each indicator gets its OWN pane + visible price scale, and
    // shows its current value as an axis label ("tell the data", Task 11).
    let paneIndex = 0;
    const subHeights = [];
    if (vol) {
      paneIndex += 1;
      const volS = track(chart.addSeries(HistogramSeries, {
        priceFormat: { type: "volume" }, priceLineVisible: false,
        lastValueVisible: true, title: "Vol",
      }, paneIndex));
      themedData(volS, (p) => displayBars.map((b) => ({
        time: b.time, value: b.volume,
        color: withAlpha(b.close >= b.open ? p.up : p.down, 0.53),
      })));
      subHeights.push(PANE_HEIGHTS.vol);
    }
    if (rsi) {
      paneIndex += 1;
      const rsiS = addThemed(LineSeries, {
        lineWidth: 2, priceLineVisible: false, lastValueVisible: true, title: "RSI 14",
      }, (p) => ({ color: p.info }), paneIndex);
      rsiS.setData(rsiSeries(bars));
      themedPriceLine(rsiS, { price: 70, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true }, "down");
      themedPriceLine(rsiS, { price: 30, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true }, "up");
      subHeights.push(PANE_HEIGHTS.rsi);
    }
    if (macd) {
      paneIndex += 1;
      const { macd: macdData, signal, hist } = macdSeries(bars);
      const histSeries = track(chart.addSeries(HistogramSeries, {
        priceLineVisible: false, lastValueVisible: false,
      }, paneIndex));
      themedData(histSeries, (p) => hist.map((pt) => ({
        ...pt, color: withAlpha(pt.value >= 0 ? p.up : p.down, 0.53),
      })));
      const macdLine = addThemed(LineSeries, {
        lineWidth: 2, priceLineVisible: false, lastValueVisible: true, title: "MACD",
      }, (p) => ({ color: p.accent }), paneIndex);
      macdLine.setData(macdData);
      const sigLine = addThemed(LineSeries, {
        lineWidth: 1, priceLineVisible: false, lastValueVisible: true, title: "Signal",
      }, (p) => ({ color: p.info }), paneIndex);
      sigLine.setData(signal);
      subHeights.push(PANE_HEIGHTS.macd);
    }
    subHeightsRef.current = subHeights;
    applyPaneHeights(chart, subHeights, chartHeightRef.current);

    // analysis overlays (daily view only; hidden in percent-compare mode)
    if (showOverlays) {
      // Horizontal S/R — round-number levels get a distinct dotted/muted style.
      for (const l of (analysis.support || [])) {
        const round = l.source === "round";
        themedPriceLine(main, { price: l.price, lineWidth: 1,
          lineStyle: round ? LineStyle.Dotted : LineStyle.Dashed, axisLabelVisible: true,
          title: round ? `S ⌾ ${l.price}` : `S ${l.touches}x` }, round ? "muted" : "up");
      }
      for (const l of (analysis.resistance || [])) {
        const round = l.source === "round";
        themedPriceLine(main, { price: l.price, lineWidth: 1,
          lineStyle: round ? LineStyle.Dotted : LineStyle.Dashed, axisLabelVisible: true,
          title: round ? `R ⌾ ${l.price}` : `R ${l.touches}x` }, round ? "muted" : "down");
      }
      // Diagonal trendlines as two-point line series (dashed when broken).
      const lastBar = displayBars[displayBars.length - 1];
      for (const tl of (analysis.trendlines || [])) {
        const pts = (tl.pivots || []).map((pv) => ({ time: pv.date, value: pv.price }));
        if (pts.length && lastBar && lastBar.time > pts[pts.length - 1].time) {
          pts.push({ time: lastBar.time, value: tl.current_value });
        }
        if (pts.length >= 2) {
          const tlS = addThemed(LineSeries, {
            lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
            crosshairMarkerVisible: false,
            lineStyle: tl.broken ? LineStyle.Dashed : LineStyle.Solid,
          }, (p) => ({ color: tl.kind === "support" ? p.up : p.down }));
          tlS.setData(pts);
        }
      }
      if (analysis.entry) themedPriceLine(main, { price: analysis.entry, lineWidth: 1, title: "entry" }, "accent");
      if (analysis.stop) themedPriceLine(main, { price: analysis.stop, lineWidth: 2, title: "stop" }, "down");
      if (analysis.target) themedPriceLine(main, { price: analysis.target, lineWidth: 2, title: "3R" }, "up");

      const buildMarkers = (p) => {
        const markers = [];
        for (const pat of (analysis.patterns || [])) {
          for (const pv of (pat.pivots || [])) {
            markers.push({ time: pv.date, position: "aboveBar", color: p.accent, shape: "circle", text: pv.role });
          }
        }
        for (const g of (analysis.gaps || [])) {
          if (g.filled) continue;
          markers.push({
            time: g.date, position: g.kind === "up" ? "belowBar" : "aboveBar",
            color: g.kind === "up" ? p.up : p.down,
            shape: g.kind === "up" ? "arrowUp" : "arrowDown", text: "gap",
          });
        }
        return markers.sort((a, b) => (a.time < b.time ? -1 : 1));
      };
      const markers = buildMarkers(c);
      if (markers.length) {
        const markerApi = createSeriesMarkers(main, markers);
        recolor.push((cc) => markerApi.setMarkers(buildMarkers(cc)));
      }
    }

    // refresh the crosshair legend maps + reset the resting legend to the last bar
    const byTime = new Map(displayBars.map((b) => [b.time, b]));
    const idx = new Map(displayBars.map((b, i) => [b.time, i]));
    legendMapsRef.current = { bars: displayBars, byTime, idx };
    const lastB = displayBars[displayBars.length - 1];
    const prevB = displayBars[displayBars.length - 2];
    const lastChange = prevB && prevB.close ? ((lastB.close - prevB.close) / prevB.close) * 100 : null;
    setLegend({ ...lastB, changePct: lastChange, overlays: [] });

    // view preservation: refit only for a genuinely new dataset (ticker/timeframe)
    if (isNewDataset) {
      chart.timeScale().fitContent();
      // Re-enable auto-scale on EVERY pane so a prior manual wheel-zoom doesn't
      // freeze an axis at a stale range when the ticker/timeframe changes.
      for (const pane of chart.panes()) rightScaleOf(pane)?.setAutoScale(true);
      datasetKeyRef.current = datasetKey;
    } else if (savedRange) {
      const wasAtEdge = savedRange.to >= prevLen - 1.5;
      if (dataChanged && wasAtEdge) chart.timeScale().scrollToRealTime();
      else chart.timeScale().setVisibleLogicalRange(savedRange);
    }
    prevBarsRef.current = displayBars;
    // Tell the drawing layer to re-attach to the series we just built.
    setSeriesEpoch((n) => n + 1);
  }, [bars, displayBars, compareBars, analysis, prefs.type, prefs.overlays, prefs.compare,
      prefs.tf, ticker, intraday, prepostOn, ma, ema, bb, vwap, vol, rsi, macd]);

  // ---- D/W/M: dashed "Pre" / "After" price lines from the real extended print ----
  useEffect(() => {
    const main = mainSeriesRef.current;
    if (!main || intraday || prefs.compare || !extended) return undefined;
    const lines = [];
    const add = (pt, label, colorKey) => {
      if (!pt || pt.price == null) return;
      try {
        lines.push(main.createPriceLine({
          price: pt.price, color: colors[colorKey], lineWidth: 1,
          lineStyle: LineStyle.Dashed, axisLabelVisible: true,
          title: `${label} ${sym}${Number(pt.price).toFixed(2)}`,
        }));
      } catch { /* series rebuilt underneath us */ }
    };
    add(extended.pre, "Pre", "info");
    add(extended.post, "After", "compare");
    return () => {
      for (const l of lines) { try { main.removePriceLine(l); } catch { /* series gone */ } }
    };
  }, [extended, seriesEpoch, intraday, prefs.compare, colors, sym]);

  // User-drawn annotations: their own primitive layer, so they're independent
  // of indicator toggles and of the analysis overlays drawn from the payload.
  const drawing = useDrawings({
    ticker,
    chartRef,
    elRef,
    mainSeriesRef,
    seriesEpoch,
    enabled: hasData,
    colors: drawColors,
    barsRef,
    scopeRef: wrapRef,
  });

  const tfLabel = TIMEFRAMES.find((t) => t.key === prefs.tf)?.label || prefs.tf;

  // ---- snapshot (Task 5): the chart exactly as it is on screen ----
  const { setSelectedId } = drawing;
  const captureSnapshot = useCallback(async () => {
    const chart = chartRef.current;
    const el = elRef.current;
    if (!chart || !el) throw new Error("chart not ready");
    // Selection handles are editing chrome, not part of the picture.
    setSelectedId(null);
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    // addTopLayer=true brings in primitives on the top layer (the drawings);
    // the crosshair is left out.
    const shot = chart.takeScreenshot(true, false);
    const lbars = barsRef.current || [];
    const last = lbars[lbars.length - 1];
    const prev = lbars[lbars.length - 2];
    const studies = [
      prefs.inds.ma && "SMA 20/50/150/200",
      prefs.inds.ema && "EMA 9/21",
      prefs.inds.bb && "BB(20,2)",
      prefs.inds.vwap && intraday && "VWAP",
      prefs.inds.vol && "Volume",
      prefs.inds.rsi && "RSI 14",
      prefs.inds.macd && "MACD",
      prefs.compare && "vs SPY %",
      prepostOn && "incl. pre/after hours",
      prefs.overlays && prefs.tf === "1d" && analysis && !prefs.compare && "Plan overlay",
      drawing.shapes.length && !drawing.hidden && `${drawing.shapes.length} drawing${drawing.shapes.length === 1 ? "" : "s"}`,
    ].filter(Boolean);
    const ext = extended?.post ? `After ${sym}${extended.post.price.toFixed(2)}` : extended?.pre ? `Pre ${sym}${extended.pre.price.toFixed(2)}` : null;
    const now = new Date();
    const out = composeSnapshot(shot, {
      cssWidth: el.clientWidth,
      ticker,
      timeframe: tfLabel,
      price: last?.close ?? null,
      currencySymbol: prefs.compare ? "" : sym,
      changePct: last && prev && prev.close ? ((last.close - prev.close) / prev.close) * 100 : null,
      stamp: now.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }),
      studies,
      note: ext,
      colors: snapColors,
      fontFamily: getComputedStyle(document.body).fontFamily,
      monoFamily: monoFont(),
    });
    // A quick shutter flash on the chart, via animejs (skipped under reduced motion).
    if (flashRef.current && !prefersReducedMotion()) {
      animate(flashRef.current, { opacity: [0.5, 0], duration: 420, ease: "outQuad" });
    }
    const blob = await canvasToBlob(out);
    return { blob, name: snapshotFileName(ticker, tfLabel, now), title: `${ticker} · ${tfLabel} chart` };
  }, [setSelectedId, prefs, intraday, prepostOn, analysis, drawing.shapes.length, drawing.hidden,
      extended, ticker, tfLabel, snapColors, sym]);

  const toneOf = (b) => (b && b.close >= b.open ? "pos" : "neg");

  return (
    <div className={styles.wrap} ref={wrapRef}>
      <div className={styles.toolbar} role="toolbar" aria-label="Chart controls">
        <div className={styles.group} role="group" aria-label="Timeframe">
          {TIMEFRAMES.map((t) => (
            <Tooltip key={t.key} side="bottom" content={t.name}>
              <button className={styles.pill} data-active={prefs.tf === t.key ? "yes" : "no"}
                      aria-pressed={prefs.tf === t.key}
                      onClick={() => setPref({ tf: t.key })}>{t.label}</button>
            </Tooltip>
          ))}
        </div>

        <div className={styles.segmented} role="group" aria-label="Chart type">
          {CHART_TYPES.map((t) => {
            const active = prefs.type === t.key;
            return (
              <Tooltip key={t.key} side="bottom" content={t.label}>
              <button
                type="button"
                className={styles.segBtn}
                data-active={active ? "yes" : "no"}
                onClick={() => setPref({ type: t.key })}
                aria-label={t.label}
                aria-pressed={active}
              >
                {active && (
                  <motion.span
                    layoutId="chartTypeThumb"
                    className={styles.segThumb}
                    transition={prefersReducedMotion()
                      ? { duration: 0 }
                      : { type: "spring", stiffness: 520, damping: 40 }}
                    aria-hidden="true"
                  />
                )}
                <span className={styles.segGlyph}><TypeIcon type={t.key} /></span>
              </button>
              </Tooltip>
            );
          })}
        </div>

        <div className={styles.group} role="group" aria-label="Indicators">
          {IND_DEFS.map((d) => {
            const disabled = d.intradayOnly && !intraday;
            const def = GLOSSARY[d.term]?.short;
            return (
              <Tooltip key={d.key} side="bottom" content={(
                <>
                  <strong>{d.label}</strong>
                  {def && <p>{def}</p>}
                  {disabled && <p>Unavailable on this timeframe — switch to 1m, 5m, 15m or 1h to use it.</p>}
                </>
              )}>
                <button
                  className={styles.pill}
                  data-active={prefs.inds[d.key] && !disabled ? "yes" : "no"}
                  aria-pressed={!!prefs.inds[d.key] && !disabled}
                  disabled={disabled}
                  onClick={() => setPref({ inds: { [d.key]: !prefs.inds[d.key] } })}
                >
                  {d.key.toUpperCase()}
                </button>
              </Tooltip>
            );
          })}
        </div>

        <div className={styles.group} role="group" aria-label="Scale and overlays">
          <Tooltip side="bottom" content={prefs.compare
            ? "Logarithmic price scale — off while comparing with SPY, which uses a percent scale"
            : "Logarithmic price scale: equal distances are equal percentage moves"}>
            <button className={styles.pill} data-active={prefs.logScale && !prefs.compare ? "yes" : "no"}
                    aria-pressed={!!prefs.logScale && !prefs.compare}
                    disabled={prefs.compare}
                    onClick={() => setPref({ logScale: !prefs.logScale })}>LOG</button>
          </Tooltip>
          <Tooltip side="bottom" content="Compare with SPY (S&P 500 ETF) on a percent-change scale">
            <button className={styles.pill} data-active={prefs.compare ? "yes" : "no"}
                    aria-pressed={!!prefs.compare}
                    onClick={() => setPref({ compare: !prefs.compare })}>vs SPY</button>
          </Tooltip>
          {intraday && extSupported && (
            <Tooltip side="bottom" content={prefs.prepost
              ? "Showing pre-market and after-hours bars (shaded) — click for the regular session only"
              : "Regular session only — click to include pre-market and after-hours bars"}>
              <button className={styles.pill} data-active={prefs.prepost ? "yes" : "no"}
                      aria-pressed={prefs.prepost}
                      onClick={() => setPref({ prepost: !prefs.prepost })}>EXT</button>
            </Tooltip>
          )}
          {intraday && !extSupported && (
            <Tooltip side="bottom" content={isTase(ticker)
              ? "Tel Aviv Stock Exchange has no pre-market or after-hours session, so there are no extended-hours bars to show."
              : "This market reports no pre-market or after-hours session, so there are no extended-hours bars to show."}>
              <span className={styles.pillNote} tabIndex={0}>
                REG ONLY
              </span>
            </Tooltip>
          )}
          {analysis && (
            <Tooltip side="bottom" content="Analysis overlays: support/resistance, entry/stop/target, patterns (daily)">
              <button className={styles.pill} data-active={prefs.overlays ? "yes" : "no"}
                      aria-pressed={!!prefs.overlays}
                      onClick={() => setPref({ overlays: !prefs.overlays })}>PLAN</button>
            </Tooltip>
          )}
        </div>

        <div className={styles.actions}>
          {!drawing.synced && (
            <Tooltip side="bottom" content="Drawings are saved on this device; the server copy will catch up on the next load.">
              <span className={styles.offlineNote} tabIndex={0}>
                local
              </span>
            </Tooltip>
          )}
          <DraftsMenu ticker={ticker} tf={prefs.tf} tfLabel={tfLabel} drawing={drawing}
                      onToast={toast.show} onTimeframe={(tf) => setPref({ tf })} disabled={!hasData} />
          <SnapshotMenu capture={captureSnapshot} onToast={toast.show} disabled={!hasData} />
        </div>
      </div>

      {legend && (
        <div className={styles.legend} aria-live="off">
          <span className={styles.legendTicker}>{ticker}</span>
          {ccy && !prefs.compare && (
            <Tooltip side="bottom" content={`Prices in ${CCY_NAMES[ccy] || ccy}${isTase(ticker) ? " — Yahoo's agorot quotes are divided by 100 into shekels" : ""}`}>
              <span className={styles.legendCcy} tabIndex={0}>{sym.trim()} {ccy}</span>
            </Tooltip>
          )}
          {intraday && legend.session && (
            <Tooltip side="bottom" content={legend.session === "pre" ? "Pre-market bar" : legend.session === "post" ? "After-hours bar" : "Regular-session bar"}>
              <span className={styles.sessionTag} data-session={legend.session}>
                {SESSION_LABEL[legend.session]}
              </span>
            </Tooltip>
          )}
          <span>O <em data-tone={toneOf(legend)}>{fmt(legend.open)}</em></span>
          <span>H <em data-tone={toneOf(legend)}>{fmt(legend.high)}</em></span>
          <span>L <em data-tone={toneOf(legend)}>{fmt(legend.low)}</em></span>
          <span>C <em data-tone={toneOf(legend)}>{fmt(legend.close)}</em></span>
          {legend.changePct != null && (
            <span><em data-tone={legend.changePct >= 0 ? "pos" : "neg"}>
              {legend.changePct >= 0 ? "+" : ""}{legend.changePct.toFixed(2)}%
            </em></span>
          )}
          <span>Vol <em>{fmtVol(legend.volume)}</em>
            {legend.volume != null && (
              <span className={styles.volExact}> ({legend.volume.toLocaleString()})</span>
            )}
          </span>
          {prefs.compare && <span className={styles.legendCompare}>vs SPY (%)</span>}
          {!intraday && !prefs.compare && extended?.pre && (
            <Tooltip side="bottom" content={`Pre-market print at ${new Date(extended.pre.time * 1000).toLocaleString()} (latest 1-minute extended-hours bar)`}>
              <span className={styles.extChip} data-session="pre">
                Pre <em>{fmt(extended.pre.price)}</em> · {fmtClock(extended.pre.time)}
              </span>
            </Tooltip>
          )}
          {!intraday && !prefs.compare && extended?.post && (
            <Tooltip side="bottom" content={`After-hours print at ${new Date(extended.post.time * 1000).toLocaleString()} (latest 1-minute extended-hours bar)`}>
              <span className={styles.extChip} data-session="post">
                After <em>{fmt(extended.post.price)}</em> · {fmtClock(extended.post.time)}
              </span>
            </Tooltip>
          )}
          {legend.overlays?.map((o) => (
            <span key={o.label} className={styles.overlayChip}>
              <span className={styles.overlayDot} style={{ background: o.color }} aria-hidden="true" />
              {o.label} <em>{fmt(o.value)}</em>
            </span>
          ))}
        </div>
      )}

      {error ? (
        <div className={styles.message}>
          <p>Chart data unavailable: {error}</p>
          <button className={styles.retry} onClick={loadBars}>Retry</button>
        </div>
      ) : bars && bars.length === 0 ? (
        <div className={styles.message}><p>No {prefs.tf} price history for {ticker}.</p></div>
      ) : null}

      <div className={styles.stage}>
      <DrawingRail drawing={drawing} disabled={!hasData} />
      <div className={styles.canvasWrap} data-tool={drawing.tool ? "yes" : "no"}>
        <div ref={elRef} className={styles.canvas} style={{ width: "100%" }} />
        <ShapeProperties drawing={drawing} palette={drawColors} />
        {drawing.editing && (
          <TextEditor
            key={drawing.editing.shape.id}
            editing={drawing.editing}
            onCommit={drawing.commitText}
            onCancel={drawing.cancelEdit}
          />
        )}
        <span ref={flashRef} className={styles.flash} aria-hidden="true" />
        <ChartToast toast={toast.toast} onDismiss={toast.dismiss} />
        <AnimatePresence>
          {(updating || bars === null) && !error && (
            <motion.div
              key="chart-loader"
              className={styles.loader}
              initial={prefersReducedMotion() ? false : { opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: prefersReducedMotion() ? 0 : 0.18 }}
            >
              <span className={styles.spinner} aria-hidden="true" />
              <span>{bars === null ? `Loading ${prefs.tf} bars…` : "Updating…"}</span>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      </div>

      <p className={styles.key}>
        {prefs.inds.ma && <><span data-c="info">MA20</span><span data-c="accent">MA50</span><span data-c="muted">MA150</span><span data-c="neg">MA200</span></>}
        {prefs.inds.ema && <><span data-c="pos">EMA9</span><span data-c="cmp">EMA21</span></>}
        {prefs.inds.bb && <span data-c="muted">BB(20,2)</span>}
        {prefs.inds.vwap && intraday && <span data-c="cmp">VWAP</span>}
        {prefs.compare && <span data-c="cmp">SPY</span>}
        {prefs.overlays && prefs.tf === "1d" && analysis && !prefs.compare && (
          <span className={styles.keyNote}>dashed = support/resistance · dotted = round numbers · diagonals = trendlines · dots = pattern pivots · arrows = unfilled gaps</span>
        )}
      </p>
    </div>
  );
}
