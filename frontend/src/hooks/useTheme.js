import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "theme";

// The full theme set. `swatch` colors are plain hex previews for the picker
// (CSS custom properties aren't resolvable in an inline swatch out of context).
export const THEMES = [
  { key: "dark", label: "Dark", hint: "Iris Dusk", swatch: ["#1b1a17", "#f0b429"] },
  { key: "light", label: "Light", hint: "Daylight", swatch: ["#f4f1ec", "#c1861f"] },
  { key: "retro", label: "Ultraviolet", hint: "Purple phosphor", swatch: ["#160f1a", "#c07af0"] },
  { key: "warm", label: "Warm", hint: "Amber paper", swatch: ["#f3ece0", "#c06a2e"] },
];

const VALID = new Set(THEMES.map((t) => t.key));

function initialTheme() {
  if (typeof localStorage !== "undefined") {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved && VALID.has(saved)) return saved;
  }
  return "dark"; // dark by default
}

/**
 * Theme state persisted to localStorage and applied to <html data-theme>.
 * Returns { theme, setTheme, toggle, themes } — `setTheme` validates against
 * the known set; `toggle` flips dark↔light (used by the command palette).
 */
export function useTheme() {
  const [theme, setThemeState] = useState(initialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // localStorage unavailable (private mode etc.) — non-fatal
    }
  }, [theme]);

  const setTheme = useCallback((t) => {
    if (VALID.has(t)) setThemeState(t);
  }, []);

  const toggle = useCallback(() => {
    setThemeState((t) => (t === "light" ? "dark" : "light"));
  }, []);

  return { theme, setTheme, toggle, themes: THEMES };
}
