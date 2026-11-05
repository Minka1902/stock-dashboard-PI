import { createContext, useContext } from "react";

/**
 * Display preferences shared app-wide, so no component needs `settings`
 * threaded down through every panel. Provided by <SettingsProvider>.
 */
export const SettingsContext = createContext(null);

export function useSettingsContext() {
  const ctx = useContext(SettingsContext);
  if (!ctx) throw new Error("useSettingsContext must be used inside <SettingsProvider>");
  return ctx;
}

/**
 * `label(ticker)` renders the symbol or the company name per the user's
 * setting, always falling back to the symbol. Prefer the <TickerLabel>
 * component, which also sets a title so the symbol stays discoverable.
 */
export function useTickerLabel() {
  return useSettingsContext().labelFor;
}
