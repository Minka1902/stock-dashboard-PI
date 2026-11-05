# Wave 2 QA log (WS-I)

Final QA and fix loop for `feat/wave-2`. The checks ran against a throwaway backend on `:8040`, which used a consistent `conn.backup()` copy of the real database. They also used a Vite dev server on `:5195` and a fresh TOTP account, promoted to admin in the copy only. Real market data was live, and the scheduler kept fetching in the background during the checks.

**Coverage matrix:** every view in `lib/routes.js`, plus `/stock/AAPL`, `/stock/NVDA` and `/stock/TEVA.TA`.

| What | Settings |
|---|---|
| Widths, dark theme | 375, 768, 1280 and 1920 |
| Light, Ultraviolet and Warm themes | every view at 1280; the main views also at 375 |
| Dyslexia mode with reduced motion | the main views at 1280 |

**Checks on each page:**
- Console errors and warnings.
- Non-2xx responses.
- Horizontal overflow, both on the document and inside the inner scroll container.
- Clipped text.
- axe-core rules: `color-contrast`, `button-name`, `link-name`, `label` and `nested-interactive`.
- Keyboard focus visibility.
- Native `title=` leftovers.

On top of the matrix, a scripted end-to-end pass exercised every feature from tasks 1 to 16. Screenshots and scripts live in the session scratchpad and are not committed.

**Severity:** blocker = the feature can't be used · major = broken or misleading in normal use · minor = visible defect with a workaround · polish = improvement.

## Issues

