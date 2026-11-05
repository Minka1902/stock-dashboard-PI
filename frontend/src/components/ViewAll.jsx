import styles from "./ViewAll.module.css";

/** A right-aligned "View all →" link shown in a panel header in compact mode. */
export default function ViewAll({ onClick }) {
  return (
    <button type="button" className={styles.viewAll} onClick={onClick}>
      View all <span aria-hidden="true">→</span>
    </button>
  );
}
