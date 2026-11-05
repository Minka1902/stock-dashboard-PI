import GLOSSARY from "../lib/glossary";
import Tooltip from "./Tooltip";
import styles from "./Term.module.css";

/**
 * An abbreviation or metric label that explains itself on hover/focus —
 * for table headers and inline labels where an "i" button would be clutter.
 *
 *   <th><Term term="rsi">RSI14</Term></th>
 *   <th><Term tip="Market capitalisation: share price × shares outstanding">Mkt Cap</Term></th>
 *
 * `term` pulls the definition from lib/glossary.js (label + short); `tip`
 * gives a one-off explanation; with both, `tip` is added under the glossary
 * text. Rendered as a focusable <abbr> with a dotted underline so keyboard
 * users can reach the explanation too, and screen readers get it through
 * aria-describedby. The visible label always stays on screen — the tooltip
 * only expands it.
 */
export default function Term({ term, tip, side = "top", children }) {
  const entry = term ? GLOSSARY[term] : null;
  if (!entry && !tip) return children;
  const content = entry ? (
    <>
      <strong>{entry.label}</strong>
      <p>{entry.short}</p>
      {tip && <p>{tip}</p>}
    </>
  ) : tip;
  return (
    <Tooltip content={content} side={side}>
      <abbr className={styles.term} tabIndex={0}>{children}</abbr>
    </Tooltip>
  );
}
