import { useMemo, useSyncExternalStore } from "react";
import { getRouteSnapshot, parseRoute, subscribeToRoute } from "../lib/nav";

/**
 * The current route, kept in sync with the address bar.
 *
 * The store snapshot is the raw "pathname + search" string so it stays
 * Object.is-stable between renders; parsing happens in a useMemo keyed off it.
 * Returning the parsed object straight from getSnapshot would hand React a new
 * object every call and loop forever.
 */
export function useRoute() {
  const href = useSyncExternalStore(
    subscribeToRoute,
    getRouteSnapshot,
    () => "/", // server/prerender snapshot
  );
  return useMemo(() => {
    const [pathname, search] = href.split("?");
    return parseRoute(pathname, search ? `?${search}` : "");
  }, [href]);
}
