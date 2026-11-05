# Wave 2 report

Branch: `feat/wave-2` (not yet merged to `main`). The QA log, with all 41 findings and 4 rounds, is in
[`wave-2-qa-log.md`](wave-2-qa-log.md).

**Final verification (2026-09-27):**
- Backend `pytest`: **823 passed**
- Frontend `npm run lint`: clean
- Frontend `npm run build`: OK
- Extension tests: 48/48
- Desktop tests: 12/12
- Last full Playwright pass: 0 console errors, 0 failed requests, 0 axe violations, no page-level sideways scroll

**How it was built:**
- Every workstream was implemented by an Opus agent.
- Library APIs (lightweight-charts v5, APScheduler, motion, animejs v4, recharts, FastAPI, Playwright, exchange_calendars) were checked against context7 docs before use.
- The bigger new UI pieces animate with `motion` or `animejs`, always behind the app's reduced-motion setting.

---

## 1. Currencies: hold ILS and USD at the same time

**Added**
- `backend/app/currency.py` provides three helpers: `quote_currency`, `normalize_price` and `fx_rate` (tries the direct pair, then the inverse).
- Yahoo quotes Tel Aviv prices in **agorot (ILA)**. They are divided by 100 into shekels at every point where a Yahoo price comes in: quotes, chart bars, the extended-hours price, stored daily/weekly bars, technicals, seasonality, and the live analysis.
- `portfolio.currency`: each holding keeps its native currency. It is detected from Yahoo when you add the holding, and you can edit it. Adding the same ticker in a different currency is refused.
- `notify_profile.base_currency` (USD or ILS). `GET /api/fx/rates` returns the rates in use.
- **Portfolio page**
  - Every row shows its own currency badge and values in that currency.
  - The summary cards, group rows and total are converted into your base currency.
  - Per-currency subtotals ("USD x · ILS y") and the rate used are shown.
  - An animated USD/ILS switcher saves your choice to your profile.
  - The add/edit form has a currency picker.
- **Position sizing** converts your account size into the stock's currency, for example "Account 100,000 ILS ≈ 32,820 USD".
- **Settings** shows "Account size (₪ ILS)". Switching the base currency converts the account size at the live rate.

**Changed**
- The stock page, chart legend, Pre/After price lines, snapshot header, technicals and market cap now use the stock's currency. No `$` is left on a TASE page.
- Officer pay is shown in the company's reporting currency, with the code spelled out when it differs from the share price's currency.
- `lib/format.js` has currency-aware formatters. Data that really is US-only (contracts, insider trades, congress, margin debt) keeps `$`.

**Removed**
- The hardcoded USD-only formatting on those pages.
- A one-time migration deletes `.TA` price rows stored before the agorot fix, so no series mixes units, and marks old `.TA` holdings as ILS.

**Honesty:** a missing exchange rate is never guessed. The UI shows "FX unavailable" and leaves that holding out of the converted total, with a visible note.

**Verified live:**
- USD/ILS was 3.0472.
- AAPL 10 @ $150 showed $341.46.
- TEVA.TA 100 @ ₪55 showed ₪120.50 (Yahoo sent 12,050 agorot).
- The base-USD and base-ILS totals matched a manual calculation.

**Limits**
- For old `.TA` holdings entered before this change, the average cost isn't rescaled, because it can't be known whether you typed it in agorot or shekels. Please check it.

## 2. Israeli stock market (TASE)

**Added**
- Search takes a market filter (`all`, `us` or `tase`). The Cmd/Ctrl+K palette has All / US / TASE chips and "TASE · ILS" / "US · USD" badges.
- TA-35 (`TA35.TA`) is in the ticker carousel, and Israel is available in the economic-calendar countries.
- **TASE trading calendar** in `market_calendar.py`:
  - Monday–Friday since 2026-01-05.
  - Mon–Thu 10:00–17:35 and Fri 10:00–13:50.
  - Holidays come from XTAE, and the times were checked against live 5-minute bars.
  - It's a hand-maintained table because the `exchange_calendars` XTAE calendar still uses the pre-2026 hours.
- Separate US and TASE session chips. TASE is only ever LIVE or CLOSED, since it has no pre- or after-hours session.
- US-only sources (SEC Form 4, congress trades, federal contracts, short interest) say "not applicable to TASE listings" on TASE pages instead of an error or a blank.
- The Boom Score is rescaled over the components that apply, and a "TASE · renormalized" chip explains that.

**Limits**
- The 2027–2028 holiday dates are XTAE's projections; check them once TASE publishes its official calendar.
- The Boom Score rescaling is covered by unit tests, but no score was computed live during the test run.

## 3. Pre-market and after-hours on the analysis chart