| ID | Area | Severity | Description | Evidence | Status |
|---|---|---|---|---|---|
| QA-01 | Onboarding tour | minor | React "Cannot update a component while rendering a different component": `Tour.next` called `onClose` inside a `setIndex` updater. | console, `tour-1.png` | fixed ce06e64 |
| QA-02 | Portfolio | major | The whole page scrolls horizontally at 375/768. The table's visually-hidden "Actions" header is absolutely positioned, escapes the table's scroll wrapper and widens the document to 942px. | `r1/portfolio-375-dark.jpg` | fixed 3d2cd74 |
| QA-03 | Portfolio | major | Inline edit widens the holdings table past the panel at 1280, so the Save/Cancel buttons are off-screen and you have to scroll the table sideways to reach them. | `port-edit.png` | fixed 3d2cd74 |
| QA-04 | Portfolio | polish | While live FX rates load, the cards show ILS as "(not in total)" in warning orange, then it flips to converted. | `r1/portfolio-1280-dark.jpg` | fixed 3d2cd74 |
| QA-05 | Portfolio | polish | The "Added LUMI.TA in ILS (detected)" confirmation uses warning styling and stays on screen after that holding is removed. | `port-remove.png` | fixed 3d2cd74 |
| QA-06 | Currency display | minor | The ₪ sign renders from Consolas (the mono stack) as a boxy glyph that is hard to read at small sizes. | `port-ils.png` | fixed 69f7fea |
| QA-07 | Source status strip | minor | At ≤390px the "Not responding" chip keeps a fixed 220px meta width and overflows, so the main column scrolls sideways on every view. | `t_375` report | fixed 3d2cd74 |
| QA-08 | Analysis page | major | A failed `/api/analyze` call (429 rate limit, network, 5xx) renders as "No analysis yet… still loading — try again in a minute". The real error is hidden and there is no retry. | 429s in matrix | fixed 3d2cd74 + db78a58 |
| QA-09 | Theme contrast | minor | axe `color-contrast`: `--text-faint` small text is 3.8–3.9:1 on Light/Warm and 4.0:1 in dark dyslexia mode. The active sidebar label (accent on accent-weak) is 2.9–3.1:1 on Light/Warm. Caution and positive chips on tinted backgrounds are 3.1–4.3:1. | matrix axe | fixed 69f7fea + 6f6ac39 |
| QA-10 | Economic calendar | minor | Ultraviolet: out-of-range day numbers in the suggestion-history strip are 1.9:1. | matrix axe | fixed 69f7fea |
| QA-11 | Sparklines | polish | recharts warns "width(-1) and height(-1) of chart should be greater than 0" on first paint (Watchlist, Portfolio). | console | fixed ce06e64 |
| QA-12 | Suggestion history strip | minor | Suggestion markers never render. There is no `<XAxis>`, so the x scale is category indices while `ReferenceDot x` is a date. | code + `teva-2.png` | fixed ce06e64 |
| QA-13 | FX watch editor | minor | The drag handle is a squeezed "⋮⋮" text glyph (letter-spacing −2px). | `settings-2.png` | fixed 3d2cd74 |
| QA-14 | Collapsible sections | minor | Collapsing a section that contains focus (caption click, collapse-all) leaves focus on an inert element, and it drops to `<body>`. | WS-E note | fixed ce06e64 |
| QA-15 | Build | major | `frontend/package-lock.json` is out of sync with `package.json` (`@emnapi/wasi-threads`), so `npm ci` fails. | WS-E note | fixed fe4b6f4 |
| QA-16 | Company panel | minor | Officer pay is always formatted as $. Yahoo reports it in the company's `financialCurrency` (LUMI.TA: ILS; TEVA.TA: USD). | Yahoo probe | fixed 27e8b65 |
| QA-17 | Analysis chart | minor | Check that the ~45px "Chart" header doesn't leave the chart too short on short screens. | `chart-short-screen.png` | verified, no change needed (chart keeps 280px min, fits a 640px-tall window) |
| QA-18 | Command palette | polish | Double divider line between the market filter row and the results. | `palette-us.png` | fixed 3d2cd74 |
| QA-19 | Analysis header | minor | "Watching" runs straight into the list name ("WatchingMy Watchlist"). | `aapl-d.png` | fixed 3d2cd74 |
| QA-20 | Boom Score list | minor | A longer ticker (TEVA.TA) pushes its row's bar, score and chips out of line with the other rows. | `boom-page.png` | fixed 3d2cd74 |
| QA-21 | Scheduler (task 14) | major | Existing installs keep margin_debt at "every 14 days": unedited seeded rows never pick up the new weekly Mon 06:00 Asia/Jerusalem default. The real `stocks.db` row has `updated_at` NULL and interval 1209600. | `/api/server/schedules` | fixed 2751e0e |
| QA-22 | Watchlist | minor | Longer tickers push the price and change columns out of alignment. Prices show without a currency (TEVA.TA is ₪). | `watchlist-0.png` | fixed 3d2cd74 |
| QA-23 | Settings | minor | The "Ticker labels" choice is an unstyled pair of grey pills with no clear selected state. The toggle rows under Company information and Reading & focus have no spacing between items. | `settings-4.png` | fixed 3d2cd74 |
| QA-24 | Economic calendar | major | Literal `&nbsp;` in the Actual and Previous columns: the scraped HTML entities are not decoded. | `r1/econ-calendar-1280-retro.jpg` | fixed 27e8b65 + a68ce3c |
| QA-25 | Analysis chart (mobile) | major | At ≤560px the vertical drawing rail plus the plan's price-scale labels leave about 150px of plot, and the rail runs taller than the chart. | `aapl375-0.png` | fixed 3d2cd74 |
| QA-26 | Alerts popover | major | At 375px the alerts popover opens 48px off the left edge of the screen. | `alerts-375.png` | fixed 3d2cd74 |
| QA-27 | Top bar | major | At ≤560px the action buttons wrap over the page title and LIVE status. | `r1/sentiment-375-dark.jpg` | fixed 3d2cd74 |
| QA-28 | Server → Scheduler | major | The mode toggle ("Every N / At times") collapses to a one-letter sliver at 1280. The `auto` grid track shrinks to 0 because the control has `overflow:hidden`. | `server-1.png` | fixed 3d2cd74 |
| QA-29 | Server → Sources | minor | The Sources table is wider than the panel at 1280, so the "Next" column is cut off. | `server-0.png` | fixed 3d2cd74 |
| QA-30 | Seasonality / anchors | major | Yahoo silently downgrades `range=max&interval=1d` to monthly bars. So "this day N years ago" shows month-start dates with month-end closes (NVDA "2025-09-01 $186.58"), and the forward-week and calendar windows are computed on monthly data. | Yahoo probe (`dataGranularity: 1mo`) | fixed 27e8b65 |
| QA-31 | Chart | minor | After switching from an intraday timeframe back to D/W/M, the bars fill only the right half: the logical range isn't refit to the new series. | `aapl-d-after5m.png` | fixed ce06e64 |
| QA-32 | Server → Scheduler | polish | Every row repeats the same `aria-label` ("Interval", "Timezone", "Add a time"). An interval under 1 minute is silently reverted with no message. | `t_server` | fixed 3d2cd74 |
| QA-33 | Server → drawer | polish | Opened by clicking a source name, the drawer returns focus to `<body>` on close. | `t_server` | fixed 3d2cd74 |
| QA-34 | Settings | polish | One control in tab order (the native time input) shows no visible focus ring. | `t_focus` | not a defect: the input has a 3px accent focus ring through box-shadow (the checker only looked at outline); verified :focus-visible |
| QA-35 | Portfolio (mobile) | polish | At 375 the panel description is squeezed into a narrow column beside the base-currency and range controls. | `r1/portfolio-375-dark.jpg` | fixed 3d2cd74 |
| QA-36 | Settings → tours | major | "Replay all tours" did nothing for an onboarded account: it cleared toursSeen, but tours only auto-run before onboarding. | `t_features2` (no tour after replay) | fixed 79250c2 |
| QA-37 | Theme contrast (round 2) | minor | Remaining axe misses: SELL/sell chips at 4.2–4.4:1, mid-tone score badges and caution text at 3.4–4.1:1, the Warm filled-button text at 4.2:1, chart legend accent/muted text at 3.7–4.2:1, and the portfolio advice score at 3.7:1. | `sum-r2.txt` | fixed 6f6ac39 |
| QA-38 | Analysis header | minor | Between 720 and 1000px the top bar didn't wrap, so Report/PDF sat off screen (the page scrolled sideways at 768). | `r2/stock_AAPL-768-dark.jpg` | fixed 6f6ac39 |
| QA-39 | Server (mobile) | minor | The hidden "Actions" header in the activity table widened the page at 375px (the same bug as QA-02, in another table). | `sum-r2.txt` DOC-HSCROLL | fixed 6f6ac39 |
| QA-40 | Analysis page | major | After a failed analysis load, the company, insider, alert and history panes showed their empty states ("No Form 4 filings…", "nothing has tripped"), which asserts things about data that was never received. | `r3/stock_NVDA-1280-dark.jpg` | fixed db78a58 |
| QA-41 | Overview / Signals | minor | The 13-column technical table (and short interest) scrolled sideways inside half-width Overview panels at 1280, and on Signals below ~1500px. | `r3/ov-table.png` | fixed 94c7c7e |

