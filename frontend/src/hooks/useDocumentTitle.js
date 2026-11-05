import { useEffect } from "react";

const BRAND = "Signal";

/**
 * Keeps the browser tab title in sync with what's on screen.
 *
 * Analysis opens in its own tab, so without this every one of them reads the
 * same static title and they're impossible to tell apart.
 */
export function useDocumentTitle(title) {
  useEffect(() => {
    document.title = title ? `${title} · ${BRAND}` : BRAND;
  }, [title]);
}
