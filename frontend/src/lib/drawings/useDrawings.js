import { useCallback, useEffect, useRef, useState } from "react";
import { DrawingPrimitive } from "./DrawingPrimitive";
import { loadDrawings, persistDrawings } from "./storage";
import { initialPosition } from "./geometry";
import { TEXTUAL, TOOL_BY_SHORTCUT, pointsNeeded } from "./tools";

export const newId = () => `d${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;

// A stable empty array so "no drawings" never invalidates a downstream memo.
const EMPTY = [];
const HISTORY_LIMIT = 100;
const PREFS_KEY = "chartDrawPrefs";

function loadDrawPrefs() {
  try {
    const raw = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
    return { magnet: !!raw.magnet, hidden: !!raw.hidden, lockAll: !!raw.lockAll };
  } catch {
    return { magnet: false, hidden: false, lockAll: false };
  }
}

/** Preview of a shape under construction with the cursor at `pt`. */
function previewOf(pending, pt) {
  const pts = [...pending.points, pt];
  if (pending.kind === "long" || pending.kind === "short") {
    return { ...pending, points: pts.length >= 2 ? initialPosition(pending.kind, pts[0], pts[1]) : pts };
  }
  return { ...pending, points: pts };
}

const isTyping = (target) => !!target && (
  /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) || target.isContentEditable
);

/**
 * Drawing state and pointer handling for ChartPro.
 *
 * Owns the primitive, the shape list, the active tool, selection, dragging,
 * the undo/redo history and the global magnet / hide / lock toggles. Nothing
 * here touches the chart's own series — drawings live in their own primitive
 * layer, so they're unaffected by indicator toggles or the PLAN overlay, and
 * survive the series rebuild that every pref change triggers.
 *
 * `barsRef` holds the bars currently on screen: points snap to their times
 * (and the magnet to their OHLC). `scopeRef` is the chart's wrapper — keyboard
 * shortcuts only fire while the pointer is over it or focus is inside it, so
 * Ctrl+Z elsewhere on the page is left alone.
 */
export function useDrawings({
  ticker, chartRef, elRef, mainSeriesRef, seriesEpoch, enabled, colors, barsRef, scopeRef,
}) {
  const [tool, setToolState] = useState(null);        // null = select/pan
  // Stamped with the ticker its shapes belong to, so switching stocks shows an
  // empty chart immediately without a synchronous reset inside an effect.
  const [loaded, setLoaded] = useState({ ticker: null, shapes: EMPTY, synced: true });
  const [selectedId, setSelectedId] = useState(null);
  const [drawPrefs, setDrawPrefs] = useState(loadDrawPrefs);
  const [editing, setEditing] = useState(null);       // inline text editor state
  const [historyLen, setHistoryLen] = useState({ past: 0, future: 0, ticker: null });

  const shapes = loaded.ticker === ticker ? loaded.shapes : EMPTY;
  const synced = loaded.ticker === ticker ? loaded.synced : true;

  const primitiveRef = useRef(null);
  const shapesRef = useRef(EMPTY);
  const toolRef = useRef(null);
  const pendingRef = useRef(null);   // shape being built click by click
  const brushRef = useRef(null);     // freehand stroke in progress
  const dragRef = useRef(null);      // { id, handle, moved, last, start }
  const colorsRef = useRef(colors);  // theme colours, resolved by the host
  const prefsRef = useRef(drawPrefs);
  const selectedRef = useRef(null);
  const pastRef = useRef([]);
  const futureRef = useRef([]);
  const hoverRef = useRef(false);

  useEffect(() => { shapesRef.current = shapes; }, [shapes]);
  useEffect(() => { toolRef.current = tool; }, [tool]);
  useEffect(() => { selectedRef.current = selectedId; }, [selectedId]);
  useEffect(() => {
    prefsRef.current = drawPrefs;
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(drawPrefs)); } catch { /* private mode */ }
    primitiveRef.current?.setHidden(drawPrefs.hidden);
  }, [drawPrefs]);
  // Theme change: recolour the live primitive (a fresh one picks it up below).
  useEffect(() => {
    colorsRef.current = colors;
    primitiveRef.current?.setColors(colors);
  }, [colors]);

  // --- load: newest copy wins, a local copy survives a failed push ---
  useEffect(() => {
    let alive = true;
    pastRef.current = [];
    futureRef.current = [];
    loadDrawings(ticker).then((res) => {
      if (!alive) return;
      setLoaded({ ticker, shapes: res.shapes, synced: res.synced });
      setHistoryLen({ past: 0, future: 0, ticker });
    });
    return () => { alive = false; };
  }, [ticker]);

  const setShapes = useCallback((next) => {
    shapesRef.current = next;
    setLoaded((cur) => ({ ...cur, ticker, shapes: next }));
  }, [ticker]);

  const syncHistory = useCallback(() => {
    setHistoryLen({ past: pastRef.current.length, future: futureRef.current.length, ticker });
  }, [ticker]);

  const persist = useCallback((next) => {
    persistDrawings(ticker, next).then(
      (ok) => setLoaded((cur) => (cur.ticker === ticker ? { ...cur, synced: ok } : cur)),
    );
  }, [ticker]);

  /** Apply + persist a new shape list, recording `prev` for undo. */
  const commit = useCallback((next, prev = shapesRef.current) => {
    if (prev !== next) {
      pastRef.current = [...pastRef.current, prev].slice(-HISTORY_LIMIT);
      futureRef.current = [];
      syncHistory();
    }
    setShapes(next);
    persist(next);
  }, [setShapes, persist, syncHistory]);

  const undo = useCallback(() => {
    const prev = pastRef.current[pastRef.current.length - 1];
    if (!prev) return;
    pastRef.current = pastRef.current.slice(0, -1);
    futureRef.current = [...futureRef.current, shapesRef.current];
    syncHistory();
    setShapes(prev);
    persist(prev);
    if (selectedRef.current && !prev.some((s) => s.id === selectedRef.current)) setSelectedId(null);
  }, [setShapes, persist, syncHistory]);

  const redo = useCallback(() => {
    const next = futureRef.current[futureRef.current.length - 1];
    if (!next) return;
    futureRef.current = futureRef.current.slice(0, -1);
    pastRef.current = [...pastRef.current, shapesRef.current];
    syncHistory();
    setShapes(next);
    persist(next);
  }, [setShapes, persist, syncHistory]);

  // --- attach the primitive to whatever the current main series is ---
  useEffect(() => {
    const series = mainSeriesRef.current;
    const chart = chartRef.current;
    if (!enabled || !series || !chart) return undefined;
    const primitive = new DrawingPrimitive();
    primitiveRef.current = primitive;
    primitive.setColors(colorsRef.current);
    primitive.setHidden(prefsRef.current.hidden);
    series.attachPrimitive(primitive);
    primitive.setShapes(shapesRef.current);
    primitive.setSelected(selectedRef.current);
    return () => {
      // Mark it dead first, so nothing else touches the series.
      primitive.kill();
      if (primitiveRef.current === primitive) primitiveRef.current = null;
      // On unmount React runs ChartPro's chart cleanup BEFORE this one, so the
      // chart is already removed and chartRef nulled. Detaching from a series
      // on a disposed chart schedules an async re-layout that then throws
      // "Object is disposed" from the library's own update loop — uncatchable
      // here. If the chart is gone there is nothing to detach from anyway.
      // Reading the *current* ref is the whole point — we're asking "is the
      // chart I attached to still the live one?", so the usual
      // copy-it-into-a-variable advice would defeat the check.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      if (chartRef.current !== chart) return;
      try { series.detachPrimitive?.(primitive); } catch { /* series already gone */ }
    };
    // ChartPro tears down and rebuilds its series on every pref change, so the
    // primitive has to re-attach to the new one — `seriesEpoch` is bumped there.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, seriesEpoch, ticker, chartRef]);

  useEffect(() => { primitiveRef.current?.setShapes(shapes); }, [shapes]);
  useEffect(() => { primitiveRef.current?.setSelected(selectedId); }, [selectedId]);

  // --- coordinates ---
  const indexCacheRef = useRef({ bars: null, map: new Map() });
  const indexOf = useCallback((time) => {
    const bars = barsRef.current || [];
    const cache = indexCacheRef.current;
    if (cache.bars !== bars) {
      cache.bars = bars;
      cache.map = new Map(bars.map((b, i) => [b.time, i]));
    }
    return cache.map.get(time);
  }, [barsRef]);

  /** A logical (fractional bar index) position -> {time, off?} anchor. */
  const anchorAt = useCallback((logical, fractional) => {
    const bars = barsRef.current || [];
    if (!bars.length || logical == null || !Number.isFinite(logical)) return null;
    const last = bars.length - 1;
    const idx = Math.max(0, Math.min(last, Math.round(logical)));
    let off = logical - idx;
    if (!fractional) off = Math.round(off); // whole bars (0 inside the data)
    off = Math.round(off * 1000) / 1000;
    return off ? { time: bars[idx].time, off } : { time: bars[idx].time };
  }, [barsRef]);

  /** Screen px (client coords) -> the nearest (time, price[, off]) anchor. */
  const toPoint = useCallback((clientX, clientY, { fractional = false } = {}) => {
    const chart = chartRef.current;
    const el = elRef.current;
    const series = mainSeriesRef.current;
    if (!chart || !el || !series) return null;
    const rect = el.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    let logical;
    let price;
    try {
      logical = chart.timeScale().coordinateToLogical(x);
      price = series.coordinateToPrice(y);
    } catch {
      return null;
    }
    if (logical == null || price == null) return null;
    const anchor = anchorAt(logical, fractional);
    if (!anchor) return null;
    // Magnet: snap to the nearest of the bar's O/H/L/C (on screen).
    if (prefsRef.current.magnet && !fractional && !anchor.off) {
      const bar = (barsRef.current || [])[indexOf(anchor.time)];
      if (bar) {
        let best = price;
        let bestD = Infinity;
        for (const v of [bar.open, bar.high, bar.low, bar.close]) {
          const vy = series.priceToCoordinate(v);
          if (vy == null) continue;
          const d = Math.abs(vy - y);
          if (d < bestD) { bestD = d; best = v; }
        }
        price = best;
      }
    }
    return { ...anchor, price };
  }, [chartRef, elRef, mainSeriesRef, anchorAt, indexOf, barsRef]);

  /** Shift an anchor by `dBars` bars and `dPrice` in price. */
  const shiftPoint = useCallback((p, dBars, dPrice) => {
    const idx = indexOf(p.time);
    const moved = idx == null ? null : anchorAt(idx + (p.off || 0) + dBars, true);
    const base = moved || { time: p.time, ...(p.off ? { off: p.off } : {}) };
    return { ...base, price: p.price + dPrice };
  }, [indexOf, anchorAt]);

  const localXY = useCallback((e) => {
    const rect = elRef.current.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }, [elRef]);

  /** Screen position (relative to the chart element) of an anchor. */
  const toScreen = useCallback((pt) => primitiveRef.current?.toScreen(pt) ?? null, []);

  // --- tool state ---
  const cancelPending = useCallback(() => {
    pendingRef.current = null;
    brushRef.current = null;
    primitiveRef.current?.setDraft(null);
  }, []);

  const setTool = useCallback((t) => {
    cancelPending();
    setEditing(null);
    setToolState((cur) => (cur === t ? null : t));
    if (t) setSelectedId(null);
  }, [cancelPending]);

  /** Open the inline editor for a new text/callout shape or an existing one. */
  const openEditor = useCallback((shape, isNew) => {
    const anchor = shape.kind === "callout" ? shape.points[1] : shape.points[0];
    const pos = primitiveRef.current?.toScreen(anchor);
    if (!pos) return;
    setEditing({ shape, isNew, x: pos.x, y: pos.y, text: shape.text || "" });
  }, []);

  const startEdit = useCallback((id) => {
    const shape = shapesRef.current.find((s) => s.id === id);
    if (shape && TEXTUAL.has(shape.kind)) openEditor(shape, false);
  }, [openEditor]);

  // Side effects stay out of the setState updater (StrictMode runs updaters
  // twice), so the open editor is read from a ref.
  const editingRef = useRef(null);
  useEffect(() => { editingRef.current = editing; }, [editing]);

  const commitText = useCallback((text) => {
    const ed = editingRef.current;
    editingRef.current = null;
    setEditing(null);
    if (!ed) return;
    const value = String(text || "").trim();
    if (ed.isNew) {
      if (value) {
        commit([...shapesRef.current, { ...ed.shape, text: value }]);
        setSelectedId(ed.shape.id);
      }
    } else if (value && value !== ed.shape.text) {
      commit(shapesRef.current.map((s) => (s.id === ed.shape.id ? { ...s, text: value } : s)));
    }
  }, [commit]);

  const cancelEdit = useCallback(() => setEditing(null), []);

  /** Finish a click-built shape: commit it (or open the text editor first). */
  const finishShape = useCallback((shape) => {
    cancelPending();
    setToolState(null);
    if (TEXTUAL.has(shape.kind)) {
      openEditor(shape, true);
      return;
    }
    commit([...shapesRef.current, shape]);
    setSelectedId(shape.id);
  }, [cancelPending, commit, openEditor]);

  // --- pointer handling ---
  useEffect(() => {
    const el = elRef.current;
    if (!enabled || !el) return undefined;

    const onPointerDown = (e) => {
      const primitive = primitiveRef.current;
      if (!primitive || e.button !== 0) return;
      const activeTool = toolRef.current;

      if (!activeTool) {
        // select / drag existing
        const { x, y } = localXY(e);
        const hit = primitive.findShape(x, y);
        if (hit) {
          e.preventDefault();
          e.stopPropagation();
          setSelectedId(hit.shape.id);
          const locked = prefsRef.current.lockAll || hit.shape.locked;
          if (!locked) {
            dragRef.current = {
              id: hit.shape.id, handle: hit.handle, moved: false,
              last: toPoint(e.clientX, e.clientY, { fractional: true }),
              lastLogical: null,
              start: shapesRef.current,
            };
            el.setPointerCapture?.(e.pointerId);
          }
        } else {
          setSelectedId(null);
        }
        return;
      }

      // drawing: consume the click so the chart doesn't pan
      e.preventDefault();
      e.stopPropagation();

      if (activeTool === "brush") {
        const pt = toPoint(e.clientX, e.clientY, { fractional: true });
        if (!pt) return;
        brushRef.current = { id: newId(), kind: "brush", points: [pt], last: localXY(e) };
        primitive.setDraft(brushRef.current);
        el.setPointerCapture?.(e.pointerId);
        return;
      }

      const pt = toPoint(e.clientX, e.clientY);
      if (!pt) return;
      const need = pointsNeeded(activeTool);
      const pending = pendingRef.current || { id: newId(), kind: activeTool, points: [] };
      const points = [...pending.points, pt];
      if (points.length >= need) {
        const shape = { ...pending, points };
        if (activeTool === "long" || activeTool === "short") {
          shape.points = initialPosition(activeTool, points[0], points[1]);
        }
        finishShape(shape);
        return;
      }
      pendingRef.current = { ...pending, points };
      primitive.setDraft(previewOf(pendingRef.current, pt));
    };

    const onPointerMove = (e) => {
      const primitive = primitiveRef.current;
      if (!primitive) return;

      if (brushRef.current) {
        const p = localXY(e);
        const last = brushRef.current.last;
        if (Math.hypot(p.x - last.x, p.y - last.y) < 3) return;
        const pt = toPoint(e.clientX, e.clientY, { fractional: true });
        if (!pt) return;
        brushRef.current = { ...brushRef.current, points: [...brushRef.current.points, pt], last: p };
        primitive.setDraft(brushRef.current);
        return;
      }

      // live preview of the next point
      if (pendingRef.current) {
        const pt = toPoint(e.clientX, e.clientY);
        if (pt) primitive.setDraft(previewOf(pendingRef.current, pt));
        return;
      }

      const drag = dragRef.current;
      if (!drag) {
        // cursor affordance over an existing shape
        const { x, y } = localXY(e);
        el.style.cursor = toolRef.current ? "crosshair" : (primitive.findShape(x, y) ? "pointer" : "");
        return;
      }

      e.preventDefault();
      e.stopPropagation();
      const next = shapesRef.current.map((s) => {
        if (s.id !== drag.id) return s;
        if (drag.handle != null) {
          const pt = toPoint(e.clientX, e.clientY);
          if (!pt) return s;
          const points = s.points.slice();
          if ((s.kind === "long" || s.kind === "short") && drag.handle > 0) {
            // target/stop share the right edge: moving one moves both edges
            const other = drag.handle === 1 ? 2 : 1;
            points[drag.handle] = pt;
            const { price } = points[other];
            points[other] = { ...pt, price };
          } else {
            points[drag.handle] = pt;
          }
          return { ...s, points };
        }
        // whole-shape move: shift every point by the pointer delta (bars + price)
        const cur = toPoint(e.clientX, e.clientY, { fractional: true });
        if (!cur || !drag.last) return s;
        const i0 = indexOf(drag.last.time);
        const i1 = indexOf(cur.time);
        if (i0 == null || i1 == null) return s;
        const dBars = Math.round((i1 + (cur.off || 0)) - (i0 + (drag.last.off || 0)));
        const dPrice = cur.price - drag.last.price;
        if (!dBars && !dPrice) return s;
        // Only consume the whole-bar part of the time delta, so slow drags
        // accumulate instead of rounding away.
        const consumed = anchorAt(i0 + (drag.last.off || 0) + dBars, true);
        drag.pendingLast = { ...(consumed || drag.last), price: cur.price };
        return { ...s, points: s.points.map((p) => shiftPoint(p, dBars, dPrice)) };
      });
      if (drag.pendingLast) { drag.last = drag.pendingLast; drag.pendingLast = null; }
      drag.moved = true;
      setShapes(next);
      primitive.setShapes(next);
    };

    const onPointerUp = () => {
      const brush = brushRef.current;
      if (brush) {
        brushRef.current = null;
        primitiveRef.current?.setDraft(null);
        if (brush.points.length >= 2) {
          const { last: _last, ...shape } = brush; // eslint-disable-line no-unused-vars
          commit([...shapesRef.current, shape]);
        }
        return;
      }
      const drag = dragRef.current;
      dragRef.current = null;
      if (drag?.moved) commit(shapesRef.current, drag.start);
    };

    const onDblClick = (e) => {
      if (toolRef.current) return;
      const { x, y } = localXY(e);
      const hit = primitiveRef.current?.findShape(x, y);
      if (hit && TEXTUAL.has(hit.shape.kind) && !prefsRef.current.lockAll && !hit.shape.locked) {
        e.preventDefault();
        e.stopPropagation();
        startEdit(hit.shape.id);
      }
    };

    el.addEventListener("pointerdown", onPointerDown, { capture: true });
    el.addEventListener("pointermove", onPointerMove, { capture: true });
    el.addEventListener("dblclick", onDblClick);
    window.addEventListener("pointerup", onPointerUp);
    return () => {
      el.removeEventListener("pointerdown", onPointerDown, { capture: true });
      el.removeEventListener("pointermove", onPointerMove, { capture: true });
      el.removeEventListener("dblclick", onDblClick);
      window.removeEventListener("pointerup", onPointerUp);
    };
  }, [enabled, elRef, toPoint, localXY, commit, setShapes, finishShape, indexOf, shiftPoint, startEdit, anchorAt]);

  // --- actions ---
  const deleteShape = useCallback((id) => {
    if (!id) return;
    commit(shapesRef.current.filter((s) => s.id !== id));
    if (selectedRef.current === id) setSelectedId(null);
  }, [commit]);

  const deleteSelected = useCallback(() => deleteShape(selectedRef.current), [deleteShape]);

  const clearAll = useCallback(() => {
    setSelectedId(null);
    commit([]);
  }, [commit]);

  const updateShape = useCallback((id, patch) => {
    commit(shapesRef.current.map((s) => (s.id === id ? { ...s, ...patch } : s)));
  }, [commit]);

  /** Replace every drawing (e.g. loading a draft). Undoable. */
  const replaceShapes = useCallback((next) => {
    setSelectedId(null);
    commit(Array.isArray(next) ? next : []);
  }, [commit]);

  /** Add shapes alongside the current ones, re-keying any clashing ids. */
  const mergeShapes = useCallback((extra) => {
    const taken = new Set(shapesRef.current.map((s) => s.id));
    const added = (extra || []).map((s) => (taken.has(s.id) ? { ...s, id: newId() } : s));
    commit([...shapesRef.current, ...added]);
  }, [commit]);

  const setPref = useCallback((patch) => setDrawPrefs((p) => ({ ...p, ...patch })), []);

  // --- keyboard: Esc / Del / undo / redo / Alt+<tool> ---
  useEffect(() => {
    const scope = scopeRef?.current;
    if (!enabled) return undefined;
    const onEnter = () => { hoverRef.current = true; };
    const onLeave = () => { hoverRef.current = false; };
    scope?.addEventListener("pointerenter", onEnter);
    scope?.addEventListener("pointerleave", onLeave);

    const onKeyDown = (e) => {
      if (isTyping(e.target)) return;
      const busy = !!(pendingRef.current || brushRef.current || toolRef.current);
      const active = busy || hoverRef.current || (scope && scope.contains(document.activeElement));
      if (!active) return;

      if (e.key === "Escape") {
        if (pendingRef.current || brushRef.current) cancelPending();
        else if (toolRef.current) setToolState(null);
        else setSelectedId(null);
        return;
      }
      const mod = e.ctrlKey || e.metaKey;
      if (mod && !e.altKey) {
        const k = e.key.toLowerCase();
        if (k === "z" && !e.shiftKey) { e.preventDefault(); undo(); return; }
        if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); redo(); return; }
        return;
      }
      if ((e.key === "Delete" || e.key === "Backspace") && selectedRef.current) {
        const shape = shapesRef.current.find((s) => s.id === selectedRef.current);
        if (!shape || shape.locked || prefsRef.current.lockAll) return;
        e.preventDefault();
        deleteShape(shape.id);
        return;
      }
      if (e.altKey && !mod && !e.shiftKey) {
        // e.code keeps working when Alt changes the produced character (macOS).
        const letter = /^Key([A-Z])$/.exec(e.code || "")?.[1];
        const key = letter && TOOL_BY_SHORTCUT[letter];
        if (key) {
          e.preventDefault();
          cancelPending();
          setEditing(null);
          setSelectedId(null);
          setToolState((cur) => (cur === key ? null : key));
        }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      scope?.removeEventListener("pointerenter", onEnter);
      scope?.removeEventListener("pointerleave", onLeave);
    };
  }, [enabled, scopeRef, undo, redo, deleteShape, cancelPending]);

  const history = historyLen.ticker === ticker ? historyLen : { past: 0, future: 0 };
  const selected = shapes.find((s) => s.id === selectedId) || null;

  return {
    tool,
    setTool,
    shapes,
    selectedId,
    selected,
    setSelectedId,
    synced,
    clearAll,
    deleteSelected,
    deleteShape,
    updateShape,
    replaceShapes,
    mergeShapes,
    undo,
    redo,
    canUndo: history.past > 0,
    canRedo: history.future > 0,
    magnet: drawPrefs.magnet,
    hidden: drawPrefs.hidden,
    lockAll: drawPrefs.lockAll,
    setMagnet: (v) => setPref({ magnet: v }),
    setHidden: (v) => setPref({ hidden: v }),
    setLockAll: (v) => setPref({ lockAll: v }),
    editing,
    startEdit,
    commitText,
    cancelEdit,
    toScreen,
  };
}
