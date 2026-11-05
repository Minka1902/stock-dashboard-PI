# fixes_plan_782026.md — Stock-Dashboard Fix & Feature Plan

**Executor**: Opus 5 (`claude-opus-5`), one feature branch at a time, in the order below. This
plan file lives at the repo root on branch `claude/trading-methodology-gt8sko`. Base every
feature branch on `main`; push each branch separately and open a PR per branch; **remind the
user to merge each PR before starting a branch that depends on it** (stacking rules in the
Dependency section). Before using any library API (animejs v4, motion v12, recharts,
lightweight-charts, APScheduler, Playwright), pull current docs via the **context7 MCP server**
— do not code these APIs from memory. Big UI work uses **animejs + motion** (both already
installed), always through the `prefersReducedMotion()` policy in
`frontend/src/lib/motionConfig.js` (nothing essential may be motion-only).

Product principles from `CLAUDE.md` bind every change: signals not predictions; every signal
shows source + reasoning; never fabricate/placeholder data; sources record error status instead
of inventing values; single uvicorn worker; `db.py` is the only SQLite access point; SOURCES
ordering matters (`boom_score` after its inputs, `alerts` last).

**Per-feature verification loop** (run after every branch, before pushing):

1. `cd backend && python -m pytest -q`
2. `cd frontend && npm run lint && npm run build`
3. `cd frontend && npx playwright test e2e/<feature>.spec.js` (harness from Branch 0)

## Background: what exploration established

- `backend/app/analysis.py` already implements the entire trading methodology **except
  Section 3 (Fibonacci retracements/extensions + golden pocket)** — its header docstring maps
  A1–A9, B1–B6, C, D, E, F to existing functions (`support_resistance`, `trendline_levels`,
  `detect_channel`, `gaps`/`classify_gaps`/`gap_levels`, `round_number_levels`,
  `moving_averages`/`ma_structure`, `detect_breakout`, `staging_guidance`, `atr`/`compute_stop`,
  `target_ladder`, R/R ≥ 3 gate, `detect_patterns`, `detect_candles`, `volume_read`, `build()`
  confluence pipeline). Fibonacci (Branch 12) is the only net-new engine work.
