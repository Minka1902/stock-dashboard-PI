import GLOSSARY from "../lib/glossary";
import Tooltip from "./Tooltip";
import styles from "./InfoTip.module.css";

/**
 * Small "i" affordance that reveals a plain-language glossary definition.
 * Built on Tooltip, so it behaves like every other tooltip (hover delay,
 * instant on keyboard focus, Esc, long-press on touch) — plus a click/tap
 * pins it open, since this is an explicit "what does this mean?" control.
 *
 * Renders nothing for an unknown term, so a missing glossary entry can never
 * produce an empty bubble.
 */
export default function InfoTip({ term, size = 16, side = "top" }) {
  const entry = GLOSSARY[term];
  if (!entry) return null;
  return (
    <Tooltip
      side={side}
      openOnClick
      content={(
        <>
          <strong>{entry.label}</strong>
          <p>{entry.short}</p>
        </>
      )}
    >
      <button
        type="button"
        className={styles.btn}
        aria-label={`What is ${entry.label}?`}
        style={{ width: size, height: size }}
      >
        i
      </button>
    </Tooltip>
  );
}
