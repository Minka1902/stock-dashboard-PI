// Formatting helpers shared across the dashboard.

const compactCurrency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});

const fullCurrency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

// $48.0B — genuinely-USD data only (contracts, insider/congress trades,
// margin debt). Prices and holdings use formatMoney with their own currency.
export function formatCurrencyCompact(n) {
  if (n == null || Number.isNaN(n)) return "—";
  return compactCurrency.format(n);
}

// $48,063,763,681 — genuinely-USD data only (see above).
export function formatCurrencyFull(n) {
  if (n == null || Number.isNaN(n)) return "—";
  return fullCurrency.format(n);
}

// ---- multi-currency ----
// Intl.NumberFormat is expensive to build, so one per (currency, style) key.
const moneyFormatters = new Map();

function moneyFormatter(currency, { compact = false, digits } = {}) {
  const code = (currency || "USD").toUpperCase();
  const key = `${code}|${compact ? "c" : "f"}|${digits ?? ""}`;
  let f = moneyFormatters.get(key);
  if (!f) {
    try {
      f = new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: code,
        // "narrowSymbol" gives ₪ / € / £ rather than "ILS 1.00".
        currencyDisplay: "narrowSymbol",
        ...(compact
          ? { notation: "compact", maximumFractionDigits: 1 }
          : { minimumFractionDigits: digits ?? 2, maximumFractionDigits: digits ?? 2 }),
      });
    } catch {
      // Unknown ISO code: fall back to a plain number with the code in front.
      const plain = new Intl.NumberFormat("en-US", {
        ...(compact ? { notation: "compact", maximumFractionDigits: 1 }
          : { minimumFractionDigits: digits ?? 2, maximumFractionDigits: digits ?? 2 }),
      });
      f = { format: (n) => `${code} ${plain.format(n)}` };
    }
    moneyFormatters.set(key, f);
  }
  return f;
}

/**
 * Money in its own currency: formatMoney(120.5, "ILS") → "₪120.50",
 * formatMoney(48e9, "USD", { compact: true }) → "$48B". `digits` fixes the
 * decimals for non-compact output (default 2). null/NaN → "—".
 */
export function formatMoney(n, currency = "USD", opts = {}) {
  if (n == null || Number.isNaN(n)) return "—";
  return moneyFormatter(currency, opts).format(n);
}

/** Signed money: "+₪1.2K" / "−$340". Used for P/L. */
export function formatMoneySigned(n, currency = "USD", opts = {}) {
  if (n == null || Number.isNaN(n)) return "—";
  return `${n >= 0 ? "+" : "−"}${formatMoney(Math.abs(n), currency, opts)}`;
}

const SYMBOLS = { USD: "$", ILS: "₪", EUR: "€", GBP: "£", JPY: "¥" };

/** "$", "₪", "€"… or the ISO code plus a space for anything else. */
export function currencySymbol(currency) {
  if (!currency) return "";
  return SYMBOLS[currency.toUpperCase()] ?? `${currency.toUpperCase()} `;
}

/** A price with its currency symbol and fixed decimals: "₪120.50". */
export function formatPrice(n, currency = "USD", digits = 2) {
  if (n == null || Number.isNaN(Number(n))) return "—";
  return `${currencySymbol(currency)}${Number(n).toFixed(digits)}`;
}

/** Trading currency implied by a Yahoo symbol (".TA" → ILS, bare → USD). */
export function currencyForSymbol(ticker) {
  const t = String(ticker || "").trim().toUpperCase();
  if (!t) return null;
  if (t.endsWith(".TA")) return "ILS";
  if (t.endsWith("=X")) return t.length >= 8 ? t.slice(3, 6) : null;
  if (t.includes(".")) return null;
  return "USD";
}

/** "US" | "TASE" | "FX" | "OTHER" — mirrors backend currency.market_for_symbol. */
export function marketForSymbol(ticker) {
  const t = String(ticker || "").toUpperCase();
  if (!t) return "OTHER";
  if (t.endsWith("=X")) return "FX";
  if (t.endsWith(".TA")) return "TASE";
  if (t.includes(".")) return "OTHER";
  return "US";
}

// 1,234
export function formatCount(n) {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US");
}

// "just now" / "2 min ago" / "3 hr ago" / locale date for older
/**
 * Time until a FUTURE instant ("in 3 min", "overdue").
 *
 * formatRelativeTime is past-tense: a future timestamp gives it a negative
 * diff, which lands in its "just now" branch — so a job scheduled hours out
 * reads as if it were about to run.
 */
export function formatUntil(iso) {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const sec = Math.round((then - Date.now()) / 1000);
  if (sec <= 0) return "due";
  if (sec < 60) return `in ${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `in ${min} min`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `in ${hr}h ${min % 60}m`;
  return `in ${Math.floor(hr / 24)}d ${hr % 24}h`;
}

export function formatRelativeTime(iso) {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "never";
  const diffSec = Math.round((Date.now() - then) / 1000);
  if (diffSec < 5) return "just now";
  if (diffSec < 60) return `${diffSec} sec ago`;
  const min = Math.floor(diffSec / 60);
  if (min < 60) return `${min} min ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} hr ago`;
  const days = Math.floor(hr / 24);
  if (days < 7) return `${days} day${days === 1 ? "" : "s"} ago`;
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

// Signed percent from a fraction: 0.032 → "+3.2%", -0.011 → "−1.1%"
export function formatPercentSigned(v, digits = 1) {
  if (v == null || Number.isNaN(v)) return "—";
  const pct = v * 100;
  const sign = pct > 0 ? "+" : pct < 0 ? "−" : "";
  return `${sign}${Math.abs(pct).toFixed(digits)}%`;
}

// Freshness bucket from an ISO timestamp: "fresh" (<1h), "mid" (1–6h), "stale" (>6h).
// Lives here (not in components) so the clock read stays out of render purity checks.
export function freshnessTone(iso) {
  if (!iso) return "stale";
  const hrAgo = (Date.now() - new Date(iso).getTime()) / 3600000;
  if (Number.isNaN(hrAgo)) return "stale";
  return hrAgo < 1 ? "fresh" : hrAgo < 6 ? "mid" : "stale";
}

// short date or em dash
export function formatDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}