## Verified working (no issue)

- **Login and 2FA:** login → TOTP verify → dashboard; logged-out 401s only on `/api/auth/me`.
- **Onboarding tour:** auto-runs once per view for a new account; "Replay all tours" works.
- **Task 1:**
  - USD and ILS holdings.
  - Base-currency switch persisted to the profile.
  - Totals checked against live FX (USD/ILS 3.0472): USD base $7.4K = $3.4K + ₪12.1K × 0.3282; ILS base ₪22.5K.
  - Per-currency subtotals; the "1 ILS = $0.3282" rate line.
  - Add, edit and remove a TASE holding with currency auto-detected.
- **Task 2:**
  - The palette's All / US / TASE filter with market badges.
  - TASE analysis page in ₪, with the not-applicable note and "REG ONLY" plus its tooltip on intraday.
  - "TASE · renormalized" chip on the Boom Score page.
- **Task 3:** intraday pre/post shading with a PRE/REG/POST legend; dashed "After $x" line on D.
- **Task 5:** snapshot preview; Download PNG includes the header strip and drawings (`snapshot-download.png`); Copy image puts `image/png` on the clipboard; toasts fire.
- **Tasks 6–9:**
  - Scheduler editing: interval, at-times plus add time, days, timezone, pause and resume.
  - A bad timezone rolls back with the server's reason.
  - Run-now while paused reports "Queued".
  - "Show similar" survives a reload and clears.
  - The error drawer shows the full traceback, Copy error and Retry now.
  - A `coalesced` job event was recorded; no `skipped` rows since start.
