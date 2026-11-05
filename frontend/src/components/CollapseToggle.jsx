import Tooltip from "./Tooltip";
import styles from "./CollapseToggle.module.css";

// Shared chevron button for collapsing/expanding an Overview section. Mirrors
// the ViewAll component pattern — a small, reusable header affordance.
// `controls` (optional) is the id of the region it shows/hides → aria-controls.
export default function CollapseToggle({ collapsed, onClick, label, controls }) {
  return (
    <Tooltip content={`${collapsed ? "Expand" : "Collapse"} ${label}`}>
    <button
      type="button"
      className={styles.toggle}
      onClick={onClick}
      aria-expanded={!collapsed}
      aria-controls={controls}
      aria-label={`${collapsed ? "Expand" : "Collapse"} ${label}`}
    >
      <svg
        className={styles.chevron}
        data-collapsed={collapsed ? "yes" : "no"}
        width="16" height="16" viewBox="0 0 24 24"
        fill="none" stroke="currentColor" strokeWidth="2.2"
        strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
      >
        <path d="M6 9l6 6 6-6" />
      </svg>
    </button>
    </Tooltip>
  );
}
