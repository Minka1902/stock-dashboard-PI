import { useEffect, useRef, useState } from "react";
import { onStatus, subscribe } from "../lib/eventStream";

/** Run `handler(data)` on every server-sent `kind` event while mounted. */
export function useEventStream(kind, handler) {
  const ref = useRef(handler);

  useEffect(() => {
    ref.current = handler;
  });

  useEffect(() => subscribe(kind, (data) => ref.current(data)), [kind]);
}

/** Whether the shared event stream is currently connected. */
export function useStreamConnected() {
  const [connected, setConnected] = useState(false);

  useEffect(() => onStatus(setConnected), []);

  return connected;
}
