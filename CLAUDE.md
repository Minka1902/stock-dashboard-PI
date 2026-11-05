# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A **multi-user Stock Signal Dashboard** that aggregates *public* signals
(federal contracts, SEC Form 4 insider trades, congressional trades, news, technicals,
short interest, social sentiment, analyst ratings, fundamentals, seasonality) and surfaces
explainable "something is happening here" indicators per ticker. Accounts require
**mandatory TOTP 2FA** (Google/Microsoft Authenticator); each user has their own
watchlist, portfolio and notification profile, while market data is shared.

**Core product principle — signals, not predictions.** Every signal must show its source and
reasoning; never a black-box score and never fabricated/placeholder data. If a data source is
unavailable, the source records an error status (visible in the UI) rather than inventing values.

## Layout & commands

Three independent apps, no root package manager. Run each from its own directory.

### Backend (`backend/`) — FastAPI + SQLite + APScheduler, Python 3.11+
```bash
cd backend
python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements.txt  # first time (Windows)
.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000   # run API + scheduler
.venv/Scripts/python.exe -m pytest                                       # all tests
```
Imports are package-relative (`from app import db`), so **always run from `backend/`** with the
`app.main:app` module path. Tests use a `conn` fixture (`tests/conftest.py`) giving a fresh
temp-file SQLite DB per test.

### Frontend (`frontend/`) — React 19 + Vite, plain CSS Modules
Standard npm scripts (`install` / `dev` / `build` / `lint`) — see `package.json`.

**Two run modes:**
- **Single port (prod-like):** `cd frontend && npm run build`, then run uvicorn — the backend
  serves the built `frontend/dist/` on `:8000` (SPA catch-all in `app/main.py::_mount_spa`, only
  active when a build exists). Open `http://localhost:8000`. Override the dist path with
  `STOCKS_STATIC_DIR`.
- **Dev (hot reload):** run both — Vite on `:5173` proxies `/api` → `:8000` (see
  `vite.config.js`), so `src/api.js` uses a same-origin relative base (`VITE_API_BASE` defaults to
  `""`; set it only for a cross-origin backend). The backend CORS allowlist defaults to
  `http://localhost:5173` (override with `STOCKS_CORS_ORIGINS`; wildcard rejected because requests
  carry the session cookie).

**Run exactly one uvicorn worker.** The scheduler, TTL caches, rate limiter, the shared
SQLite connection and the server-sent-events bus (`app/events.py`) are all in-process; scaling
out means moving all five out of the process first.

### Extension (`extension/`) — MV3 browser extension, Chrome + Firefox
```bash
cd extension
npm install
npm run build    # -> dist-chrome/ and dist-firefox/ (three Vite passes each)
npm run test     # node --test, no browser needed
npm run lint
```
A companion to the dashboard, not a second one: ticker badges on finance/social sites, a
watchlist popup, and desktop notifications for high-severity alerts (the backend has no push
channel). It reuses the existing REST API and session cookie — **no backend changes.** All
network calls run in the background worker, because a content-script `fetch` would carry the
visited page's origin and be blocked by CORS. See `extension/README.md` for the load-unpacked
steps, the `STOCKS_CORS_ORIGINS` note, and the list of constants duplicated from `frontend/`
that must be kept in sync (notably `convictionTier`/`CHIP_META` from `BoomScorePanel.jsx`).

### Windows service (`windows/`) — NSSM, PowerShell only
```powershell
.\windows\install-service.ps1      # elevated; registers + starts SignalDashboard
.\windows\service-control.ps1 -Status    # -Start -Stop -Restart -Logs
.\windows\uninstall-service.ps1          # -PurgeData to drop the database too
.\windows\install-desktop.ps1            # npm install + Start Menu shortcut (no elevation)
```
The production run mode. Hosts the backend as a boot-start LocalSystem service so the
scheduler keeps ingesting before anyone logs in. **NSSM is the only supervisor** —
the service invokes `run_server.py --no-supervise`, which runs uvicorn *in-process* so
NSSM's Ctrl-C reaches it and the lifespan WAL checkpoint actually runs.

