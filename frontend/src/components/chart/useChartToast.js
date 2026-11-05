import { useCallback, useEffect, useRef, useState } from "react";

export const TOAST_MS = 2800;

/** Toast state for the chart: `show(text, tone)` replaces any current toast. */
export function useChartToast() {
  const [toast, setToast] = useState(null);
  const timer = useRef(null);
  const show = useCallback((text, tone = "ok") => {
    clearTimeout(timer.current);
    setToast({ id: Date.now(), text, tone });
    timer.current = setTimeout(() => setToast(null), TOAST_MS);
  }, []);
  const dismiss = useCallback(() => setToast(null), []);
  useEffect(() => () => clearTimeout(timer.current), []);
  return { toast, show, dismiss };
}
