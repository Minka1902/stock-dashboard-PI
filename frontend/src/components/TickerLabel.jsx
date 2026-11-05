import { useSettingsContext } from "../hooks/useSettingsContext";
import Tooltip from "./Tooltip";
import styles from "./TickerLabel.module.css";

/**
 * Renders a ticker as either its symbol or the company name, per the user's
 * "Ticker labels" setting. Falls back to the symbol whenever no name is
 * stored. When a name is shown, a tooltip carries "SYMBOL — Name" so the
 * symbol stays discoverable (and the full name, if the clamp cut it off).
 *
 * Company names are much longer than symbols, so the name variant is width-
 * capped and ellipsised rather than being allowed to blow up table layouts.
 */
export default function TickerLabel({ ticker, className = "", as: Tag = "span" }) {
  const { labelFor, wantsNames } = useSettingsContext();
  if (!ticker) return null;
  const label = labelFor(ticker);
  const showingName = wantsNames && label !== ticker;
  const el = (
    <Tag
      className={`${styles.label} ${className}`.trim()}
      data-name={showingName ? "yes" : "no"}
    >
      {label}
    </Tag>
  );
  // Symbol mode: a tooltip would only repeat the visible text.
  return showingName ? <Tooltip content={`${ticker} — ${label}`}>{el}</Tooltip> : el;
}