Data moves machine-wide to `C:\ProgramData\SignalDashboard\` (`db\`, `logs\`); the code
still runs from this working tree. Secrets come from `windows\service.env` — **nothing
loads a `.env`**, so that file is the only way they reach a LocalSystem service.
The service DB is separate from `backend\stocks.db` used by `start.ps1` (separate
accounts, separate TOTP). See `windows/README.md`, especially the note that
`STOCKS_CORS_ORIGINS[0]` doubles as the OAuth post-login redirect target
(`routes_oauth.py::_frontend_origin`) and must stay the app's own origin.

**In-app update** (Info → Updates): `app/updater.py` compares the checkout against
`origin/main` using git, with a 6-hourly scheduler job and a 1h cache. `app/routes_update.py`
serves `GET /api/update/status` to any user. `POST /api/update/apply` is admin-only and refuses
with 409 unless the checkout is on `main`, has no tracked changes, and can fast-forward. It
spawns a detached `windows/update.ps1` (pull → pip → npm build → restart, rolling back on
failure), which writes progress to `<LOG_DIR>/update-status.json`. `/api/health` carries
`commit`, which is how the UI knows the restarted server is on the new code.
`backend/app/version.py` is the version source of truth, and `frontend/package.json` mirrors
it. See `windows/README.md` → "Updating from the app".

**Installer** (`windows/setup/`): `SignalSetup.exe` is an Inno Setup *bootstrapper* built by
`.github/workflows/installer.yml`. It installs git/Node/Python machine-wide, clones `main` into
Program Files, and runs `install-service.ps1` + `install-desktop.ps1`. It stays a git checkout
on purpose, so the updater above keeps working. The desktop app runs that updater's check on
every launch (`desktop/src/updates/`).

### Desktop app (`desktop/`) — Electron shell
```bash
cd desktop
npm install
npm start        # electron .
npm run test     # node --test, no Electron needed
npm run lint
```
A window plus a tray icon (unread count, native toasts) over the running service — it
never starts the backend itself. The window loads **`http://127.0.0.1:8000`, the backend's
own origin, never `file://`**: routing is History-API paths, Vite emits absolute asset
URLs, and auth is a same-origin httpOnly cookie, so same-origin is what makes all three
work with zero frontend changes. `127.0.0.1` not `localhost` — uvicorn binds IPv4 only
and they are separate cookie jars. See `desktop/README.md`.

### Constants duplicated across apps (keep in sync)
No root package manager, so the companions copy rather than share:

| Copy | Source |
|---|---|
| `extension/…` `convictionTier`/`CHIP_META` | `frontend/src/components/BoomScorePanel.jsx` |
| `desktop/src/alerts/seen.js`, `desktop/tests/seen.test.js` | `extension/src/background/seen.js` (verbatim) |
| `"app:navigate"` in `desktop/src/preload.cjs` | `NAV_EVENT` in `frontend/src/lib/nav.js` |
| `desktop/src/tray.js` menu items | `commandItems` in `frontend/src/App.jsx` |
| poll cadence, `MAX_NOTIFICATIONS_PER_POLL` | `extension/src/background/index.js` |
| `STALE_RUN_MS`, `isApplyRunning` in `desktop/src/…` | `STALE_RUN_SECONDS`, `is_apply_running` in `backend/app/updater.py` |

## Auth & multi-tenancy

- **`app/auth.py` + `app/routes_auth.py`** — Argon2id passwords; opaque session tokens
  (SHA-256-hashed in the `sessions` table) delivered as httpOnly SameSite=Lax cookies; 2FA is
  mandatory: register → `totp_setup` session → QR enrollment (pyotp + segno SVG) → `active`;
  login → `pending_totp` → 6-digit verify → `active`. Tokens rotate on every state upgrade.
  Single-use recovery codes are stored hashed.
- An ASGI middleware in `app/main.py` resolves the cookie once per request into
  `request.state.user` and 401s everything under `/api` except `/api/health` and `/api/auth/*`.
  Routes needing the user take `Depends(auth.get_current_user)`.
