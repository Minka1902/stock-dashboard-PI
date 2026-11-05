import { useCallback, useEffect, useId, useRef, useState } from "react";
import Popover from "./Popover";
import Tooltip from "./Tooltip";
import styles from "./MenuButton.module.css";

/**
 * A "⋯" overflow menu, shared by anything that needs row- or tab-level actions.
 * Follows the same contract as UserMenu (aria-haspopup + role="menu", outside
 * mousedown and Escape to close, motion entrance gated on reduced motion) and
 * adds arrow-key roving focus, since these menus can get long.
 *
 * Children are a render prop receiving `close`, so callers compose their own
 * items with the exported MenuItem / MenuLabel / MenuDivider.
 */
export default function MenuButton({
  label = "More actions",
  align = "end",
  glyph = "⋯",
  className = "",
  children,
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef(null);
  const menuRef = useRef(null);
  const menuId = useId();

  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      // The menu is portaled to <body>, so it is NOT inside wrapRef — check it
      // separately or every click on a menu item would close the menu first.
      if (menuRef.current?.contains(e.target)) return;
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    };
    const onKey = (e) => {
      if (e.key === "Escape") {
        setOpen(false);
        // Return focus to the trigger so keyboard users aren't stranded.
        wrapRef.current?.querySelector("button")?.focus();
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  /**
   * Focus the first item as soon as the menu exists, so the menu is
   * immediately keyboard-operable.
   *
   * A callback ref rather than an effect keyed on `open`: Popover measures its
   * anchor in a layout effect, so on the render where `open` first flips true
   * the menu is not mounted yet and such an effect would find nothing. This
   * fires exactly when the node attaches.
   */
  const attachMenu = useCallback((node) => {
    menuRef.current = node;
    // In a picker, start on the current choice (as a native <select> does).
    (node?.querySelector('[role="menuitem"][data-tone="active"]:not([disabled])')
      || node?.querySelector('[role="menuitem"]:not([disabled])'))?.focus();
  }, []);

  const onMenuKeyDown = (e) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const items = Array.from(
      menuRef.current?.querySelectorAll('[role="menuitem"]:not([disabled])') || [],
    );
    if (!items.length) return;
    const i = items.indexOf(document.activeElement);
    const next = e.key === "ArrowDown"
      ? (i + 1) % items.length
      : (i - 1 + items.length) % items.length;
    items[next].focus();
  };

  return (
    <div className={`${styles.wrap} ${className}`} ref={wrapRef}>
      <Tooltip content={label} disabled={open}>
        <button
          type="button"
          className={styles.trigger}
          onClick={() => setOpen((o) => !o)}
          aria-haspopup="menu"
          aria-expanded={open}
          aria-controls={open ? menuId : undefined}
          aria-label={label}
        >
          {glyph}
        </button>
      </Tooltip>

      <Popover
        open={open}
        anchorRef={wrapRef}
        align={align}
        className={styles.menu}
        id={menuId}
        contentRef={attachMenu}
        data-align={align}
        role="menu"
        onKeyDown={onMenuKeyDown}
      >
        {typeof children === "function" ? children(close) : children}
      </Popover>
    </div>
  );
}

export function MenuItem({ onSelect, tone, disabled, children }) {
  return (
    <button
      type="button"
      role="menuitem"
      className={styles.item}
      data-tone={tone}
      disabled={disabled}
      onClick={onSelect}
    >
      {children}
    </button>
  );
}

export function MenuLabel({ children }) {
  return <div className={styles.groupLabel}>{children}</div>;
}

export function MenuDivider() {
  return <div className={styles.divider} />;
}
