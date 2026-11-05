import MenuButton, { MenuItem } from "./MenuButton";
import styles from "./SelectMenu.module.css";
import Tooltip from "./Tooltip";

/**
 * A themed stand-in for a single-value native <select>.
 *
 * A native dropdown's option list is OS chrome: it ignores data-theme and
 * cannot be styled cross-browser, so it opens in system colours over every
 * palette. This is the same MenuButton the portfolio theme picker uses — real
 * DOM, so it inherits the tokens, and portaled, so containers can't clip it.
 * The current option is marked active and gets focus when the menu opens, and
 * arrow keys / Escape come from MenuButton.
 *
 * The trigger's accessible name is "<label>: <current value>", so screen
 * readers hear both the field and its setting.
 */
export default function SelectMenu({ label, value, options, onChange, className = "" }) {
  const current = options.find((o) => o.value === value);
  const currentLabel = current ? current.label : String(value ?? "");

  return (
    <MenuButton
      label={`${label}: ${currentLabel}`}
      align="start"
      className={`${styles.select} ${className}`}
      glyph={(
        <>
          <Tooltip truncate><span className={styles.value}>{currentLabel}</span></Tooltip>
          <svg className={styles.chevron} viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">
            <path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="currentColor" strokeWidth="1.6"
                  strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </>
      )}
    >
      {(close) => options.map((o) => (
        <MenuItem
          key={o.value}
          tone={o.value === value ? "active" : undefined}
          onSelect={() => { onChange(o.value); close(); }}
        >
          {o.label}
        </MenuItem>
      ))}
    </MenuButton>
  );
}
