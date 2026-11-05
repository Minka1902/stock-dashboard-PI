/**
 * Chart screenshot + share (Task 5).
 *
 * lightweight-charts' `chart.takeScreenshot(addTopLayer, includeCrosshair)`
 * renders the whole chart — every pane, both axes, the visible range exactly
 * as it is on screen. `addTopLayer=true` is what brings in primitives drawn on
 * the top layer (our DrawingPrimitive uses zOrder "top"); the crosshair is
 * left out so the image shows the chart, not where the mouse happened to be.
 *
 * The chart canvas is transparent, so it is composited onto the theme
 * surface under a header strip (ticker, timeframe, last price, time, the
 * active studies and the "signals, not predictions" line).
 */

const HEADER_CSS_PX = 58;
const PAD_CSS_PX = 14;

function font(weight, px, ratio, family) {
  return `${weight} ${Math.round(px * ratio)}px ${family}`;
}

/**
 * Compose the snapshot canvas.
 * @param {HTMLCanvasElement} shot  chart.takeScreenshot(true, false)
 * @param {object} info  { cssWidth, ticker, timeframe, price, changePct,
 *   studies: string[], note?: string, colors: {bg, text, muted, faint, up, down, accent, border}, fontFamily, monoFamily }
 */
export function composeSnapshot(shot, info) {
  const ratio = info.cssWidth ? shot.width / info.cssWidth : (window.devicePixelRatio || 1);
  const header = Math.round(HEADER_CSS_PX * ratio);
  const pad = Math.round(PAD_CSS_PX * ratio);
  const out = document.createElement("canvas");
  out.width = shot.width;
  out.height = shot.height + header + pad;
  const ctx = out.getContext("2d");
  const c = info.colors;

  ctx.fillStyle = c.bg;
  ctx.fillRect(0, 0, out.width, out.height);

  // header strip
  const mono = info.monoFamily || "monospace";
  const sans = info.fontFamily || "system-ui, sans-serif";
  let x = pad;
  const line1 = Math.round(24 * ratio);
  const line2 = Math.round(44 * ratio);
  ctx.textBaseline = "alphabetic";
  ctx.fillStyle = c.text;
  ctx.font = font(700, 17, ratio, sans);
  ctx.fillText(info.ticker, x, line1);
  x += ctx.measureText(info.ticker).width + Math.round(10 * ratio);

  ctx.font = font(600, 12, ratio, mono);
  ctx.fillStyle = c.accent;
  const tf = info.timeframe;
  ctx.fillText(tf, x, line1);
  x += ctx.measureText(tf).width + Math.round(12 * ratio);

  if (info.price != null) {
    ctx.fillStyle = c.text;
    ctx.font = font(600, 14, ratio, mono);
    // With its currency symbol ("₪120.50" for a TASE listing).
    const p = `${info.currencySymbol || ""}${Number(info.price).toFixed(2)}`;
    ctx.fillText(p, x, line1);
    x += ctx.measureText(p).width + Math.round(8 * ratio);
    if (info.changePct != null) {
      ctx.fillStyle = info.changePct >= 0 ? c.up : c.down;
      ctx.font = font(600, 12, ratio, mono);
      ctx.fillText(`${info.changePct >= 0 ? "+" : ""}${info.changePct.toFixed(2)}%`, x, line1);
    }
  }

  ctx.fillStyle = c.muted;
  ctx.font = font(400, 11, ratio, mono);
  const meta = [info.stamp, ...(info.studies || [])].filter(Boolean).join("  ·  ");
  ctx.fillText(meta, pad, line2);

  // right-aligned product line
  ctx.textAlign = "right";
  ctx.fillStyle = c.faint;
  ctx.font = font(600, 11, ratio, sans);
  ctx.fillText("Signals, not predictions", out.width - pad, line1);
  if (info.note) {
    ctx.font = font(400, 10.5, ratio, mono);
    ctx.fillText(info.note, out.width - pad, line2);
  }
  ctx.textAlign = "left";

  // divider
  ctx.fillStyle = c.border;
  ctx.fillRect(pad, header - Math.max(1, Math.round(ratio)), out.width - pad * 2, Math.max(1, Math.round(ratio)));

  ctx.drawImage(shot, 0, header);
  return out;
}

export function canvasToBlob(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("could not encode PNG"))), "image/png");
  });
}

export function snapshotFileName(ticker, timeframe, date = new Date()) {
  const pad2 = (n) => String(n).padStart(2, "0");
  const stamp = `${date.getFullYear()}${pad2(date.getMonth() + 1)}${pad2(date.getDate())}-${pad2(date.getHours())}${pad2(date.getMinutes())}`;
  return `${ticker}-${timeframe}-${stamp}.png`.replace(/[^A-Za-z0-9._-]/g, "_");
}

/** True when the Web Share API can take this file (mobile, Edge/Chrome on Windows). */
export function canShareFile(file) {
  try {
    return typeof navigator !== "undefined" && !!navigator.canShare && navigator.canShare({ files: [file] });
  } catch {
    return false;
  }
}

export function canCopyImage() {
  return typeof window !== "undefined" && !!window.ClipboardItem && !!navigator.clipboard?.write;
}

export async function copyImage(blob) {
  // A promise-valued ClipboardItem keeps Safari's user-activation window open.
  await navigator.clipboard.write([new window.ClipboardItem({ "image/png": Promise.resolve(blob) })]);
}

export function downloadBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}