**Added**
- **Intraday charts**
  - Each bar is tagged pre-market, regular or after-hours using Yahoo's own trading periods for that exchange; no hours are hardcoded.
  - Extended hours are on by default.
  - Pre- and after-hours bars get a shaded background and dimmer candles.
  - The crosshair legend shows PRE, REG or POST.
- **Daily, weekly and monthly charts**
  - A new endpoint, `GET /api/chart/{ticker}/extended`, returns the current pre-market or after-hours price from real 1-minute bars.
  - The chart draws a dashed "Pre $x" or "After $y" line with a legend chip, only when such a price actually exists.
- Markets with no extended session, such as TASE, show a "REG ONLY" label with a tooltip explaining why.

**Fixed along the way**
- The volume, RSI and MACD panes no longer jump in height when you toggle an indicator.
- After switching timeframes, the new bars fill the whole chart.

## 4. Watch currencies in the ticker carousel

**Added**
- A new per-user table, `fx_watch`. Each user starts with **USD/ILS and EUR/ILS**.
- `GET` / `PUT /api/fx-watch` validates the pair: the format, real ISO currency codes, two different currencies, and at most 12 pairs.
- Settings has an FX watch editor: add, remove, and reorder by dragging or with the ↑/↓ buttons. It saves automatically and the carousel updates straight away.

**Changed**
- `/api/quotes` uses your own pairs instead of one global list. The response shape is unchanged, so the extension and desktop app keep working.
- `STOCKS_FX_PAIRS` now only sets the defaults for new users.

## 5. Screenshot and share the analysis chart

**Added**
- A snapshot button captures the chart exactly as it is on screen, with drawings included and the crosshair left out, using lightweight-charts' `takeScreenshot`.
- The image gets a header strip: ticker, timeframe, last price and change, timestamp, active indicators, drawing count, the Pre/After price when there is one, and "Signals, not predictions".
- A menu with a preview offers three actions, each confirmed by a toast:
  - **Share…**, shown only when the browser can share files
  - **Copy image**
  - **Download PNG**

## 6. Scheduler on the Server page

**Added**
- A `source_schedules` table. Each source is scheduled either **every N minutes** or **at set times** (HH:MM on chosen days, in a chosen timezone). It can also be paused and have its own retry delay.
- Rows are seeded from each source's old cadence, and an admin's edits are never overwritten.
- **Margin debt** is seeded weekly, **Monday 06:00 Israel time**. Existing installs whose margin-debt row was never edited pick up the new default.
- A new Scheduler section on the Server page. One row per source has:
  - a mode toggle
  - interval or time chips, days, and timezone
  - on/paused
  - the next run time
  - a **Run now** button

  Changes save straight away. If the server rejects one, the change is undone and the server's reason is shown.
- New endpoints, all admin-only:
  - `GET /api/server/schedules`
  - `PUT /api/server/schedules/{source}`
  - `POST /api/server/schedules/{source}/run-now`
  - `GET /api/server/sources/{source}/runs`

**Changed**
- The single "refresh every 180s" job is replaced by **one job per source**. They all run on a single-thread executor, so the database still has one writer.
- Boom Score, then alerts, run together as one "derived" step. It has its own schedule row and is pulled forward about 30 seconds after any source updates.
- Fixed a shutdown deadlock: pending work is drained before the scheduler stops. Documented in CLAUDE.md.

## 7. "Show similar" in Recent activity

**Added**
- Every row in Recent activity has a **Show similar** button. It filters the list on the server (`/api/server/events?kind=&id=`) to that one task's history.
- The active filter shows as a chip with ×, and it is kept in the URL so it survives a reload.
- The table gained a header row.

## 8. Errors section removed; click a source to see its error

**Removed**
- The separate Errors section on the Server page.

**Added**
- Clicking an errored or deferred source, a status badge, or an outcome badge in the log opens a **slide-over panel** showing:
  - the status and key facts (last success, last attempt, schedule, next run)
  - the full traceback
  - the last 10 runs, each with its own error
  - **Copy error** and **Retry now** buttons

  Esc closes the panel and returns focus to the row you came from.

## 9. No task is skipped, and every failure says why

**Changed**
- The throttle that silently skipped runs is gone. `run_source` now has only three outcomes:
  - **ok**
  - **error**, with the full traceback stored on the run row
  - **deferred**, with the reason and the next attempt time; for example, GDELT's rate limit
- A job that is late **runs late** instead of being dropped. Queued work is shown under "Right now".
- A job that fires while its previous run is still going is recorded as **coalesced**, with an explanation.
- Every source is listed, including ones that have never run ("never run · next in …").
- Scheduler jobs record how long they took, and note it when they started more than 60 seconds late.
- Deferred runs are shown in amber everywhere; errors show their reason in the panel from task 8.
- The earnings retry setting, which existed but wasn't used, is now wired in.

