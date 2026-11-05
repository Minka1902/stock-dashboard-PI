/**
 * Background bands behind pre-market and after-hours bars (intraday + EXT).
 *
 * A lightweight-charts v5 series primitive with a `bottom` z-order pane view,
 * so the bands sit under the candles and grid-independent. Which bars are
 * extended comes from the backend's per-bar `session` tag (Yahoo's own
 * trading periods for the exchange) — nothing here knows market hours.
 */
export class SessionPrimitive {
  constructor() {
    this._bars = [];            // [{ time, session }]
    this._colors = { pre: "rgba(128,128,128,0.08)", post: "rgba(128,128,128,0.08)" };
    this._chart = null;
    this._requestUpdate = null;
    this._dead = false;
    const renderer = { draw: () => {}, drawBackground: (target) => this._draw(target) };
    this._paneViews = [{ renderer: () => renderer, zOrder: () => "bottom" }];
  }

  attached({ chart, requestUpdate }) {
    this._chart = chart;
    this._requestUpdate = requestUpdate;
    this._dead = false;
  }

  detached() {
    this._dead = true;
    this._chart = null;
    this._requestUpdate = null;
  }

  paneViews() { return this._paneViews; }
  updateAllViews() {}

  setBars(bars) {
    this._bars = (bars || []).filter((b) => b.session === "pre" || b.session === "post")
      .map((b) => ({ time: b.time, session: b.session }));
    this._redraw();
  }

  /** { pre, post } canvas-safe fill colours. */
  setColors(colors) { this._colors = { ...this._colors, ...colors }; this._redraw(); }

  _redraw() {
    if (this._dead) return;
    try { this._requestUpdate?.(); } catch { this._dead = true; }
  }

  _draw(target) {
    if (this._dead || !this._chart || !this._bars.length) return;
    let ts;
    try { ts = this._chart.timeScale(); } catch { return; }
    const spacing = ts.options().barSpacing || 6;
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      // Merge adjacent same-session bars into one band: fewer fills, no seams.
      let band = null;
      const flush = () => {
        if (!band) return;
        ctx.fillStyle = this._colors[band.session];
        ctx.fillRect(band.x1, 0, band.x2 - band.x1, mediaSize.height);
        band = null;
      };
      for (const b of this._bars) {
        let x;
        try { x = ts.timeToCoordinate(b.time); } catch { x = null; }
        if (x == null || x < -spacing || x > mediaSize.width + spacing) { flush(); continue; }
        const x1 = x - spacing / 2;
        const x2 = x + spacing / 2;
        if (band && band.session === b.session && x1 - band.x2 <= 1) {
          band.x2 = x2;
        } else {
          flush();
          band = { session: b.session, x1, x2 };
        }
      }
      flush();
    });
  }
}