- **Per-user tables**: `watchlists` (named lists, PK `id`), `watchlist` (items, PK
  `(watchlist_id, ticker)` — `user_id` is denormalized onto each row), `portfolio` (PK
  `(user_id, account, ticker)`, each row has a native `currency`), `transactions` (the ledger),
  `cash_balances`, `equity_snapshots`, `bots` / `bot_events`, `notify_profile`
  (PK `user_id`, carries `base_currency`), `alert_reads`, `fx_watch` (carousel FX pairs, PK
  `(user_id, pair)`), and the trading-bot tables: `orders`, `audit_log`, `position_rules`,
  `risk_settings`, `signal_mute_overrides`, `saved_rules` / `rule_variants` / `rule_signals`,
  `scanner_prefs` / `scanner_alert_log`, `telegram_links` / `telegram_codes`. `alerts` rows with a
  `user_id` belong to that user; `NULL` means market-wide. **Shared**: all market-data tables, `stock_analysis` (stored
  *unsized*; `analysis.apply_sizing` personalizes at read time), `app_settings` (PUT is
  admin-only). The first registered account becomes admin and claims legacy `user_id=0` rows
  (`db.claim_legacy_rows`); old single-user DBs are rebuilt in place by `init_schema`.
- Tests authenticate a `TestClient` with `tests/conftest.py::authenticate` (registers +
  enrolls TOTP via pyotp).
- Rate limiting (`app/security.py`) is a fixed-window in-memory limiter; ticker inputs are
  whitelisted by `app/validation.py::clean_ticker` before reaching outbound URLs.
- **`app/registration.py` is the only gate on account creation** (`STOCKS_REGISTRATION` =
  `open` | `invite` | `closed`, default `open`). There are exactly two account factories —
  `routes_auth.register` and the auto-create inside `routes_oauth._resolve_user` — and both
  must call it. A third creation path that forgets to is the regression to watch for.
  Deliberately no first-user bootstrap exemption: it would be a race between the operator and
  the first stranger to load the URL, and the winner gets `is_admin` plus `claim_legacy_rows`.
- `db.delete_user` sweeps every per-user table explicitly (incl. `drawings`, `drawing_drafts`, `fx_watch`, the ledger and the bots). Only `sessions`,
  `recovery_codes` and `oauth_identities` declare `ON DELETE CASCADE`, so deleting the `users`
  row alone strands the rest under an id SQLite may reissue. Keep `_PER_USER_TABLES` in step
  with the schema.

## Public exposure

The app can be served on the internet through a tunnel (Tailscale Funnel — see
`windows/README.md`). Three things that are load-bearing:

- **The bind never changes.** The tunnel dials `127.0.0.1` from this machine; `0.0.0.0` would
  only add LAN exposure. `install-service.ps1 -PublicOrigin` derives every env var that has to
  agree with the public URL, including the `CORS_ORIGINS[0]` ordering rule above.
- **`security.py::_client_ip` trusts `X-Forwarded-For` only from `config.TRUSTED_PROXY_IPS`.**
  Behind a tunnel every socket peer is loopback, so keying rate limits on the peer alone puts
  the whole internet in one bucket; trusting the header unconditionally lets anyone forge a
  private one. uvicorn's own `ProxyHeadersMiddleware` (on by default, `forwarded_allow_ips`
  `127.0.0.1`) is a second layer that composes — `run_server.py` passes it the same list so
  the two agree on one boundary. Because it may already have rewritten `request.client`, a
  peer that is *not* trusted is treated as the real client and the header is never re-parsed.
- **HSTS is only sent on requests that really arrived over HTTPS** (`_is_https`). An
  unconditional header would also go out on `http://localhost:8000` and pin that origin to
  HTTPS in the browser profile permanently, breaking dev in a way that is very hard to
  diagnose. Never add `preload`: `ts.net` is Tailscale's domain, not ours.

## On-demand analysis & search (any ticker)

- `GET /api/search?q=` — Yahoo keyless search (`app/search.py`, TTL-cached), surfaced in the
  frontend Cmd/Ctrl+K palette.
- `GET /api/analyze/{ticker}` — `app/analyze.py`: stored fast-path for holdings, otherwise a
  live 2y-bars build (TTL-cached, **never persisted** — `stock_analysis` stays portfolio-only).
  Also powers the HTML report fallback for never-watched tickers and serves the seasonality
  anchors ("this day 1/2/5/max years ago", `seasonality.compute_anchors`, stored in
  `anchors_json`).

## Backend architecture

The whole pipeline hangs off the **`SOURCES` registry** in `app/main.py`:
`name -> SourceSpec(fetch, store, min_interval, retry_interval, force_on_daily)`. The cadence
fields only **seed** each source's row in `source_schedules`; after that the row (editable on
the Server page) is what runs.