**Limits**
- Runs recorded as `skipped` before this change stay in the log until the normal 30-day cleanup removes them.
- "Run now" still works on a paused schedule, as a deliberate manual override.

## 10. Tooltips

**Added**
- `Tooltip.jsx` follows accessible-tooltip practice:
  - **Mouse:** opens after a 400ms hover, and instantly when you move straight from one tooltip to the next.
  - **Keyboard:** opens immediately on keyboard focus.
  - **Staying open:** you can move the pointer onto the tooltip, and it stays until dismissed (WCAG 1.4.13).
  - **Esc:** closes only the tooltip, not the dialog behind it.
  - **Touch:** a long press opens it.
  - **Disabled buttons:** they still show a "why it's disabled" tooltip.
  - **Shortcuts:** keyboard shortcuts are shown as keys.
  - **Truncated text:** shows its full text only when it's actually cut off.
- `InfoTip` (glossary definitions, click to pin) is used on the Boom Score, Form 4, Congress, Short Interest, Analyst, WSB, Fear & Greed, Yield Curve and market-sentiment panels.
- `Term` marks up abbreviations. There are freshness tooltips on "Updated" cells and explanations on status badges.
- **25 new glossary terms**, including RSI/MACD/ATR, R-multiple, conviction, days to cover, put/call, margin debt, VIX, extended hours, P/E, TASE and base currency.

**Changed**
- Every native `title=` attribute used as UI chrome was replaced, so there are no double tooltips.
- Icon-only buttons have tooltips and accessible labels.
- Admin-only controls say "Only an admin can change this".

## 11. More drawing tools on the chart

**Added**
- A tool rail like other charting platforms, with grouped flyouts and **18 tools**:
  - **Lines:** trend line, ray, extended line, horizontal line, horizontal ray, vertical line, arrow, parallel channel
  - **Fibonacci:** retracement, trend-based extension
  - **Shapes:** rectangle, ellipse, freehand brush
  - **Annotation:** text, callout
  - **Measure:** Δprice, Δ%, bars, time
  - **Positions:** long and short (entry, target and stop, with R:R)
