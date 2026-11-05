import { useState } from "react";
import Dialog from "./Dialog";
import Icon from "./Icon";
import { NAV_ITEMS, PRIMARY_KEYS, UTILITY_ITEMS } from "../lib/navItems";
import styles from "./MobileNav.module.css";

/**
 * Phone navigation (≤640px; hidden above by CSS): a bottom tab bar with the
 * five core views plus "More", a sheet listing every other view. Replaces the
 * desktop rail, which would otherwise take a sixth of a phone's width.
 *
 * Props: view (current key), onNavigate(key), isAdmin (shows Server in More),
 * extraItems (more {key,label,icon,hint} entries appended to the sheet).
 */
export default function MobileNav({ view, onNavigate, isAdmin = false, extraItems = [] }) {
  const [moreOpen, setMoreOpen] = useState(false);

  const primary = PRIMARY_KEYS.map((k) => NAV_ITEMS.find((n) => n.key === k));
  const more = [
    ...NAV_ITEMS.filter((n) => !PRIMARY_KEYS.includes(n.key)),
    ...extraItems,
    ...UTILITY_ITEMS.filter((u) => !u.admin || isAdmin),
  ];
  const inMore = more.some((m) => m.key === view);

  const go = (key) => {
    setMoreOpen(false);
    onNavigate(key);
  };

  return (
    <>
      <nav className={styles.bar} aria-label="Main">
        {primary.map((item) => (
          <button key={item.key} type="button" className={styles.tab}
                  data-active={view === item.key ? "yes" : "no"}
                  aria-current={view === item.key ? "page" : undefined} aria-label={item.label}
                  onClick={() => go(item.key)}>
            <Icon name={item.icon} size={19} />
            <span className={styles.label} aria-hidden="true">{item.short || item.label}</span>
          </button>
        ))}
        <button type="button" className={styles.tab} data-active={inMore || moreOpen ? "yes" : "no"}
                aria-haspopup="dialog" aria-expanded={moreOpen} onClick={() => setMoreOpen(true)}>
          <Icon name="layers" size={19} />
          <span className={styles.label}>More</span>
        </button>
      </nav>

      <Dialog open={moreOpen} title="All views" caption="Navigate" onClose={() => setMoreOpen(false)}>
        <ul className={styles.list}>
          {more.map((item) => (
            <li key={item.key}>
              <button type="button" className={styles.row} data-active={view === item.key ? "yes" : "no"}
                      aria-current={view === item.key ? "page" : undefined} onClick={() => go(item.key)}>
                <Icon name={item.icon} size={18} />
                <span className={styles.rowLabel}>{item.label}</span>
                <span className={styles.rowHint}>{item.hint}</span>
              </button>
            </li>
          ))}
        </ul>
      </Dialog>
    </>
  );
}
