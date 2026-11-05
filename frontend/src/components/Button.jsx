import { forwardRef } from "react";
import styles from "./Button.module.css";

/**
 * The shared action button. `variant`: primary (accent), ghost (outlined),
 * danger (destructive), quiet (text-only). `size`: sm | md. Anything else is
 * passed to the <button>; `type` defaults to "button" so it never submits a
 * form by accident.
 */
const Button = forwardRef(function Button(
  { variant = "ghost", size = "md", type = "button", className, children, ...rest },
  ref,
) {
  return (
    <button ref={ref} type={type} className={`${styles.btn} ${className || ""}`}
            data-variant={variant} data-size={size} {...rest}>
      {children}
    </button>
  );
});

export default Button;
