# Stock Signal Companion — browser extension

A Chrome/Firefox companion for the Stock Signal Dashboard in this repo. It is a
**companion, not a second dashboard** — it reuses the existing REST API and
session cookie, and adds the two things the web app structurally can't do:

1. **Ticker badges in the wild.** A content script annotates ticker mentions on
   Yahoo Finance, Reddit, X, StockTwits, CNBC, MarketWatch, Seeking Alpha and
   Bloomberg with *your* Boom Score, with a hover card explaining the signals.
2. **Desktop alerts.** A background poller raises native notifications for
   high-severity alerts. The backend has no push channel — `app/alerts.py` only
   fans high-severity events out over email/SMS — so before this, an alert
   required an open, polling browser tab.

Same product principle as the dashboard: **signals, not predictions.** A ticker
the backend has no score for gets a muted badge and an "Add to watchlist" action,
never an invented number.

## Commands

Run everything from `extension/` (this is a third independent app; there is still
no root package manager).

```bash
npm install
npm run build          # -> dist-chrome/ and dist-firefox/
npm run build:chrome   # one target
npm run test           # node --test, no browser needed
npm run lint
```

## Loading it

The backend must be running and you must be signed in to the dashboard in the
same browser profile.

- **Chrome** — `chrome://extensions` → enable Developer mode → *Load unpacked* →
  pick `extension/dist-chrome`.
- **Firefox** — `about:debugging#/runtime/this-firefox` → *Load Temporary Add-on*
  → pick `extension/dist-firefox/manifest.json`. Temporary add-ons are removed on
  restart and get a fresh UUID each time; that's expected.

Then open the extension's **Settings**, set your dashboard address (default
`http://localhost:8000`), click **Grant access**, and **Test connection**.

## How auth works

The extension sends the dashboard's existing httpOnly `SameSite=Lax` session
cookie. That works because **all network calls happen in the background worker**,
never in the content script: an extension-initiated request to an origin in
`host_permissions` is treated as first-party by both Chrome and Firefox, and is
CORS-exempt. A `fetch` from a content script would carry the *visited page's*
origin and be blocked.

There is no token system and no new backend endpoint.

### Backend configuration

Strictly speaking nothing is required, because the background worker's requests
bypass CORS. If you want belt-and-braces coverage for Chrome, add the extension's
ID to the allowlist:

```
STOCKS_CORS_ORIGINS=http://localhost:5173,chrome-extension://<your-extension-id>
```

`backend/app/config.py` splits that on commas and only rejects a literal `*`, so
the value passes through unchanged — **no backend code change is needed.**

Firefox is different: its `moz-extension://<uuid>` origin is regenerated per
install per profile, so allowlisting it isn't practical. Rely on the host-permission
exemption there. If Firefox ever does hit CORS, the fix would be a
`STOCKS_CORS_ORIGIN_REGEX` config knob — deliberately not written until observed,
because it weakens a config that is strict on purpose
(see the comment at `backend/app/config.py:12-14`).

## Design notes

**False positives are the main UX risk.** `ALL`, `IT`, `ON`, `NEW`, `DD` and `AI`
are all real tickers, and annotating them turns prose into confetti. Two controls:
a bare uppercase word is only annotated if it appears in the backend's own symbol
list (`GET /api/company-names`, cached 24h) **and** is not in the `STOPWORDS` list
in `src/content/detect.js`. Cashtags (`$TSLA`) bypass both, since they're
unambiguous. If the symbol list is unavailable — signed out, cold cache — the
overlay degrades to cashtag-only rather than disappearing.

**Badges are additive.** We insert an `<sd-badge>` after the matched text rather
than wrapping or rewriting it, so the page keeps its own text nodes and styling
and teardown is a clean `remove()`. Badges and the hover card live in shadow roots
so page CSS can't reach them.

**Rate limits are shared with your open dashboard tabs.** The limiter in
`backend/app/security.py` is per-user, so the extension batches deliberately: one
`GET /api/boom-scores` per page (cached 120s) covers every ticker on it, and
`GET /api/analyze` — limited to 10/60s — is never called on hover or scan, only on
an explicit click.

**MV3 lifecycle.** The background worker is torn down whenever it goes idle, so
`src/background/index.js` keeps no module-level state, registers every listener
synchronously, schedules only via `chrome.alarms`, and touches no DOM API. That's
also what lets one bundle serve Chrome's service worker and Firefox's event page.

**Alerts are global rows.** `GET /api/alerts` returns the 100 newest with no
cursor, and only `read` is per-user, so the client tracks which `dedup_key`s it
has seen in a capped FIFO (`src/background/seen.js`). The first poll after install
seeds that set **without notifying** — otherwise installing would fire a
notification for the entire existing backlog.

## Code duplicated from `frontend/`

Copied rather than imported, because the extension is a separate app and pulling
`../frontend/src` into its module graph would drag in `import.meta.env` and CSS
Modules resolution. **Keep these in sync:**

| Extension file | Source |
|---|---|
| `src/lib/boom.js` | `CHIP_META` / `HORIZON_TIP` / `convictionTier` from `frontend/src/components/BoomScorePanel.jsx:15-48` (module-local there, not exported) |
| `src/styles/tokens.css` | `frontend/src/index.css:40-205`, minus the `@font-face` rules (absolute `/fonts/*.woff2` paths 404 inside an extension) and minus the retro/warm themes |
| `src/lib/format.js` | subset of `frontend/src/lib/format.js` |
| `src/popup/panels/ExtHoursBadge.jsx` | `frontend/src/components/ExtHoursBadge.jsx` |

`tests/boom.test.js` pins the 76/51/26/0 conviction thresholds so a drift in the
tiers fails a test rather than silently disagreeing with the dashboard.

## Not done yet

- **Safari.** One MV3 source would mostly port via
  `xcrun safari-web-extension-converter`, but it needs macOS, Xcode, a paid Apple
  Developer account to distribute, and Safari does not honour the same cookie
  carve-out — it would likely force a token-auth path.
- **The cookie carve-out is unverified in a real browser.** It cannot be tested in
  this headless environment; it is the one load-bearing assumption. See
  "Verify first" below.
- Store packaging (`web-ext lint`, zip, signing).

## Verify first

Before trusting anything else, confirm the cookie actually rides along: load the
extension, sign in to the dashboard, open Settings → **Test connection**. Green
means the whole design holds. If it reports signed-out while the dashboard tab is
clearly logged in, the carve-out failed and the fallback is an extension API token
(a revocable token endpoint plus a `Bearer` header). All auth is confined to
`src/lib/api.js` to keep that a one-file change.
