// Single source of truth for the manifest. `scripts/build.js` renders this into
// dist-chrome/manifest.json and dist-firefox/manifest.json.
//
// The only real divergence between the two browsers is the background entry:
// Chrome MV3 wants a service worker, Firefox MV3 wants an event page. Keeping the
// background code free of DOM/global state (see src/background/index.js) is what
// lets the same bundle satisfy both.

export const VERSION = "0.1.0";

// Sites where the ticker overlay runs. Users can disable any of these per-site in
// the options page; this list only decides where the content script is *injected*.
// "*://*.host/*" matches the bare domain AND its subdomains, so it covers both
// reddit.com and www.reddit.com. Using the bare "*://host/*" form would miss the
// www. variant, which is how most of these sites actually serve.
export const CONTENT_MATCHES = [
  // Scoped to the finance subdomain deliberately — "*.yahoo.com" would inject
  // into Yahoo Mail and News, which is not what anyone installed this for.
  "*://finance.yahoo.com/*",
  "*://*.reddit.com/*",
  "*://*.x.com/*",
  "*://*.twitter.com/*",
  "*://*.stocktwits.com/*",
  "*://*.cnbc.com/*",
  "*://*.marketwatch.com/*",
  "*://*.seekingalpha.com/*",
  "*://*.bloomberg.com/*",
];

function base() {
  return {
    manifest_version: 3,
    name: "Stock Signal Companion",
    version: VERSION,
    description:
      "Surfaces your own Stock Signal Dashboard's Boom Scores on tickers around the web, " +
      "with a watchlist popup and desktop alerts.",
    // storage       - backend URL, per-site toggles, caches
    // alarms        - the only legal scheduler in an MV3 worker
    // notifications - desktop alerts for high-severity events
    // activeTab     - read the active tab's host for the popup's per-site toggle.
    //                 Granted on popup click; "tabs" would be a scarier permission
    //                 for the same information.
    permissions: ["storage", "alarms", "notifications", "activeTab"],
    // The backend origin is user-configured (there is no fixed deployment), so it
    // is requested at runtime from the options page rather than baked in.
    host_permissions: ["http://localhost:8000/*"],
    optional_host_permissions: ["http://*/*", "https://*/*"],
    action: {
      default_popup: "popup.html",
      default_title: "Stock Signals",
      default_icon: {
        16: "icons/icon-16.png",
        32: "icons/icon-32.png",
        48: "icons/icon-48.png",
        128: "icons/icon-128.png",
      },
    },
    icons: {
      16: "icons/icon-16.png",
      32: "icons/icon-32.png",
      48: "icons/icon-48.png",
      128: "icons/icon-128.png",
    },
    options_ui: { page: "options.html", open_in_tab: true },
    content_scripts: [
      {
        matches: CONTENT_MATCHES,
        js: ["content.js"],
        run_at: "document_idle",
        all_frames: false,
      },
    ],
    content_security_policy: {
      extension_pages: "script-src 'self'; object-src 'self'",
    },
  };
}

export function manifestFor(browser) {
  const m = base();
  if (browser === "firefox") {
    // Firefox MV3 has no background.service_worker — it uses a non-persistent
    // event page. `browser_specific_settings` must be ABSENT from the Chrome
    // manifest (Chrome warns on the unknown key), which is the main reason this
    // file generates the manifest instead of shipping two static JSON files.
    m.background = { scripts: ["background.js"], type: "module" };
    m.browser_specific_settings = {
      gecko: { id: "stock-signals@stock-dashboard.local", strict_min_version: "128.0" },
    };
  } else {
    m.background = { service_worker: "background.js", type: "module" };
    m.minimum_chrome_version = "116";
  }
  return m;
}