- **Task 10:** no native `title=` attributes left in the DOM; tooltips appear on hover and focus.
- **Task 11:**
  - All 18 tools place shapes and persist.
  - Style properties persist.
  - Drag, delete, Alt shortcuts, magnet, lock and hide.
  - Undo/redo within a session.
  - Themed recolour on Light.
- **Task 12:** every section collapses; collapse-all persists across reload and tickers; the chart keeps its width after a collapse → resize → expand.
- **Task 13:** chart, drawings and recharts follow the theme on Light, Ultraviolet and Warm.
- **Task 14:** margin-debt run-now → headless browser (workbook), 356 rows in 38s.
- **Task 15:** drafts save, load (replace), merge (with Ctrl+Z undo), edit/rename and delete.
- **Task 16:** Info → Updates renders and Check now works. `POST /api/update/apply` returns a 409 with its reason.

- **Task 1 (after the fixes):** the TEVA.TA officer-pay chips now read "$7.2M USD" (Teva reports in USD, while the ₪ listing price stays ₪). LUMI.TA reports in ILS.
- **QA-24 and QA-30 against live data:** after run-now, the NVDA anchors are daily (2025-09-26 $178.19, not the month-start 2025-09-01), and no `&nbsp;` is left in the calendar.
- **QA-21 against live data:** after a restart, margin_debt in the QA copy became "at 06:00 Mon (Asia/Jerusalem)" without an admin edit. Rows an admin edited stay as they are.

## Rounds

| Round | Scope | Found | Fixed in round | Remaining |
|---|---|---|---|---|
| 1 | Full matrix: 236 page loads (28 routes × 4 widths dark; 3 themes at 1280; main views at 375 × 3 themes; dyslexia with reduced motion), plus an end-to-end pass over tasks 1–16 | QA-01 … QA-35 | all but QA-17 and QA-34, which need no change (commits 2751e0e … 69f7fea) | – |
| 2 | Full matrix again, plus features: watchlists, alerts, reports, tooltips (hover, focus, Esc, long-press), theme menu, FX editor, tours, back-to-top, rail tooltips, logout | QA-36 … QA-39 | 79250c2, 6f6ac39 | – |
| 3 | Full matrix | QA-40, QA-41 | db78a58, 94c7c7e | – |
| 4 | Full matrix, plus regression of drawings (18 tools), snapshot download and copy, and drafts | **nothing new.** 0 console errors or warnings, 0 failed requests, 0 axe violations (`color-contrast`, `button-name`, `link-name`, `label`, `aria-valid-attr-value`, `nested-interactive`, `duplicate-id-aria`), 0 document-level horizontal overflow | – | clean |

**What round 4 still lists, and why it is intentional:**
- Data tables that scroll inside their own wrapper at 375 and 768 (`tableWrap` on Trades, Contracts, Analyst, Fundamentals, Econ calendar, Portfolio, History, Server). This is the designed mobile behaviour: the page itself doesn't scroll sideways.
- The ticker-tape marquee track.
- The visually hidden "Actions" table headers, which are clipped on purpose.

**Checks outside the browser:**
- Backend `pytest`: 823 passed.
- Frontend `npm run lint`: clean. `npm run build`: OK.
- A clean `npm ci` succeeds on the regenerated lockfile.
- Extension `npm test`: 48/48. Desktop `npm test`: 12/12.
