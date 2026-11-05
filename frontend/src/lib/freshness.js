import { freshnessTone } from "./format";

const AGE = {
  fresh: "under 1 hour old",
  mid: "1–6 hours old",
  stale: "over 6 hours old",
};

/**
 * Tooltip text for a relative "Updated" cell: the exact fetch time plus what
 * its colour means (the bands are freshnessTone's).
 */
export function freshnessTip(iso) {
  if (!iso) return "Not fetched yet";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "Fetch time unknown";
  return `Fetched ${d.toLocaleString()} — ${AGE[freshnessTone(iso)]}`;
}
