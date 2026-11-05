// Deterministic avatar helpers: initials + a stable gradient derived from a
// string hash, mapped onto the design-token accent hues. Shared by the X feed
// (account avatars) and the user menu.

const HUES = [
  ["var(--accent)", "var(--info)"],
  ["var(--info)", "var(--positive)"],
  ["var(--positive)", "var(--accent)"],
  ["var(--chart-compare)", "var(--accent)"],
];

function hashString(str) {
  let h = 0;
  for (let i = 0; i < str.length; i += 1) {
    h = (h << 5) - h + str.charCodeAt(i);
    h |= 0; // 32-bit
  }
  return Math.abs(h);
}

/** Initials for an avatar: from an email local part or a plain handle. */
export function initialsFor(value = "") {
  const local = value.includes("@") ? value.split("@")[0] : value;
  const parts = local.split(/[.\-_ ]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return (local.slice(0, 2) || "?").toUpperCase();
}

/** A stable `linear-gradient(...)` background for `value`. */
export function gradientFor(value = "") {
  const [a, b] = HUES[hashString(value) % HUES.length];
  return `linear-gradient(135deg, ${a}, ${b})`;
}