- A properties bar for the selected shape: colour (a theme colour), width, dash style, fill opacity, edit text, lock and delete.
- Magnet (snaps to the bar's open/high/low/close), lock all, hide all, and clear.
- **Undo/redo** with Ctrl+Z and Ctrl+Y or Ctrl+Shift+Z. Esc cancels and Del deletes.
- Alt+letter shortcuts for each tool, shown in the tooltips.
- Shapes can be dragged whole or by their handles.
- Text is edited inline, replacing the old `window.prompt`.
- Old saved drawings still load.
- On phones the rail sits above the chart.

**Limits**
- Undo history lasts for the session only.
- Drawings are still tied to the timeframe they were drawn on.

## 12. Collapsible sections on the analysis page

**Added**
- `CollapsibleSection` makes every section collapsible:
  - chart, company, insiders, alerts, suggestion history, this day in history, X Watch
  - the 7 analysis panes
- Sections animate smoothly, or instantly when reduced motion is on.
- Collapsed state is remembered across reloads and across tickers.
- **Collapse all / Expand all** in the top bar.
- A collapsed section's content stays in the page, so the chart isn't rebuilt and panes don't re-fetch.
- If focus was inside a section when it collapsed, it moves to that section's toggle.

## 13. Sector list and other colours not following the theme

**Changed**
- Every hardcoded dark colour is now a theme token across all four themes: the chart canvas, drawings, recharts panels, buttons, badges and overlays.
- `lib/themeColors.js` converts the themes' oklch colours for the chart canvas.
- Switching theme recolours the chart in place, without rebuilding it.
- The theme-blind native dropdowns in Settings (the likely "sector list") were replaced by a themed `SelectMenu`.
- During QA, text contrast was brought up to **WCAG AA in all four themes**, using a new `--accent-text` token. As a result, Light and Warm are slightly deeper in tone.

## 14. Weekly margin-debt automation

**Added**
- FINRA blocks plain requests to its API and statistics page (401/403 behind Cloudflare). A new last-resort step fetches the data through **headless Chromium** using Playwright:
  - It prefers FINRA's workbook, fetched through the same browser session, and falls back to the page's table.
  - It has a 60-second limit and always closes the browser.
- The schedule is weekly, **Monday 06:00 Israel time**, retrying 6 hours after a failure.
- If Playwright or the browser is missing, the source shows a clear error with the install command. It never skips silently.
- **Windows service:** `install-service.ps1` installs Chromium into `C:\ProgramData\SignalDashboard\ms-playwright` and sets `PLAYWRIGHT_BROWSERS_PATH`; documented in `windows/README.md`.

**Verified live:** 356 monthly rows, 1997-01 to 2026-08, fetched in about 11–15 seconds.

**Action needed:** re-run `install-service.ps1` so the service gets the browser.

**Limit:** if FINRA moves to an interactive challenge, this step will fail with a clear message rather than try to get around it.

## 15. Save drawing drafts with a title and description

**Added**
- A new per-user table, `drawing_drafts`, with owner-checked routes:
  - `GET` and `POST /api/drawings/{ticker}/drafts`
  - `PUT` and `DELETE /api/drawings/drafts/{id}`

  Validation: title 1–120 characters, description up to 2,000, at most 200 shapes and 100 drafts per ticker.
- A **Save draft** dialog (title and description, with character counters) and a **Drafts** menu:
  - **Load** asks whether to replace or merge, switches to the draft's timeframe, and can be undone.
  - **Edit** renames the draft and can overwrite its drawings with the current ones.
  - **Delete** asks for confirmation.
- Drafts are deleted along with their user's account (CLAUDE.md now says twelve per-user tables).

## 16. Update the app from inside the app

**Added**
- `app/updater.py` compares this checkout with `origin/main` on GitHub. It checks every 6 hours and caches the result for 1 hour. It reports:
  - new commits
  - the latest version
  - local changes
  - why an update is blocked, if it is
- **Info → Updates** shows:
  - your version and commit, and when it last checked
  - a **Check now** button
  - the list of new commits
  - an **Update now** button (admin only), with a confirmation and an animated progress stepper

  After an update it waits for the server to come back on the new commit, then reloads.
- A dot on the account menu and on "Info / Guide" when an update is available.
- `windows/update.ps1` runs the update: git pull, pip install, npm build, restart. It **rolls back** to the previous commit if any step fails.
- `/api/health` now includes `commit`.

**Tested:** an update and a rollback, both in throwaway clones.

**Limits**
- The Windows-service update path hasn't been run for real. Watch `C:\ProgramData\SignalDashboard\logs\update.log` on the first one.
- `git` and `npm` must be on the machine PATH.
- Update now only runs on a clean `main` checkout, so it will refuse, with that reason, until this branch is merged.

## 17. UI placement pass

A full Playwright sweep ran 4 rounds:
- **Pages:** every page plus `/stock/AAPL`, `/stock/NVDA` and `/stock/TEVA.TA`
- **Widths:** 375, 768, 1280 and 1920 px
- **Modes:** all 4 themes, dyslexia mode and reduced motion
- **Checks:** axe, console errors, network, overflow and focus

It found **41 issues; 39 were fixed** and 2 needed no change. All are listed in [`wave-2-qa-log.md`](wave-2-qa-log.md). The main placement fixes:
- **Portfolio**
  - The mobile header was cramped and the edit buttons were pushed off screen.
  - The page scrolled sideways on phones.
  - Totals briefly said "not in total" while exchange rates loaded.
- **Top bar and menus**
  - On phones the top-bar controls wrapped over the page title.
  - The alerts popover opened off the left edge.
  - The analysis page's top bar overflowed between 720 and 1000px.
- **Server page**
  - The scheduler's mode toggle was squeezed.
  - The Sources table was cut off at 1280px, and the page scrolled sideways at 375px.
  - Closing the error panel lost focus.
- **Status strip:** the "Not responding" chips made every page scroll sideways at 375px.
- **Watchlist and Boom Score:** rows went out of line with longer TASE symbols.
- **Tables:** the technical and short-interest tables scrolled sideways inside half-width panels.
- **Small visual fixes**
  - A double divider line in the command palette.
  - "WatchingMy Watchlist" running together; the list names are now chips.
  - The FX editor's squeezed drag handle is now an icon.
  - The unstyled Ticker labels control in Settings.
- **Wrong empty messages:** when an analysis failed to load, panes said "no analysis yet", "no filings" or "nothing tripped". The page now shows the real reason with a Retry button.

**Other issues fixed during QA**
- Seasonality was quietly running on monthly bars (Yahoo's `range=max` downsamples), so "this day N years ago" was off by up to a month. It now uses daily bars.
- A literal `&nbsp;` appeared in economic-calendar values; stored rows were repaired too.
- Suggestion-history markers never rendered.
- "Replay all tours" did nothing for accounts that had finished onboarding.
- A React console error from the tour, and chart-size warnings from the sparklines.
- `package-lock.json` was out of sync, so `npm ci` failed; it now works.

---

## Before merging / deploying
1. Merge `feat/wave-2` into `main`. In-app updates work only from a clean `main`.
2. Re-run `.\windows\install-service.ps1` so the service gets Chromium and `PLAYWRIGHT_BROWSERS_PATH`.
3. Check the average cost of any Tel Aviv holdings you entered before this change (agorot vs shekels).
4. Watch the first real in-app update through `update.log`.
