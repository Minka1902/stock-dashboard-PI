import styles from "./Skeleton.module.css";

// A shimmering placeholder block. `w`/`h` accept any CSS size string.
export default function Skeleton({ w = "100%", h = "14px", radius }) {
  return (
    <span
      className={styles.skeleton}
      style={{ width: w, height: h, borderRadius: radius }}
    />
  );
}