- The Server and Earnings pages are unreachable: their `server`/`earnings` keys were
  merge-dropped from `frontend/src/lib/routes.js` TITLES and `App.jsx` VIEWS (PRs #42/#43);
  sidebar entries and panel imports remain. The ServerPanel admin gate lived in the deleted JSX
  branch (original wiring in commit `0081cb3`) and must be restored.
- The earnings persistence layer is missing entirely (`earnings` table +
  `db.upsert_earnings/get_earnings/get_earnings_for`) → `GET /api/earnings` 500s and
  `backend/tests/test_earnings.py` fails.
- `/api/backtest/*` routes are never registered although `backend/app/backtest.py` is
  implemented, tested, and imported — the track-record panel errors.
- Header popups collide with sticky table rows because `.band`'s `backdrop-filter` creates a
  stacking context that traps the popups (z 40/50/60) below `.scroll` content.
- Analysis pages open via `window.open(..., "_blank", "noopener")` → null opener →
  `window.close()` refused; `history.length === 1` → Back strands a duplicate dashboard tab.
- Skips are deliberate throttling (`ingest._should_skip`) with reason strings already recorded
  in `source_runs.detail` and `next_eligible_at` exposed by `/api/server/sources`.
- Per-source cadence is env-only today; a DB-backed live-reschedule precedent exists
  (`app_settings` + `scheduler.reschedule_job("daily_analysis", ...)`).
- `eps_actual`/`surprise_pct` already cross the wire; the frontend ignores them.

---

## Branch 0 — `feat/e2e-harness` (Playwright foundation; everything depends on it)

**Goal**: Greenfield Playwright setup so every later branch can verify in a real browser.
**Root cause**: No E2E infrastructure exists; auth is mandatory TOTP so tests must script
enrollment.

Files: `frontend/package.json` (devDeps: `@playwright/test`, `otpauth`),
`frontend/playwright.config.js`, `frontend/e2e/helpers/auth.js`, `frontend/e2e/helpers/seed.js`,
`frontend/e2e/serve.sh`, `frontend/e2e/smoke.spec.js`, `backend/app/config.py` +
`backend/app/main.py` (one small flag), `.env.example`.

Steps:

- Chromium is pre-installed at `/opt/pw-browsers` (`PLAYWRIGHT_BROWSERS_PATH` set) — **never run
  `playwright install`**.
- Single-port mode: `serve.sh` runs `npm run build` (skip if `dist/` newer than `src/`), then
  `uvicorn app.main:app --port 8000` from `backend/` with `STOCKS_DB_PATH` pointed at a
  throwaway DB. Wire it as the Playwright `webServer` (consult context7 for the current config
  shape).
- Add a `STOCKS_SCHEDULER_DISABLED` env flag (`config.py` + a guard around `scheduler.start()`
  in the `main.py` lifespan) so E2E runs never hit external upstreams; document it in
  `.env.example`. This is the only backend change in this branch; add a pytest asserting the
  flag prevents scheduler start.
- `auth.js`: mirror `backend/tests/conftest.py::authenticate` over HTTP — POST
  `/api/auth/register` (the first registered user becomes admin), TOTP setup → capture the
  secret, compute a code with `otpauth`, enable TOTP, log in, save `storageState`. Provide
  `adminState()` (first user) and `userState()` (second user, non-admin).
- `seed.js`: insert deterministic rows (watchlist ticker, OHLC bars, source_runs, earnings,
  suggestion history) into the E2E DB via a small `python -c` helper using `app.db` — tests
  assert against seeded data, never live upstreams.
- `smoke.spec.js`: login lands on Market Sentiment; no console errors.

Playwright asserts: register/enroll/login round-trip works; dashboard renders.

---

## Branch 1 — `fix/routing-views` (tasks 1, 2, 13 + palette gaps)

**Goal**: `/server` and `/earnings` reachable again; the Server entry lives next to Settings and
Info/Guide in the account menu (admin-only), not the sidebar.
**Root cause**: `routes.js` TITLES and `App.jsx` VIEWS lost their `server`/`earnings` keys in
merges (PR #42 `2c1e722`, PR #43 `1d06074`); `parseRoute` → `known:false` → URL rewritten to `/`.

Files: `frontend/src/lib/routes.js`, `frontend/src/App.jsx`,
`frontend/src/components/Sidebar.jsx`, `frontend/src/components/UserMenu.jsx`.

Steps:

- Recover the original wiring: `git show 0081cb3 -- frontend/src/App.jsx` (and the pre-merge
  parents of `2c1e722`/`1d06074`) to copy the exact dropped TITLES + VIEWS entries.
- `routes.js`: add `earnings: "Earnings"` and `server: "Server"` to TITLES.
- `App.jsx` VIEWS: add the `earnings` entry and the `server` entry **with the admin gate
  restored from `0081cb3`**: `p.user?.is_admin ? <ServerPanel/> : <error message>` (both panel
  imports already exist, currently dead).
- `Sidebar.jsx`: remove `server` from ADMIN_NAV (it moves to the account menu); keep the
  earnings entry.
- `UserMenu.jsx`: insert an admin-only "Server" menu item between Settings and Info/Guide,
  gated on `user.is_admin`, calling `go("server")`, reusing the existing item styling.
- `App.jsx` command-palette items: add `earnings`, `suggestion-history`, and an admin-gated
  `server` entry.

Tests: pytest — none (frontend only). Playwright (`routing.spec.js`): as admin, visiting
`/server` renders the Process section and the URL survives reload; UserMenu shows Server between
Settings and Info; as non-admin, `/server` renders the not-authorized message and UserMenu has
no Server item; `/earnings` renders the Earnings panel; the Cmd/Ctrl+K palette lists the new
entries.

---

## Branch 2 — `fix/popup-stacking` (task 3)

**Goal**: Top-bar popups (theme menu, user menu, alerts bell) render above scrolled content,
including the sticky first `<tr>` of PortfolioPanel groups.
**Root cause**: `.band` (`App.module.css:18-30`) has `backdrop-filter: blur(6px)` → a stacking
context with `z-index: auto`, trapping ThemeMenu (z40) / UserMenu (z50) / AlertsBell (z60)
below `.scroll`'s sticky `thead th` and `.groupRow td` elements.

Files: `frontend/src/App.module.css` (+ small z bumps in CommandPalette/Tour css if needed).

Steps: add `position: relative; z-index: 70;` to `.band` (keep the popups' internal z-indexes).
Document the app layer scale in a CSS comment: in-scroll sticky ≤ 10, in-row MenuButton menus
60, band 70, tooltips 90 (Branch 7), palette/tour/modals ≥ 100 — verify CommandPalette (100) and
Tour (1000) overlays already exceed 70. Portalling the three popups was evaluated and rejected
(positioning complexity for no extra benefit once the band is lifted) — note the tradeoff in
the PR.

Tests: Playwright (`stacking.spec.js`): on `/portfolio` with a seeded grouped holding, open each
popup and assert `document.elementFromPoint(center-of-popup)` resolves inside the popup while a
sticky group row sits beneath it in the viewport; repeat with the page scrolled.

---

## Branch 3 — `fix/tab-navigation` (tasks 4, 17)

**Goal**: Back/close on the analysis page returns you where you came from; ticker tabs opened
from the dashboard actually close.
**Root cause**: `nav.js:79-81` opens tabs with `"noopener"`, so `window.close()` is refused and
`history.length === 1` makes `goBack()` strand a dashboard replica.

Files: `frontend/src/lib/nav.js`, `frontend/src/App.jsx`.

Steps:

- `openTickerTab`: drop `"noopener"` → `window.open(path, "_blank")`, with a comment: safe
  because this helper only ever opens **our own same-origin app**; the opener is our own page
  and no third-party content flows through it (reverse-tabnabbing requires a foreign
  destination).
- Add `export function closeOrBack(fallback)` to `nav.js`: if
  `window.opener && window.history.length <= 1` → `window.close()` (script-opened tab —
  returning the user to the exact tab and scroll position they came from); else
  `goBack(fallback)` (same-tab entries like the alerts bell walk history back). `goBack`
  already falls back to `/` for opener-less direct visits.
- `App.jsx`: `closeDetail` calls `closeOrBack()`; rewrite the stale comment block at
  lines 172-177.

Tests: Playwright (`tab-nav.spec.js`): click a watchlist ticker → `context.waitForEvent("page")`
yields `/stock/X`; click Back there → the page fires `close`. Alerts-bell same-tab path: open an
alert → Back returns to the prior view URL. A direct `browser.newPage()` to `/stock/NVDA` →
Back lands on `/` (no close attempt).

---

## Branch 4 — `feat/earnings-db-layer` (task 18 backend prerequisite; **blocks Branch 5**)

**Goal**: Restore the merge-dropped earnings persistence so `GET /api/earnings` stops 500ing
and `test_earnings.py` passes.
**Root cause**: `db.py` has no `earnings` table nor `upsert_earnings`/`get_earnings`/
`get_earnings_for`, but `main.py` (lines 152/287/1326/1337) and
`backend/tests/test_earnings.py:95-132` reference them.

Files: `backend/app/db.py`, `backend/app/main.py`, `frontend/src/hooks/useDashboardData.js`.

Steps:

- `db.py init_schema`, following the existing `CREATE TABLE IF NOT EXISTS` conventions:

  ```sql
  CREATE TABLE IF NOT EXISTS earnings (
      ticker TEXT NOT NULL, event_date TEXT NOT NULL,
      is_estimate INTEGER NOT NULL, timing TEXT NOT NULL DEFAULT '',
      eps_estimate REAL, eps_actual REAL, surprise_pct REAL,
      revenue_estimate REAL, quarter TEXT NOT NULL DEFAULT '',
      fetched_at TEXT, PRIMARY KEY (ticker, event_date));
  CREATE INDEX IF NOT EXISTS idx_earnings_date ON earnings(event_date);
  ```

  (column set = `EarningsEvent` in `models.py:219`; `main.py:152` reads `fetched_at`).
- Accessors: `upsert_earnings(conn, events)` (ON CONFLICT(ticker, event_date) DO UPDATE every
  field), `get_earnings(conn, ...)`, `get_earnings_for(conn, ticker)`. **Match the exact
  signatures used by `main.py:1326`/`:1337` and `test_earnings.py:95-132` — read those call
  sites first.**
- `main.py` SOURCES registry: convert the earnings 3-tuple to
  `SourceSpec(..., retry_interval=config.EARNINGS_RETRY_INTERVAL_SECONDS)` (the constant is
  currently declared but ignored).
- `useDashboardData.js:40-44`: add `"earnings"` to `EXTERNAL_SOURCES`.

Tests: `test_earnings.py` now passes; add a roundtrip test for `date_from`/`date_to`/`tickers`
filtering and conflict-update (re-upsert with `eps_actual` filled). Playwright: `/earnings`
(route from Branch 1) loads with seeded rows, no error banner.

---

## Branch 5 — `feat/earnings-beat-miss` (task 18 UI; after Branches 1 + 4)

**Goal**: Show whether a reported quarter was positive or negative.
**Root cause**: `EarningsPanel.jsx:182` reads only `eps_estimate`; `eps_actual`/`surprise_pct`
already cross the wire.

Files: `frontend/src/components/EarningsPanel.jsx` + `.module.css`.

Steps: in the day-detail rows, when `eps_actual != null` render a badge: sign of `surprise_pct`
if present, else `eps_actual − eps_estimate` when both present; > 0 → "beat +X%" (positive
tone), < 0 → "miss −X%" (negative), exactly 0 → "in line" (neutral); **no actual → no badge**
(never fabricate). Show `act. EPS` beside `est. EPS`. Tokenized colors (no `#fff` — see
Branch 13). Subtle motion pop-in per badge, reduced-motion aware.

Tests: Playwright (`earnings.spec.js`): seed one beat (`surprise_pct=4.2`), one miss, one
unreported → assert badge text/tone attributes and the absence of a badge on the unreported row.

---

## Branch 6 — `feat/backtest-track-record` (tasks 5, 6; after Branch 0; independent of 1–5)

**Goal**: The track-record panel loads real data, gains a per-ticker breakdown + `by_kind`, and
looks native to the app.
**Root cause**: `main.py` never registers `/api/backtest/*` although `backend/app/backtest.py`
is implemented, tested, and imported; the frontend calls the routes (`api.js:100-103`).

Files: `backend/app/main.py`, `backend/app/backtest.py`,
`backend/tests/test_backtest_routes.py` (new),
`frontend/src/components/BacktestPanels.jsx` + `.module.css`.

Steps:

- Routes (plain auth, not admin — the record is user-scoped):
  `GET /api/backtest/track-record?months=` → `backtest.track_record(conn, user.id, months)`;
  `GET /api/backtest/signal-replay?horizon=&months=` → `backtest.signal_replay(...)`. Param
  names must match `api.js:100-103` (`months`, `horizon`).
- `backtest.track_record`: add a `by_ticker` list — the same `_stats` bucket shape keyed by
  ticker (label = ticker), built from the per-entry `suggestion_history.with_outcomes` rows,
  sorted by `n` desc, capped ~30. **This is what makes the table say which stock it's talking
  about.**
- `BacktestPanels.jsx` TrackRecord: render the currently ignored `by_kind` buckets; add a
  per-ticker expandable section (motion height accordion, staggered row entrance — consult
  context7 for motion v12 stagger); replace the positional "first row is overall" bolding hack
  with an explicit styled overall row; restyle with the shared PanelHeader + design tokens;
  column-header tooltips arrive in Branch 7's apply pass.

Tests: pytest — routes return 200 with `overall/by_action/by_kind/by_ticker/coverage/benchmark`
keys; 401 unauthenticated; `by_ticker` math spot-checked against seeded suggestion history.
Playwright: `/suggestion-history` → the panel shows data (no error state), tab switch works,
the per-ticker section expands and lists the seeded ticker.

---

## Branch 7 — `feat/tooltip-primitive` (task 12; before Branches 8, 9)

**Goal**: One real Tooltip primitive, applied app-wide.
**Root cause**: only `InfoTip.jsx` exists — imported nowhere, clipped by panel
`overflow:hidden`, always-above positioning; everything else is native `title=`.

Files: new `frontend/src/components/Tooltip.jsx` + `.module.css`; refactor `InfoTip.jsx`;
apply-pass edits in ServerPanel, BoomScorePanel (CHIP_META tips), BacktestPanels, EarningsPanel,
PortfolioPanel, StockDetailPanel.

Steps: portal to `document.body` (escapes overflow clipping and stacking contexts); position
from the trigger's `getBoundingClientRect` with above/below flip and horizontal clamping; open
on hover **and** focus, close on leave/blur/Escape; ~250 ms open delay; `role="tooltip"` +
`aria-describedby`; motion fade/scale honoring `prefersReducedMotion()`; z-index 90 per the
Branch 2 layer scale. Refactor `InfoTip` to delegate rendering to Tooltip while keeping its
glossary lookup. Apply pass: server Stat labels and table headers, boom chips, track-record /
replay column headers, earnings badges/labels, portfolio headers, analysis-page stat labels
(ATR, R/R, stop basis). Remove `title=` wherever a Tooltip replaces it (no doubles).

Tests: Playwright (`tooltip.spec.js`): hover a server Stat → the tooltip node exists under
`document.body`, visible, not clipped near viewport edges; Escape dismisses; works under
`reducedMotion: "reduce"` emulation; keyboard focus shows it.

---

## Branch 8 — `feat/server-observability` (tasks 7, 8, 9, 11; after Branches 1, 7)

**Goal**: Recent-activity filtering, inline error expansion instead of the Errors section,
honest skip/throttle presentation, informative CPU tooltips.
**Root causes**: 7 — the events table has no filter or `<thead>`; 8 — the Errors section
duplicates the Sources table; 9 — skips are by-design throttling presented like failures;
11 — per-core meters show load with no context, and psutil cannot attribute work per core.

Files: `frontend/src/components/ServerPanel.jsx` + `.module.css`,
`frontend/src/hooks/useServerStatus.js` (decision: filter client-side; `/api/server/events`
stays untouched — bump the fetched `limit` to ~200 so filters have data).

Steps:

- Recent activity: add a `<thead>`; add a "show similar" filter — clicking a source name in any
  row (or a select of distinct ids) narrows the table to that id, with a dismissible
  "filtered: X" chip; animate row changes with motion.
- Remove the Errors section entirely; make failing Sources rows expandable: a button in the row
  toggles an inline `<tr colSpan>` with `<pre>{error_detail}</pre>`, using the
  `StockAlerts.jsx:74-88` motion-height accordion pattern; chevron affordance; keyboard
  accessible.
- Skips: rows/status cells with outcome `skipped` parse the recorded detail
  (`"min_interval: 120s elapsed of 3600s"`) and render "throttled — waiting (120s of 3600s),
  next eligible <T>" (the sources API already returns `next_eligible_at`/`eligible_now`); a
  Tooltip explains *why throttling exists* (rate-limited upstreams) and points at the Scheduler
  section (Branch 9) to change cadence. Errors always surface `error_detail`. **Every skip and
  failure states its reason; nothing is silently skipped.**
- CPU: wrap each core Meter in a Tooltip: "Core N — X%. Load shown is process-wide; per-core
  attribution isn't available." plus a shared "currently working on" readout beside the CPU
  header built from `overview.running_sources` (source + elapsed seconds) and
  `overview.scheduler.jobs` next-run times. **Do not fabricate per-core attribution.** Keep the
  animejs meter fill.

Tests: pytest — none (no backend change). Playwright (`server.spec.js`): seed skipped + errored
`source_runs`; assert the filter narrows the table; an errored source row expands to show the
seeded traceback; the standalone Errors section is gone; skip rows contain "throttled"; a
core-meter tooltip contains "process-wide".

---

## Branch 9 — `feat/scheduler-ui` (task 10; stack on Branch 8 — same file)

**Goal**: The admin can change per-source fetch cadence (the thing that causes skips) and the
global refresh interval from the Server page.

### Schema (`db.py init_schema` + accessors; follow the `_try_add_column` conventions)

```sql
CREATE TABLE IF NOT EXISTS source_schedule (
    source               TEXT PRIMARY KEY,
    min_interval_seconds INTEGER,              -- NULL = use config default
    enabled              INTEGER NOT NULL DEFAULT 1,
    updated_at           TEXT NOT NULL);
```

Accessors: `get_source_schedules(conn) -> dict[str, SourceSchedule]`,
`upsert_source_schedule(conn, source, min_interval_seconds, enabled)`. New model
`SourceSchedule`: `source, min_interval_seconds: int | None, enabled: bool, updated_at`. Also
`_try_add_column(conn, "app_settings", "refresh_interval_seconds", "INTEGER")` +
`AppSettings.refresh_interval_seconds: int | None = None` (None → config default), threaded
through `get_app_settings`/`upsert_app_settings`.

### API (admin-only via the existing `_require_admin`)

- `GET /api/server/schedule` → one row per SOURCES key: `{source, default_min_interval,
  override_min_interval, effective_min_interval, retry_interval, enabled, force_on_daily,
  eligible_now, next_eligible_at}` plus `{refresh_interval_seconds, refresh_default}` for the
  global cycle.
- `PUT /api/server/schedule/{source}` body `{min_interval_seconds: int|null, enabled: bool}`;
  404 unknown source; 422 outside [30, 2592000]; null clears the override. Returns the updated
  row.
- Global interval: extend the existing app-settings PUT and on change
  `scheduler.reschedule_job("refresh_all", trigger=IntervalTrigger(seconds=...))` — exact
  precedent at `main.py:1144-1148` (`daily_analysis`). Consult context7 for APScheduler
  `reschedule_job`/`IntervalTrigger`.

### Ingest integration

Keep `ingest.run_source` unchanged. At the three call sites (`_refresh_all`,
`_run_daily_analysis`, the manual refresh route): read `db.get_source_schedules(refresh_conn)`
once per cycle; if `enabled == False`, record a run with outcome `"skipped"`, detail
`"disabled by admin"` (visible, honest) and continue; else
`effective = override if override is not None else spec.min_interval`. Config stays the
fallback; SOURCES ordering is untouched (disabling never reorders; `boom_score`/`alerts`
constraints hold).

### UI (ServerPanel new "Scheduler" section, between Sources and Recent activity)

A table per source: name, default cadence (humanized), override editor (number + unit select
min/hr/day), enabled toggle, next-eligible preview from the GET payload, per-row Save → PUT,
motion success feedback. A global "refresh cycle every N s" editor above it. Honesty copy (with
Tooltip): "These are minimum intervals, not exact times — a source refreshes on the first cycle
after its interval elapses."

Tests: pytest (`test_scheduler_api.py`) — GET lists every SOURCES key with correct defaults;
PUT override persists and shifts `next_eligible_at` in `/api/server/sources`; a disabled source
produces a `skipped`/"disabled by admin" run on the next cycle; non-admin 403; unknown source
404; bounds 422; global PUT reschedules `refresh_all` (assert via `scheduler.get_job`).
Playwright: edit an interval → Save → the row shows the new humanized cadence after reload;
toggle a source off → Sources/Recent activity reflect "disabled by admin".

---

## Branch 10 — `feat/analysis-collapsible-panes` (task 14; before Branch 12 — same file)

**Goal**: Every analysis-page section collapses, persistently.
**Root cause**: `StockDetailPanel.jsx` `Pane` (lines 29-39) is static.

Files: `frontend/src/components/StockDetailPanel.jsx` + `.module.css`.

Steps: give `Pane` a `paneKey` prop (stable keys: `detail:chart`, `detail:company`,
`detail:insiders`, `detail:alerts`, `detail:history`, `detail:anchors`, `detail:x`,
`detail:plan`, `detail:structure`, `detail:patterns`, `detail:trendlines`, `detail:breakout`,
`detail:candles`, `detail:why`); reuse `CollapseToggle.jsx` in `paneHead`; persist via the
existing `settings.collapsed` map (exactly like `App.jsx:195-201`); animate with the
`StockAlerts.jsx:74-88` motion height pattern. The chart pane **unmounts** ChartPro when
collapsed (lightweight-charts autosizing inside a 0-height container is fragile;
rebuild-on-expand is acceptable — note in a comment).

Tests: Playwright (`detail-collapse.spec.js`): collapse Trade plan on `/stock/<seeded>`, reload
→ still collapsed (header visible, body absent); chart pane collapse/expand re-renders the
chart without console errors.

---

## Branch 11 — `feat/portfolio-category-ui` (tasks 15, 16; independent)

**Goal**: The sector/category is a read-only themed chip normally and an editable themed menu
only in row-edit mode; no OS-chrome select popup.
**Root cause**: `PortfolioPanel.jsx` CategorySelect (lines 370-392) is an always-editable
native `<select>` (unstylable options that ignore the app theme), while edit mode (the pencil
button) only swaps the shares/avg-cost cells.

Files: `frontend/src/components/PortfolioPanel.jsx` + `.module.css`.

Steps: when `editing !== h.ticker` render a static tokenized chip (category label, manual/auto
styling preserved); when editing, render a category picker built on the existing `MenuButton`
primitive (WatchlistPanel precedent — fully theme-tokenized, z-index already fits the Branch 2
layer scale), calling the existing `onSetCategory`. Delete the `.catSelect` native select.

Tests: Playwright (`portfolio-category.spec.js`): non-edit rows contain the chip and no
combobox; pencil → the MenuButton menu opens (DOM-rendered options — assert against the menu
list, which a native select popup could never expose); pick a category → the chip updates;
repeat under `data-theme="light"` and `"warm"`.

---

## Branch 12 — `feat/fibonacci` (methodology Section 3 — the only missing section; after Branch 10)

**Goal**: Fibonacci retracements/extensions with the golden pocket and the confluence rule,
wired through evidence, stop, targets, the analysis page, the chart, and the report.

### Engine (`backend/app/analysis.py` — pure functions; add `#3 fib -> fib_analysis()` to the header docstring map)

Tunables: `_FIB_RETRACEMENTS = (0.236, 0.382, 0.5, 0.618, 0.65, 0.786)`;
`_FIB_EXTENSIONS = (1.272, 1.618, 2.618)`; `_FIB_CONFLUENCE_TOL = 0.015`;
`_FIB_AT_LEVEL_TOL = 0.01`.

Signatures:

- `def fib_swing(pivots: list[dict], trend_dir: str) -> tuple[dict, dict, str] | None` — pick
  the governing swing from `swing_pivots()` output: uptrend → most recent significant swing
  **low**, paired with the highest subsequent swing high (draw low→high); downtrend → most
  recent significant swing **high** paired with the lowest subsequent low (high→low); sideways
  → the widest low/high pair in the last ~120 bars, direction from whichever extreme is more
  recent. Return None when < 2 usable pivots or the range < 1% of price (degenerate).
- `def fib_levels(swing_low: float, swing_high: float, direction: str) -> list[FibLevel]` —
  price conventions (state them in the docstring): uptrend retracement =
  `high − ratio × (high − low)`; uptrend extension = `low + ratio × (high − low)`; downtrend
  mirrored (`retr = low + ratio × range`, `ext = high − ratio × range`). Mark the 0.618 and
  0.65 rows `in_golden_pocket=True`.
- `def fib_confluences(levels, support, resistance, trendlines, mas, tol=_FIB_CONFLUENCE_TOL) -> None`
  — annotate each level in place with human strings (`"support 138.4 (pivot, 3 touches)"`,
  `"rising trendline @ 137.9"`, `"ma50 @ 138.8"`) for any horizontal SRLevel, unbroken
  trendline `current_value`, or MA within `tol`. **The rule: a fib level with an empty
  `confluences` list is displayed but never generates evidence, never a stop, never upgrades a
  target — never trade a fib level in isolation.**
- `def fib_analysis(bars, pivots, price, trend_dir, support, resistance, trendlines, mas) -> FibAnalysis | None`
  — orchestrates the above; sets `nearest_level`, the `golden_pocket` band, and `invalidation`
  (the 0.786 price; the note names the swing extreme as final invalidation).

### Models (`backend/app/models.py`)

```python
class FibLevel(BaseModel):
    ratio: float; price: float
    kind: str                    # "retracement" | "extension"
    label: str                   # "61.8%", "161.8% ext"
    in_golden_pocket: bool = False
    confluences: list[str] = []  # empty = cosmetic only (methodology rule)

class FibAnalysis(BaseModel):
    direction: str               # "up" (low->high) | "down" (high->low)
    swing_low: dict; swing_high: dict   # {date, price} — same shape as pattern pivots
    levels: list[FibLevel]
    golden_pocket: dict          # {"low": float, "high": float} price band (0.618–0.65)
    nearest_level: FibLevel | None = None
    invalidation: float | None = None   # 0.786 price; beyond it the swing thesis is dead
    note: str = ""
```

`StockAnalysis` gains `fib: FibAnalysis | None = None` (placed with the structure fields).

### `build()` integration

After trendlines/MAs:
`fib = fib_analysis(daily, pivots, px, tr, support, resistance, trendlines, mas)`. Evidence via
the existing `ev()` closure, **confluent levels only**, explicit absence otherwise:

- price inside the golden pocket with ≥ 1 confluence → `ev("fib", "bullish", 10, "In the golden
  pocket (61.8–65% retracement of the <date> <low>→<high> swing) with <confluence> —
  high-probability bounce zone.", data)` (mirrored bearish, −10, for a downtrend swing);
- price within `_FIB_AT_LEVEL_TOL` of another confluent retracement → ±6 with the confluence
  named in the sentence;
- a close beyond the 0.786 level against the swing → `ev("fib", "bearish", -8, "Retracement
  broke 78.6% — the <low>→<high> swing is invalidated; the swing low is the last line.")`
  (mirrored in downtrends);
- a confluent extension within 1% overhead → neutral weight-0 note ("161.8% extension at X
  overhead — natural profit-taking shelf").

**Stop**: extend `compute_stop(entry, atr_val, support, trendlines=None, fib=None)`; candidate
= the highest **confluent retracement** below entry (uptrend fib only) minus the existing 1-ATR
buffer; insert into the candidates list as `(stop_fib, "fib")` between "trendline" and "atr".
`stop_basis` may now be `"fib"` — the models.py comment gains the value; frontend/report render
it as-is.

**Targets**: extend `target_ladder(entry, risk, resistance, trend_dir, measured_move,
fib_extensions=None)`; for each rung, a confluent extension within 1% of the rung price appends
"aligns with the 161.8% fib extension at X" to `why` and upgrades `possible → likely` (never
overriding a capping-resistance `unlikely`). Extensions also show directly in the Trade-plan UI
as TP shelves. **Do not inject fib levels into the support/resistance lists** — they're
truncated `[:8]`, re-sorted, and consumed elsewhere; the methodology says fib only matters *in
confluence with* those levels, not as one of them.

### Surfacing

- `StockDetailPanel.jsx`: a new collapsible Pane "Fibonacci" (`detail:fib`, between Structure
  and Patterns): swing description (dates + prices + direction), a levels table (label, price,
  distance from price, confluence chips, golden-pocket highlighted row), the invalidation line;
  extension rows badged "TP". The Trade-plan pane shows extension alignments in rung `why`
  (already rendered). Fib evidence flows into "Why — the read" automatically. Motion: staggered
  level-row entrance; an animejs pulse on the golden-pocket row when price is inside it
  (reduced-motion aware).
- `ChartPro.jsx`: in the daily-overlays block, when `analysis.fib` exists draw retracement
  `createPriceLine`s on the main series (dashed, labeled "38.2%" etc.), the golden pocket as
  two tighter lines (lightweight-charts has no native band — verify the current API via
  context7 before choosing between price lines and a custom band primitive), extensions dotted.
  Respect the existing overlays preference toggle and the theme-color work from Branch 13.
- `backend/app/report.py`: a new `_section("Fibonacci", ...)` between "Support & resistance"
  and "Patterns" — a `_table` of level/price/confluence/distance + a golden-pocket callout; add
  pocket-edge hlines to `render_svg_chart` when `analysis.fib` is present.

### Tests (`backend/tests/test_fibonacci.py`, concrete numbers)

A synthetic uptrend swing 100→200 (bars with a clean pivot low at 100, high at 200):
retracements exactly 176.4 / 161.8 / 150.0 / 138.2 / 135.0 / 121.4; pocket
`{low: 135.0, high: 138.2}`; extensions 227.2 / 261.8 / 361.8. Downtrend 200→100 mirrored:
0.618 retr = 161.8; 1.618 ext = 38.2. Confluence rule: place a 3-touch pivot cluster at ~138 →
0.618 gains a confluence and, with the last close 136.5 (in the pocket), `build()` emits a
bullish `fib` evidence of weight 10; move the cluster to 155 → **no** `fib` evidence at all
(`assert not any(e.component == "fib")`). Invalidation: last close 118 (< 121.4) → bearish fib
evidence. Stop: entry just above the confluent 138.2 with a wide ATR → `compute_stop` returns
basis `"fib"`. Ladder: a rung within 1% of 227.2 with clear runway → `why` mentions the
extension. Degenerate: < 2 pivots or a 0.5% range → `fib is None`. Regression:
`test_analysis_methodology.py` and `test_patterns_forming.py` stay green (non-confluent fib
must not move conviction). Playwright (`fib.spec.js`): seeded OHLC producing a known swing →
the Fibonacci pane lists the exact level prices; the chart shows fib price-line labels; the
report contains the Fibonacci section.

---

## Branch 13 — `chore/theme-and-polish` (pre-found improvements; after Branches 1–12)

One commit per bullet, all on this branch:

- **markAlertsRead per-key**: `useDashboardData.markAlertsRead` gains an optional `keys` array
  passed through to the API (verify the backend `/api/alerts/read` route accepts `dedup_keys`;
  add if not). Fixes `App.jsx:219-222` and the AlertsBell per-alert check button silently
  marking everything read.
- **useTheme toggle + TopBar**: export `THEME_META` (name → `{luminance: "dark"|"light"}`) from
  `useTheme`; `toggle()` switches to the opposite-luminance default (retro→light, warm→dark)
  instead of always jumping to dark; TopBar derives its lightness check from `THEME_META`
  instead of a hardcoded list.
- **`--shadow-lg`**: define per theme in `src/index.css` (referenced by UserMenu/MenuButton,
  defined nowhere).
- **Theme-reactive charts**: add a shared `useThemeTokens()` hook (reads `getComputedStyle`
  custom properties; re-resolves via a MutationObserver on `data-theme`); `ChartPro.jsx:19-28`
  COLORS becomes a function of it (fixes invisible gridlines on light/warm) and the chart
  rebuilds on theme change; `SuggestionHistoryStrip.jsx:68-95` recharts styling switches to the
  same hook. Consult context7 for lightweight-charts `applyOptions` and recharts theming.
- **`color:#fff` chips** (~14 module.css files — find with grep): introduce an `--on-accent`
  token per theme and replace.
- **SignalSidecar**: mount it on the analysis page as a collapsible Pane (`detail:boom`, near
  Trade plan) so boom components finally appear there; render an explicit "not on the watchlist
  — no Boom Score" empty state where applicable (report.py precedent).
- **.env.example**: `STOCKS_X_MIN_INTERVAL_SECONDS` documented as 900 vs code default 3600 —
  fix the doc to 3600 (code is truth; decision recorded below).

Tests: Playwright additions — a per-alert check leaves other alerts unread; theme toggle from
warm lands on dark; chart gridlines visible in the light theme (screenshot pixel probe); the
boom pane renders on the analysis page.

---

## Dependency ordering

```
0 e2e-harness ─┬─ 1 routing ─┬─ 5 earnings-ui (also needs 4)
               │             ├─ 8 server-observability (needs 7) ── 9 scheduler-ui (stack on 8)
               ├─ 2 stacking │
               ├─ 3 tab-nav  │
               ├─ 4 earnings-db (before 5)
               ├─ 6 backtest
               ├─ 7 tooltip (before 8, 9; apply-pass touches 5/6 surfaces)
               ├─ 10 collapsible-panes ── 12 fibonacci (same file: StockDetailPanel)
               ├─ 11 portfolio-category
               └─ 13 polish (last) ── final sweep
```

Branches sharing a file (8→9 ServerPanel; 10→12 StockDetailPanel) must either wait for the
earlier PR to merge or be branched off it (a stacked PR — say so in the PR description).
Everything else is independent off `main`. **Remind the user to merge each PR as it
completes.**

## Final iterative Playwright sweep (after all branches merge)

1. Rebuild + serve single-port (`e2e/serve.sh`) against a freshly seeded DB.
2. Run the full spec suite (`npx playwright test`).
3. Run a sweep spec that, **as admin and as non-admin, in all four themes** (`data-theme`
   dark/light/retro/warm), visits every `VIEW_KEYS` path plus `/stock/<seeded>`, exercising:
   each collapse toggle, each popup over sticky content, the palette, and one tooltip per page
   — capturing console errors, failed network requests, and screenshots.
4. Triage every finding into a numbered fix list appended to this plan; fix on
   `chore/sweep-fixes-N`; re-run steps 1–3.
5. Repeat until a full pass yields zero console errors, zero failed requests, and all specs
   green. Then run `cd backend && python -m pytest -q` one final time.

## Decisions made (recorded; do not relitigate during implementation)

- **Task 9 — throttling stays.** Skips are deliberate rate-limit protection
  (`ingest._should_skip`); removing them would hammer rate-limited upstreams. Instead: skips
  are *presented* as "throttled — waiting (Xs of Ys), next eligible at T" everywhere, and the
  Scheduler UI (Branch 9) gives the admin control over the intervals that cause them. A
  disabled source records an explicit "disabled by admin" skip — status is recorded, never
  invented. Net effect: no task is ever skipped without a visible reason.
- **Task 17 — `noopener` removed** from `openTickerTab` only. The helper exclusively opens our
  own same-origin app; the opener is our own dashboard page, so reverse-tabnabbing (the reason
  `noopener` exists) does not apply, and keeping it is exactly what breaks `window.close()` and
  strands duplicate dashboard tabs.
- **Task 11 — CPU honesty.** psutil cannot attribute work to a specific core; tooltips
  explicitly say load is process-wide and pair it with what the process *is* doing
  (`running_sources` + scheduler jobs). No fabricated per-core attribution, ever.
- **Task 7 — client-side event filtering** (no `?source=` backend param) with a larger fetch
  limit; simplest, and keeps `/api/server/events` stable.
- **Fib levels never merge into the support/resistance lists** — confluence with them is the
  signal (methodology rule); merging would double-count and pollute stop/target inputs.
- **`.env.example` follows code** for `STOCKS_X_MIN_INTERVAL_SECONDS` (3600).
