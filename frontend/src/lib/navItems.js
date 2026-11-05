// The app's module navigation, shared by the desktop rail (Sidebar) and the
// phone tab bar (MobileNav). Modules in the confirmed flow:
// mood -> act -> you -> evidence -> radar.
export const NAV_ITEMS = [
  { key: "sentiment",   label: "Sentiment",   icon: "gauge",    hint: "the mood", short: "Mood" },
  { key: "suggestions", label: "Suggestions", icon: "spark",    hint: "what to do", short: "Ideas" },
  { key: "suggestion-history", label: "History", icon: "calendar", hint: "what happened next" },
  { key: "portfolio",   label: "Portfolio",   icon: "wallet",   hint: "your book" },
  { key: "trading-bot", label: "Trading Bot", icon: "bot",      hint: "demo strategies" },
  { key: "trades",      label: "Trades",      icon: "trending", hint: "insiders" },
  { key: "news",        label: "News",        icon: "news",     hint: "the tape" },
  { key: "watchlist",   label: "Watchlist",   icon: "star",     hint: "charts & radar" },
  { key: "econ-calendar", label: "Calendar",  icon: "calendar", hint: "macro events" },
  { key: "earnings",    label: "Earnings",    icon: "contract", hint: "who reports when" },
];

// The five things the user acts on (PRODUCT.md) — the phone tab bar's tabs.
// `short` is the tab label where the full one can't fit a sixth of a phone.
// Everything else sits behind its "More" sheet.
export const PRIMARY_KEYS = ["sentiment", "suggestions", "portfolio", "trades", "news"];

// App-level utilities, kept out of the module flow (account menu on desktop).
export const UTILITY_ITEMS = [
  { key: "x",        label: "X Watch",  icon: "x",        hint: "tracked accounts" },
  { key: "settings", label: "Settings", icon: "settings", hint: "config" },
  { key: "info",     label: "Info",     icon: "info",     hint: "modules, sources & glossary" },
  { key: "server",   label: "Server",   icon: "layers",   hint: "health & errors", admin: true },
];
