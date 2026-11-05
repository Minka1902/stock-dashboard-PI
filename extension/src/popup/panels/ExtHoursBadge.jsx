// Copied from frontend/src/components/ExtHoursBadge.jsx, with the CSS Module
// swapped for the popup's plain classnames. Same contract, same semantics.

/**
 * Pre-market / after-hours badge for a live quote. Renders nothing during
 * regular hours or when closed. Shows the extended-hours change % when known.
 */
export default function ExtHoursBadge({ quote, showChange = true }) {
  const state = quote?.market_state;
  if (state !== "PRE" && state !== "POST") return null;
  const label = state === "PRE" ? "PRE" : "AH";
  const chg = quote.extended_change_pct;
  return (
    <span
      className="mono faint"
      style={{ fontSize: 10, fontWeight: 600 }}
      title={state === "PRE" ? "Pre-market" : "After hours"}
    >
      {label}
      {showChange && chg != null && (
        <em className={chg >= 0 ? "pos" : "neg"} style={{ fontStyle: "normal", marginLeft: 3 }}>
          {chg >= 0 ? "+" : ""}
          {chg.toFixed(2)}%
        </em>
      )}
    </span>
  );
}
