import { useCallback, useEffect, useMemo, useState } from "react";
import { getCompanyNames } from "../api";
import { useSettings } from "../hooks/useSettings";
import { SettingsContext } from "../hooks/useSettingsContext";

/**
 * One place for display preferences, so any component can read them without
 * having `settings` threaded down through every panel.
 *
 * Also owns the ticker -> company-name map behind the "show company names"
 * setting. The map is loaded once, only when the preference is on; tickers
 * with no stored name are absent and `labelFor` falls back to the symbol, so
 * a label is never blank or invented.
 */
export default function SettingsProvider({ children }) {
  const { settings, setSetting } = useSettings();
  const [companyNames, setCompanyNames] = useState({});

  const wantsNames = settings.nameDisplay === "company";

  useEffect(() => {
    if (!wantsNames || Object.keys(companyNames).length > 0) return undefined;
    let alive = true;
    getCompanyNames()
      .then((m) => { if (alive) setCompanyNames(m || {}); })
      .catch(() => {}); // no names available → labels stay as tickers
    return () => { alive = false; };
  }, [wantsNames, companyNames]);

  const labelFor = useCallback((ticker) => {
    if (!ticker) return "";
    if (!wantsNames) return ticker;
    return companyNames[ticker.toUpperCase()] || ticker;
  }, [wantsNames, companyNames]);

  const value = useMemo(
    () => ({ settings, setSetting, companyNames, labelFor, wantsNames }),
    [settings, setSetting, companyNames, labelFor, wantsNames],
  );

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}
