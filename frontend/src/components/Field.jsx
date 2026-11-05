import { useId } from "react";
import styles from "./Field.module.css";

/**
 * A labelled form control with an optional hint and a field-level error (the
 * `ApiError.fields` messages the backend returns). `children` is a render
 * function receiving the input's id and aria props, so any control fits:
 *   <Field label="Stop" error={errors.stop_price}>{(p) => <input {...p} />}</Field>
 */
export default function Field({ label, hint, error, suffix, inline = false, className, children }) {
  const id = useId();
  const describedBy = [hint && `${id}-hint`, error && `${id}-err`].filter(Boolean).join(" ") || undefined;
  return (
    <div className={`${styles.field} ${className || ""}`} data-inline={inline ? "yes" : "no"}
         data-invalid={error ? "yes" : "no"}>
      <label className={styles.label} htmlFor={id}>{label}</label>
      <span className={styles.control}>
        {children({ id, "aria-invalid": error ? true : undefined, "aria-describedby": describedBy,
          className: styles.input })}
        {suffix && <span className={styles.suffix}>{suffix}</span>}
      </span>
      {hint && !error && <span id={`${id}-hint`} className={styles.hint}>{hint}</span>}
      {error && <span id={`${id}-err`} className={styles.error} role="alert">{error}</span>}
    </div>
  );
}
