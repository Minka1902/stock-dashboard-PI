// The handful of formatters the extension needs. Trimmed copy of
// frontend/src/lib/format.js — copied rather than imported to keep the
// extension's module graph free of the frontend's Vite/CSS-Modules setup.

export function formatRelativeTime(iso) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const secs = Math.round((Date.now() - then) / 1000);
  if (secs < 60) return "just now";
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function formatPrice(n) {
  if (n == null || Number.isNaN(n)) return "—";
  return Number(n).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function formatPct(n) {
  if (n == null || Number.isNaN(n)) return "—";
  const v = Number(n);
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

/** "positive" / "negative" / "flat" — drives the token colour. */
export function toneOf(n) {
  if (n == null || Number.isNaN(n)) return "flat";
  if (n > 0) return "positive";
  if (n < 0) return "negative";
  return "flat";
}
