import { useCallback, useEffect, useState } from "react";
import { getAutomute, setMuteOverride } from "../api";

/** Each Boom component's recent hit rate, mute state and your override. */
export function useAutomute() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let live = true;
    getAutomute()
      .then((d) => { if (live) setData(d); })
      .catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, []);

  const override = useCallback(async (component, mode) => {
    setData(await setMuteOverride(component, mode));
  }, []);

  return { data, error, override };
}
