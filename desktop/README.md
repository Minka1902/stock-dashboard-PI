# Desktop app

An Electron shell around the dashboard: a real window, a tray icon with the
unread-alert count, and native Windows toasts for high-severity alerts.

It is a **companion to the Windows service, not a replacement** — it never
starts the backend itself. Install the service first (`windows/README.md`);
data collection, digests and alert generation all happen there whether this app
is running or not. What the app adds is the window and the notifications.

## Run

```powershell
.\windows\install-desktop.ps1     # npm install + Start Menu shortcut
cd desktop; npm start
```

```bash
npm test     # node --test, no Electron needed
npm run lint
```

## How it works

**The window loads `http://127.0.0.1:8000` — the backend's own origin, never
`file://`.** This is the load-bearing decision of the whole app, and three
things force it:

- Routing is History API paths with no hash (`frontend/src/lib/nav.js`), so
  `pushState("/stock/NVDA")` throws a `SecurityError` under `file://`.
- Vite sets no `base`, so assets emit as absolute `/assets/...`, which under
  `file://` resolve to the filesystem root.
- Auth is one httpOnly `SameSite=Lax` cookie on the backend origin — there is no
  token in JS to relay.

Same-origin makes all three non-issues and needs **zero frontend changes**.

`127.0.0.1` rather than `localhost` because uvicorn binds IPv4 only and Windows
resolves `localhost` to `::1` first — and because they are separate cookie jars,
so mixing them loses the session mid-OAuth. The service installer sets
`STOCKS_CORS_ORIGINS` and `STOCKS_OAUTH_REDIRECT_BASE` to match.

### Startup

`renderer/waiting.html` shows immediately (static, `file://`, makes no API
calls), while the main process polls `/api/health` for up to 60s. That endpoint
is public and returns 200 even when degraded — by design, so a degraded
subsystem doesn't read as "failed to start" — so the gate is the status **code**
only. On success the window swaps to the dashboard.

If the backend goes away mid-session, `did-fail-load` drops back to the waiting
page and re-enters the health loop. Transient fetch failures while the document
stays loaded are already handled by the app's own "Backend unreachable" banner.

### Navigation

| Case | Behaviour |
|---|---|
| `window.open` to the app origin | Real child window (`openTickerTab()`, the PDF report which needs `window.print()`) |
| `window.open` elsewhere | The user's actual browser — news headlines, SEC and USAspending links |
| Navigation to `/api/auth/oauth/*/start` | A popup window, so the redirect chain returns to the app origin and sets the cookie in **this** jar |
| Same-origin navigation | Allowed. The report link is `Content-Disposition: attachment`, so it becomes a save dialog, which is correct |

### Sessions

Electron's default session is disk-backed, and the session cookie is persistent
(`max_age = SESSION_TTL_SECONDS`, 14 days), so quitting and relaunching does
**not** re-prompt for TOTP. This depends on not setting a `partition` on
`webPreferences` and never calling `session.clearStorageData()` — including for
the OAuth popup, which deliberately shares the default session.

You will still re-enter TOTP roughly fortnightly. That's the app's design, not
an Electron artifact.

### Notifications

`src/alerts/poller.js` polls `/api/alerts` every 3 minutes. Ported from
`extension/src/background/index.js`, which is a working implementation of the
same feature: the `seeded` first-run gate (so installing doesn't fire a toast
for all 100 backlog rows), dedup on `dedup_key`, `severity === "high" && !read`,
and a 3-per-poll cap with a "+N more" summary.

The poll uses Electron's `net.fetch` bound to `session.defaultSession`, not
Node's global `fetch` — the latter does not share the cookie jar and would get
401 forever against an authenticated window. `credentials: "include"` is also
explicit, since `net.fetch` defaults to `omit`.

### Duplicated code

There is no root package manager (three independent apps), so these are copies
that must be kept in sync — the same convention the extension already follows:

| Here | Source |
|---|---|
| `src/alerts/seen.js`, `tests/seen.test.js` | `extension/src/background/seen.js` (verbatim) |
| `"app:navigate"` in `src/preload.cjs` | `NAV_EVENT` in `frontend/src/lib/nav.js` |
| Tray menu items | `commandItems` in `frontend/src/App.jsx` |
| Poll cadence, `MAX_NOTIFICATIONS_PER_POLL` | `extension/src/background/index.js` |
| `STALE_RUN_MS`, `isApplyRunning` in `src/updates/pending.js` | `STALE_RUN_SECONDS`, `is_apply_running` in `backend/app/updater.py` |

The tray menu deliberately omits "Refresh all sources" (there is only
`POST /api/refresh/{source}`, no bulk route, so it would mean duplicating
`EXTERNAL_SOURCES` from `useDashboardData.js` here) and the theme/dyslexia
toggles (they live in `SettingsContext` with no main-process representation).
All three are one Ctrl+K away in the app.

### Updates on launch

Once per launch, `src/updates/launch.js` calls `GET /api/update/status?refresh=true`
and, if `main` has moved, offers **Update now / Later** in a native dialog.
Non-admins, and installs the backend can't update (not on `main`, tracked
changes), get an info box with the reason instead. `src/updates/pending.js` is
that decision, kept pure and unit-tested.

"Update now" is the same `POST /api/update/apply` as Info → Updates, so the
service does the pull, pip, build, restart and rollback as LocalSystem, with no
UAC prompt. The window then goes to `/info`, whose `UpdatesSection` follows
the running update on its own. When `/api/health` reports a new `commit`, the
app relaunches itself, because the pull also changed `desktop/`, which this
process loaded at startup.

The check needs a signed-in session. A 401 leaves it pending until the
session cookie changes (tokens rotate on sign-in, and the SPA's login changes
no URL) or the next alert poll.

### Service control

The tray can *read* service state unelevated (`sc query` is permitted for
Authenticated Users) but cannot start or stop it — that needs admin rights.
"Restart backend service…" spawns an elevated PowerShell with a visible UAC
prompt. The outcome isn't observable from here and the user may cancel, so the
app re-enters the health loop rather than assuming success.

Loosening the service's security descriptor so an unelevated app could control
it would be a privilege-escalation hole in a LocalSystem service. It isn't done.

## Known rough edges

- **Toasts need the Start Menu shortcut.** Windows resolves toast identity
  through the AppUserModelID, which normally comes from a shortcut created by an
  installer. `install-desktop.ps1` creates one and stamps the AUMID via the
  shell property store. If notifications never appear, this is the first thing
  to suspect — the script warns when the stamp fails.
- **Google OAuth in an embedded browser.** Google returns
  `disallowed_useragent` for some embedded browsers; the popup overrides its
  user agent to a plain Chrome string, which is the standard mitigation but is a
  heuristic on Google's side. Email + password + TOTP always works regardless.
- **Double notifications.** If you also run the browser extension against the
  same backend, high-severity alerts toast twice — the two keep independent seen
  stores by design. Turn off `notifyHighSeverity` in whichever you use less.
- **Desktop npm deps are not updated in place.** `update.ps1` cannot reinstall
  `desktop\node_modules` while `electron.exe` is running (Windows locks it), so an
  Electron bump arrives on the next `SignalSetup.exe` run (repair), not
  through "Update now".
