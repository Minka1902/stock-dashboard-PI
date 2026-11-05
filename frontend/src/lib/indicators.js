// Pure indicator math for the pro chart. Every function takes bars
// ([{time, open, high, low, close, volume}]) or closes and returns
// lightweight-charts-ready point arrays ({time, value}), skipping the
// warm-up region instead of padding it with nulls.

export function smaSeries(bars, period) {
  const out = [];
  let sum = 0;
  for (let i = 0; i < bars.length; i++) {
    sum += bars[i].close;
    if (i >= period) sum -= bars[i - period].close;
    if (i >= period - 1) out.push({ time: bars[i].time, value: sum / period });
  }
  return out;
}

export function emaSeries(bars, period) {
  if (bars.length < period) return [];
  const k = 2 / (period + 1);
  let seed = 0;
  for (let i = 0; i < period; i++) seed += bars[i].close;
  let prev = seed / period;
  const out = [{ time: bars[period - 1].time, value: prev }];
  for (let i = period; i < bars.length; i++) {
    prev = bars[i].close * k + prev * (1 - k);
    out.push({ time: bars[i].time, value: prev });
  }
  return out;
}

export function bollingerSeries(bars, period = 20, mult = 2) {
  const upper = [], middle = [], lower = [];
  for (let i = period - 1; i < bars.length; i++) {
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += bars[j].close;
    const mean = sum / period;
    let variance = 0;
    for (let j = i - period + 1; j <= i; j++) variance += (bars[j].close - mean) ** 2;
    const sd = Math.sqrt(variance / period);
    const t = bars[i].time;
    middle.push({ time: t, value: mean });
    upper.push({ time: t, value: mean + mult * sd });
    lower.push({ time: t, value: mean - mult * sd });
  }
  return { upper, middle, lower };
}

// Wilder-smoothed RSI, same definition as the backend engine.
export function rsiSeries(bars, period = 14) {
  if (bars.length < period + 1) return [];
  let avgG = 0, avgL = 0;
  for (let i = 1; i <= period; i++) {
    const d = bars[i].close - bars[i - 1].close;
    if (d > 0) avgG += d; else avgL -= d;
  }
  avgG /= period; avgL /= period;
  const out = [{
    time: bars[period].time,
    value: avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL),
  }];
  for (let i = period + 1; i < bars.length; i++) {
    const d = bars[i].close - bars[i - 1].close;
    avgG = (avgG * (period - 1) + Math.max(d, 0)) / period;
    avgL = (avgL * (period - 1) + Math.max(-d, 0)) / period;
    out.push({
      time: bars[i].time,
      value: avgL === 0 ? 100 : 100 - 100 / (1 + avgG / avgL),
    });
  }
  return out;
}

export function macdSeries(bars, fast = 12, slow = 26, signalPeriod = 9) {
  if (bars.length < slow + signalPeriod) return { macd: [], signal: [], hist: [] };
  const emaOf = (period) => {
    const k = 2 / (period + 1);
    let seed = 0;
    for (let i = 0; i < period; i++) seed += bars[i].close;
    let prev = seed / period;
    const vals = new Array(bars.length).fill(null);
    vals[period - 1] = prev;
    for (let i = period; i < bars.length; i++) {
      prev = bars[i].close * k + prev * (1 - k);
      vals[i] = prev;
    }
    return vals;
  };
  const fastE = emaOf(fast), slowE = emaOf(slow);
  const macdPts = [];
  for (let i = slow - 1; i < bars.length; i++) {
    macdPts.push({ time: bars[i].time, value: fastE[i] - slowE[i] });
  }
  // signal = EMA(signalPeriod) of the MACD line, seeded with its SMA
  const k = 2 / (signalPeriod + 1);
  let seed = 0;
  for (let i = 0; i < signalPeriod; i++) seed += macdPts[i].value;
  let prev = seed / signalPeriod;
  const signal = [{ time: macdPts[signalPeriod - 1].time, value: prev }];
  for (let i = signalPeriod; i < macdPts.length; i++) {
    prev = macdPts[i].value * k + prev * (1 - k);
    signal.push({ time: macdPts[i].time, value: prev });
  }
  const macd = macdPts.slice(signalPeriod - 1);
  const hist = macd.map((p, i) => ({ time: p.time, value: p.value - signal[i].value }));
  return { macd, signal, hist };
}

// Session-anchored VWAP (intraday only): cumulative typical-price x volume,
// reset each new UTC session day.
export function vwapSeries(bars) {
  const out = [];
  let cumPV = 0, cumV = 0, day = null;
  for (const b of bars) {
    const d = typeof b.time === "number"
      ? Math.floor(b.time / 86400)
      : b.time;
    if (d !== day) { day = d; cumPV = 0; cumV = 0; }
    const typical = (b.high + b.low + b.close) / 3;
    cumPV += typical * (b.volume || 0);
    cumV += b.volume || 0;
    if (cumV > 0) out.push({ time: b.time, value: cumPV / cumV });
  }
  return out;
}

// Heikin-Ashi transform: smoothed candles derived from real OHLC.
export function heikinAshi(bars) {
  const out = [];
  let prevOpen = null, prevClose = null;
  for (const b of bars) {
    const close = (b.open + b.high + b.low + b.close) / 4;
    const open = prevOpen == null ? (b.open + b.close) / 2 : (prevOpen + prevClose) / 2;
    out.push({
      time: b.time,
      open,
      close,
      high: Math.max(b.high, open, close),
      low: Math.min(b.low, open, close),
      volume: b.volume,
      ...(b.session ? { session: b.session } : {}),
    });
    prevOpen = open;
    prevClose = close;
  }
  return out;
}