- **`app/sources/<name>.py`** — one isolated module per source exposing `fetch(...) -> list[Model]`.
  Network-free parsing helpers are kept pure and unit-tested directly; `fetch` does the throttled
  HTTP. Sources never write to the DB themselves. A fetch that must wait (a rate-limit cooldown)
  raises `ingest.SourceDeferred(reason, retry_after_seconds)` — GDELT's 429 does.
- **`app/ingest.run_source`** — the only orchestrator. Calls `fetch()`, passes results to the
  `store_fn`, stamps source status and appends a `source_runs` row, returning a `RunResult`.
  **It never raises and never gates**: outcomes are `ok`, `error` (brief status + full traceback on
  both the status row and the run row) or `deferred` (reason + `next_attempt_at`). There is no
  `skipped` outcome any more (only on legacy rows).
- **`app/schedules.py`** — pure schedule logic: validation, APScheduler triggers (interval, with an
  optional weekday filter, or HH:MM times on days in a tz) and `next_due` (never run → now; ok →
  one interval/next slot after the last success; error → last attempt + retry; deferred → the
  source's own next attempt; anything past → now, so a missed run happens late, never not at all).
- **`app/db.py`** — the *single* place any SQLite access lives. `init_schema` is idempotent
  (`CREATE TABLE IF NOT EXISTS` + `_try_add_column` for additive migrations). One shared connection
  (`check_same_thread=False`, `Row` factory) is created at import time in `main.py`.
- **`app/models.py`** — Pydantic models that are the common schema between sources, DB, and API.
- **`app/main.py`** — FastAPI routes (mostly thin `db.get_* -> model_dump()` reads) plus the
  scheduler wiring in `lifespan`: **one APScheduler job per source** (`src:<name>`) plus `src:derived`,
  `daily_analysis`, `daily_digest` and `prune_history`, all on the single-thread `refresh` executor.

**Scheduling rules (task 9: nothing is silently skipped):**
- Every job runs on one serial thread (`_refresh_executor`, shared with manual `/api/refresh` and
  run-now), so `refresh_conn` never has two writers. A job due while another runs **queues and runs
  late** (`misfire_grace_time=None`); a fire while its own previous run is still queued/running is
  recorded as `coalesced` (`max_instances=1`). The Server page lists what is queued.
- After each run its outcome re-arms its own job via `schedules.next_due` (`_after_run`); errors and
  deferrals get that time written onto their run row as `next_attempt_at`.
- First runs after startup/edits are held back `SCHEDULER_STARTUP_DELAY_SECONDS` (staggered 1s per
  source, registry order) — also what keeps `TestClient` lifespans from firing real fetches.
- Shutdown pauses the scheduler and drains the pool **before** `scheduler.shutdown()`: that call
  holds the job-store lock while waiting for executors, and a finishing run needs that lock to
  re-arm itself — draining inside it deadlocks.
- A non-forced manual refresh of a source with a registry `min_interval` that is still fresh
  answers `queued: false` with the reason and next run (no run row); `force=1` is admin-only.

**Derived step:** `boom_score` (a pure DB computation that reads every other source), `alerts`
(diffs the fresh boom scores against the prior `alert_state` snapshot to fire transition events
exactly once, deduped by `dedup_key`), `regime` (today's risk-on / neutral / risk-off label),
`automute` (per-signal hit rates, mute with hysteresis) and `rules` (users' saved rules on
completed bars) run together, in that order, as the `derived` job (`schedules.DERIVED_MEMBERS`).
It has its own schedule row and is pulled forward to `DERIVED_DEBOUNCE_SECONDS` after any
upstream success. Sources nothing derived reads set `requests_derived=False`.

**Boom Score** (`app/sources/boom_score.py`) combines all signals into a weighted `-90…+100`
composite per watchlist ticker. `WEIGHTS` defines each component's contribution; congress weight is
scaled by trade amount and time-decayed. Component booleans are persisted so the UI can explain the
score, and each run also appends to boom-score history.

**Ledger** (`app/ledger.py`) — the *only* write path for positions and cash. Every add, edit
(an `ADJUST`), removal (`ADJUST` to 0), sale, deposit and withdrawal is a `transactions` row;
`portfolio` and `cash_balances` are materialized from it by `db.record_transaction` in one
`BEGIN IMMEDIATE` transaction (two connections write, so read-modify-write takes the lock first).
Accounts: `manual` (hand-tracked), `paper` (the paper broker), `live` (refused: no broker), and
`bot:<id>`. The pure arithmetic lives in `ledger.apply`, and `ledger.replay` must always
reproduce the materialized tables (tested). Each row also stores the `flow` it caused, the
external capital in/out that time-weighted returns need. `equity` (the `equity_snapshot` source,
seeded at 16:15 New York on weekdays) values every account in its owner's base currency and
skips an account rather than writing a partial value when a price or FX rate is missing.

**Demo bots** (`app/bots.py`) — preset strategies (`STRATEGIES`: follow / trend / dip /
breakout) trading simulated fills in their own `bot:<id>` ledger account. Creating a bot moves
its allocation out of manual cash, and stopping it closes the positions and hands the cash back,
each atomically (`db.create_bot`, `db.stop_bot`). `bots.decide` is pure and reads **completed
daily closes only** (no look-ahead, at most one change of mind per session). Stops, targets
and fills use the live quote via `quotes.tradable_price` (LIVE and no older than
`ENGINE_QUOTE_MAX_AGE_SECONDS`), moved against the bot by `costs.apply` at `BOT_COST_BPS`. A
fifth strategy, `rule`, trades a saved rule. The engine is the 1-minute
`bots` job, a member of `_QUIET_JOBS` (only its errors reach `job_runs`). It trades a ticker
only while that ticker's exchange is LIVE, and a fill (`db.bot_trade`) re-checks inside its
transaction that the bot is still active.

**Trading & risk** — no broker exists, so nothing here can place a real order.
- **`execution.submit` is the only way an order happens**, paper and bot alike. It runs the
  `CHECKS` that `app/guardrails.py` installs (imported at the end of `execution.py`): kill switch,
  market hours, entry window, per-order / per-day notional, orders per day, position % of the
  account, and the per-trade limits (loss at the plan stop, daily loss at stops, min reward:risk,
  require a stop). **An exit — a sell of no more than is held — skips every check, the kill
  switch included**: getting out is never blocked. Orders carry `plan_stop` / `plan_target`. Then it
  writes the `orders` row and the ledger, audits the order, publishes an `order` event and (for bots)
  queues a Telegram message. Bots pass `fill=` so `db.bot_trade` keeps its in-transaction
  "still active" re-check. A new order path that skips `submit` is the regression to watch for.
- **`costs.apply`** is the one cost model (suggestion net returns, paper and bot fills, the bar
  backtester). `model_version.current()` hashes the weights and thresholds, so the track record
  never mixes versions. A saved rule's version is `rule:<hash>`.
- **Risk switches** (`risk_settings`): drawdown kill switch (after each equity snapshot), regime
  gate (off / downgrade / pause), and a global kill switch whose release needs a fresh TOTP
  (`auth.verify_step_up`). All three block **new entries** only; exits and stops keep running.
- **`audit_log`** is one hash chain per user (so `delete_user` can drop a chain whole).
  Verification is admin-only on the Server page.
- **Rules** (`app/rules.py`) take price/volume indicators only. Anything stored only as a latest
  snapshot (fundamentals, analyst, congress, Boom Score…) is refused as not point-in-time.
  `bar_backtest` fills at the next open and stops intrabar, and shows no metrics below 20 trades.
- **Scanner** (`app/scanner.py`): three sources (`universe` monthly from Wikipedia,
  `scan_history` daily bars, `scanner` batched Yahoo v7 quotes). While every market in the
  universe is closed it defers instead of calling out. The scans are rule-schema groups.
- **Push**: `GET /api/stream` (server-sent events from `app/events.py`). The frontend opens it only
  on the Trading Bot page and only while the tab is visible (`lib/eventStream.js`). Browsers allow
  ~6 HTTP/1.1 connections per origin and every stock page is its own tab. `run_server.py` bounds
  graceful shutdown to 5s so an open stream can't block the WAL checkpoint.
- **Alpaca** (`app/alpaca.py`, account `alpaca`): paper only. Live is refused in `submit` until
  STC-51's per-order TOTP exists. Keys in env, sent in headers, never in errors. One account per
  instance, owned by `STOCKS_ALPACA_OWNER` (default the first admin). `AlpacaBroker` in
  `execution.py` places orders, `sync_broker` (the minute job) applies fills by cumulative
  `filled_qty` and records bracket legs as `source="bracket"` orders. A reconcile records
  position/cash differences as labelled ledger rows. Buys are cash-only (`guardrails.cash_only`); the
  kill switch cancels open Alpaca orders but leaves bracket exit legs.
- **Telegram** (`app/telegram.py`, token `STOCKS_TELEGRAM_BOT_TOKEN`): the token is in every
  request URL, so errors are re-raised without the URL. Never log or store a raw httpx error from it.

### Adding a new data source
Step-by-step checklist lives in the `adding-a-data-source` skill
(`.claude/skills/adding-a-data-source/SKILL.md`) — it loads on demand rather than
sitting in context every session.

## Frontend architecture

- **`src/hooks/useDashboardData.js`** owns *all* dashboard state: loads every endpoint in parallel
  on mount, auto-polls every 3 min, and exposes `refresh()` which POSTs `/api/refresh/<source>` for
  each `EXTERNAL_SOURCES` entry (via `Promise.allSettled`, so partial failures are fine) then reloads.
- **Routing is real History API paths** (`src/lib/nav.js`), hand-rolled rather than react-router:
  a flat list of views plus one parameterised route (`/stock/<TICKER>`). `view` is *derived* from
  the URL in `App.jsx`, not state; `VIEWS` maps it to a panel and `TITLES` (`src/lib/routes.js`)
  maps it to a path segment and page title. Each section is a `*Panel` component in
  `src/components/`, paired with a co-located `.module.css`.
- `/stock/<TICKER>` normally opens in a **new tab** (`openTickerTab`); in-app navigation to it
  carries `?from=<view>`. That param is what `leaveStock` uses to decide whether Back returns to
  a view or closes the tab — and it is why `openTickerTab` must not pass `noopener`, which
  forfeits a tab's right to close itself.
- Navigation items live in `src/lib/navItems.js`, shared by the desktop rail (`Sidebar`) and the
  phone tab bar (`MobileNav`, ≤640px: the five core views plus a "More" sheet). On a phone the
  TopBar is one row, and refresh / theme / dyslexia / tour move into `UserMenu` `phoneItems`.
  Breakpoints and the `(pointer: coarse)` → `--tap` (40px) rule are documented in DESIGN.md.
- The **Trading Bot page** (`TradingBotPanel`) is a tab host. Every bot/risk/paper/rules/scanner
  /audit UI lives in a `*Tab` there, deep-linked with `?tab=` (`navigateTo({kind: "view", view:
  "trading-bot", tab})`). Other pages only get small entry points that open the same shared
  component (e.g. `PaperTradeButton`, an exit-rule `Dialog`).
- Shared primitives for new UI: `Dialog` (portaled, focus-trapped, a bottom sheet on phones),
  `ConfirmDialog`, `Field` (label + hint + field error), `Button`, and `ScrollHint` (edge fade on
  sideways-scrolling tables). `ApiError.fields` carries the backend's field-level errors
  (`detail = {message, errors}`).
- Styling is **CSS Modules + design tokens** defined in `src/index.css` (`:root` custom properties
  for spacing/radius/typography; the "Iris Dusk" dark theme). No CSS framework. Accessibility is a
  first-class concern: an always-on ADHD-friendly type scale and an opt-in dyslexia mode
  (self-hosted Atkinson Hyperlegible font, toggled via `useSettings`).

## Configuration

All backend config is env-var driven with `STOCKS_` prefixes and sane defaults in `app/config.py`
(DB path, refresh interval, per-source lookbacks/limits, news query, SEC user-agent). **Secrets stay
in env, never in code/DB** — SMTP (email digest), Twilio (SMS), and the optional Alpha Vantage key.
Notification channels safely no-op and log when their env vars are unset, so the app runs fully
without any of them.

**Nothing loads a `.env` file.** `config.py` reads `os.environ` once at import; there is no
`python-dotenv` and no `load_dotenv` anywhere. `backend/.env.example` is documentation — you
must export the variables in your shell, or (for the Windows service, which inherits nothing
from the installing user) put them in `windows/service.env`, which the installer bakes into the
service's environment block.

Path defaults are `__file__`-relative, not cwd-relative — `DB_PATH` → `backend/stocks.db`,
`LOG_DIR` → `backend/logs/`, the SPA dist → `frontend/dist/`. That matters because a Windows
service starts in `C:\Windows\system32`, where a relative DB path would silently create a
second, empty database and the app would come up looking healthy with no data.
