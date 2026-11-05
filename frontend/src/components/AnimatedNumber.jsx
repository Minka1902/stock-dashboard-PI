import { useEffect, useRef } from "react";
import { countUp } from "../lib/motionConfig";

/**
 * A number that tweens from its previous value to `value` (via motionConfig's
 * countUp, so it snaps instantly under reduced motion). `format` wins over
 * `decimals` when provided.
 */
export default function AnimatedNumber({ value, format, decimals = 0, duration = 900, className }) {
  const ref = useRef(null);
  const prev = useRef(0);

  useEffect(() => {
    const to = Number.isFinite(value) ? value : 0;
    countUp(ref.current, to, { from: prev.current, decimals, format, duration });
    prev.current = to;
  }, [value, decimals, format, duration]);

  // Initial paint before the effect runs.
  const initial = format ? format(0) : (0).toFixed(decimals);
  return <span ref={ref} className={className}>{initial}</span>;
}
