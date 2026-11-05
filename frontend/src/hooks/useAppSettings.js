import { useCallback, useEffect, useState } from "react";
import { getAppSettings, saveAppSettings } from "../api";

const DEFAULTS = {
  analysis_time: "15:30",
  analysis_tz: "Asia/Jerusalem",
  quotes_refresh_seconds: 30,
  x_accounts: [],
  next_analysis_run: null,
};

/**
 * Server-backed app settings: the daily analysis schedule (wall-clock time +
 * timezone; drives the backend cron job) and the live-quote refresh cadence.
 * Unlike useSettings (localStorage display prefs), these persist server-side
 * because the scheduler acts on them even when no browser is open.
 */
export function useAppSettings() {
  const [appSettings, setAppSettings] = useState(DEFAULTS);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    getAppSettings()
      .then((s) => { if (alive) setAppSettings({ ...DEFAULTS, ...s }); })
      .catch(() => {})
      .finally(() => alive && setLoaded(true));
    return () => { alive = false; };
  }, []);

  // Local edit (no network) — used while the user types.
  const update = useCallback((patch) => {
    setAppSettings((s) => ({ ...s, ...patch }));
  }, []);

  // Persist to the backend (also reschedules the analysis cron job there).
  const save = useCallback(async (next) => {
    const saved = await saveAppSettings(next);
    setAppSettings({ ...DEFAULTS, ...saved });
    return saved;
  }, []);

  return { appSettings, loaded, update, save };
}
