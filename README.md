# Stock Signal Dashboard

A self-hosted, multi-user dashboard that aggregates **public** market signals into explainable, per-ticker reads — built for self-directed retail investors who want to act deliberately instead of impulsively.

## Overview

The signals that say "something is happening with this stock" are scattered across dozens of public sources: SEC Form 4 insider filings, federal contract awards, congressional trade disclosures, news, technicals, short interest, social chatter, analyst ratings, fundamentals, and market-wide sentiment. This project pulls them all on a schedule, stores them in one place, and surfaces them per ticker with the source and reasoning attached.

The core principle is **signals, not predictions**. Every number shows where it came from and why it matters. Nothing is a black-box score, and nothing is ever fabricated or filled with placeholders: when a source is unavailable, it records an error status that the UI shows as-is.

Architecturally it is a **FastAPI + SQLite + APScheduler** backend that ingests every source on its own schedule, runs a derived scoring and alerting step, and serves a **React 19 + Vite** single-page app. The same REST API also feeds an optional MV3 browser extension and an Electron desktop shell, and on Windows the backend runs as a boot-start service so data keeps flowing while nobody is logged in.

## Key features

- **20+ public data sources** — USAspending contracts, SEC EDGAR Form 4, congressional trades, GDELT news, technicals, short interest, social sentiment, analyst ratings, fundamentals, earnings, OHLC, seasonality, X posts, and market sentiment (Fear & Greed, VIX, AAII, put/call, margin debt, yield curve, economic calendar). Each source's health, last run and full error traceback are visible on the Server page.
- **Boom Score** — a weighted `-90…+100` composite per watched ticker. Every contributing component is stored, so the UI can explain the score, and each run is kept as history.
- **Alerts and digests** — transition-based alerts that fire exactly once, plus an optional pre-market email/SMS digest.
- **Analysis for any ticker** — a Cmd/Ctrl+K search palette and on-demand analysis (2-year bars, seasonality anchors, HTML report), including for tickers you don't watch.
- **Portfolio and watchlists** — multiple named watchlists and a multi-currency portfolio with position sizing, all per user. Every trade, correction and cash movement is a row in a transaction ledger, with cash balances, recorded sales (realized P&L) and a daily equity snapshot.
- **Trading Bot page** — everything bot-related, in tabs (no real orders are ever placed; there is no broker connection):
  - **Bots**: preset strategies (Follow Suggestions, Trend, Dip-buy, Breakout) plus a *Custom rule* bot that trades one of your saved rules, on simulated fills. Each bot is funded from your portfolio cash and hands it back with its P&L when stopped.
  - **Risk**: stop / take-profit / trailing exit rules per holding, a drawdown kill switch, the market regime gate, and signal auto-mute.
  - **Paper**: a paper account with market, limit and stop orders, an order ticket with a review step, and slippage tracking.
  - **Performance**: time-weighted returns vs a benchmark, drawdown, Sharpe/Sortino, beta, exposure and correlations for any account.
  - **Rules**: build price/volume rules and backtest them over full adjusted history. Results are split into in-sample and out-of-sample, every variant you test is counted, and no metrics are shown below 20 trades. Saved rules signal daily.
  - **Scanner**: a market-wide intraday scanner (unusual volume, gaps, 52-week highs/lows, breakouts, RSI extremes) over a configurable universe, pushed live over server-sent events, with opt-in, throttled alerts.
  - **Audit**: a per-user, hash-chained audit log, plus a global kill switch that needs a fresh TOTP to release.
- **Alpaca paper broker** — a Broker tab sends orders to your Alpaca paper account through the same guardrails, kill switch and audit log; fills sync back every minute and positions and cash are reconciled. Live trading stays off.
- **Telegram** — link a private chat in Settings to get high-severity alerts and bot fills, and ask the bot `/status`, `/bots`, `/watchlist` or `/alerts` (read-only).
- **Works on a phone** — a bottom tab bar, one-row top bar, stacked portfolio cards and 40px touch targets below 640px.
- **Charts** — candlestick charts with extended hours, drawing tools and saved drafts.
- **Multi-user with mandatory 2FA** — Argon2id passwords, TOTP (Google/Microsoft Authenticator), recovery codes, optional OAuth sign-in. Market data is shared; watchlists, portfolio and notifications are per user.
- **Operations built in** — editable per-source schedules, run-now, a queue view, and a one-click in-app updater (admin only) with rollback on failure.
- **Companions** — a browser extension that adds ticker badges and desktop alerts, an Electron tray app that checks for updates on every launch, and a one-click Windows installer (`SignalSetup.exe`).

## Architecture

```
                         ┌──────────────────────── backend (one uvicorn worker) ───────────────────────┐
 public sources          │                                                                             │
 (SEC, USAspending,  ──► │ app/sources/<name>.py ──► ingest.run_source ──► app/db.py ──► SQLite (WAL)  │
  GDELT, Yahoo, …)       │   fetch() -> [Model]       store + status          ▲             │          │
                         │        ▲                   + source_runs row       │             ▼          │
                         │        │                                           │   derived step:        │
                         │  APScheduler: one job per source (src:<name>),     │   boom_score -> alerts │
                         │  plus derived / daily_analysis / daily_digest /    │             │          │
                         │  prune_history — all on ONE serial thread          │             ▼          │
                         │                                                    │   analysis / analyze / │
                         │                                                    └── suggestions / notify │
                         │                                                                             │
                         │ FastAPI  /api/*  (auth middleware, rate limiting)  +  SPA from frontend/dist │
                         └──────────────────────────────────────┬──────────────────────────────────────┘
                                                                │ same-origin cookie session
                         ┌──────────────────────────────────────┼────────────────────────────┐
                         ▼                                      ▼                            ▼
                React SPA (frontend/)              Browser extension (extension/)   Electron shell (desktop/)
```

- **Frontend** (`frontend/`) — React 19 SPA. `src/hooks/useDashboardData.js` owns dashboard state: it loads every endpoint in parallel, polls every 3 minutes, and triggers source refreshes. Routing uses real History API paths (`src/lib/nav.js`), with one panel component per view.
- **Backend** (`backend/app/`) — FastAPI routes are mostly thin `db.get_* → model_dump()` reads. The `SOURCES` registry in `app/main.py` drives the whole ingestion pipeline.
- **Database** — a single SQLite file in WAL mode. All SQL lives in `app/db.py`. Scheduler jobs write through a separate refresh connection, so ingestion never blocks dashboard reads.
- **Analysis engine** — the **derived step** (`sources/boom_score.py`, then `alerts.py`) runs after any upstream source succeeds. `analysis.py` builds the stored per-holding analysis, `analyze.py` does live analysis for any ticker, and `suggestions.py`, `sentiment.py` and `backtest.py` build the higher-level reads.
- **Data flow** — the scheduler fires, the source fetches, `ingest.run_source` stores the results and stamps status, and the job re-arms itself. The derived step then recomputes scores and alerts, the API serves them, and the UI renders them with their source and freshness.

> **Run exactly one uvicorn worker.** The scheduler, TTL caches, rate limiter and SQLite connections are all in-process.

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI 0.115, uvicorn, Pydantic 2, httpx, APScheduler 3.11, xlrd / openpyxl (spreadsheet sources) |
| Auth | argon2-cffi (Argon2id), pyotp (TOTP), segno (QR codes), OAuth (Google / GitHub / Facebook) |
| Database | SQLite (WAL mode, stdlib `sqlite3`) |
| Optional backend | psutil (Server page metrics), Playwright + Chromium (margin-debt fetch) |
| Frontend | React 19, Vite 8, CSS Modules with design tokens, lightweight-charts, Recharts, Motion, anime.js |
| Extension | Manifest V3, Vite build for Chrome and Firefox |
| Desktop | Electron 34 |
| Service | Windows service via NSSM, PowerShell scripts |
| Installer | Inno Setup bootstrapper, built by GitHub Actions |
| Testing / lint | pytest, ESLint, `node --test` |

## Prerequisites

- **OS** — Windows 10/11 is the primary target: the launcher, service and updater are PowerShell. The backend and frontend also run on Linux or macOS using the manual commands below.
- **Python 3.11+** on `PATH`.
- **Node.js 20.19+ or 22.12+** (required by Vite 8) with npm.
- **git** — needed for the in-app updater, which compares your checkout with `origin/main`.
- **Network** — outbound HTTPS to the public data sources (sec.gov, usaspending.gov, GDELT, Yahoo Finance, FINRA and others). No inbound access is needed unless you deliberately expose the app (see [Security considerations](#security-considerations)).
- **Permissions** — a normal user is enough for development. Installing the Windows service needs an **elevated** PowerShell.
- **No database server and no Docker** — SQLite is embedded.
- **Optional: `pdftotext`** (poppler) for reading House congress trade filings. On a Raspberry Pi or
  Debian it's `sudo apt install poppler-utils`; on Windows, install poppler and point
  `STOCKS_PDFTOTEXT` at `pdftotext.exe` if it isn't on the service's `PATH`. Without it the filings
  are listed but not read, and the Server page says so.

## Installation

**Windows installer.** Download
[`SignalSetup.exe`](https://github.com/Minka1902/stock-dashboard/releases/latest/download/SignalSetup.exe)
and run it (one UAC prompt). It installs git, Node.js and Python machine-wide through winget if they are
missing, clones `main` into `C:\Program Files\SignalDashboard\repo`, installs the dependencies, registers the
Windows service and adds a **Signal** Start Menu shortcut. Every launch of the app then checks GitHub for
updates and offers to apply them. The exe is unsigned, so SmartScreen asks first: *More info → Run anyway*.
Details in [`windows/README.md`](windows/README.md#one-click-installer).

**From source.**

```bash
git clone https://github.com/Minka1902/stock-dashboard.git
cd stock-dashboard
```

**Windows, one command.** This creates `backend/.venv`, then installs the backend and frontend dependencies:

```powershell
.\start.bat -Install
```

**Manual (any OS).**

```bash
# Backend
cd backend
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
# .venv/bin/python -m pip install -r requirements.txt         # Linux / macOS

# Optional: margin-debt headless fetch (only that source fails without it)
.venv/Scripts/python.exe -m playwright install chromium

# Frontend
cd ../frontend
npm install
```

Before the first run, set `STOCKS_SEC_USER_AGENT` to your own name and email. SEC EDGAR requires a real contact user agent (see [Configuration](#configuration)).

## Running the project

| Mode | Command | UI |
|---|---|---|
| Dev (hot reload) | `.\start.bat` (or `start.ps1 dev`) | http://localhost:5173 |
| Single port (prod-like) | `.\start.bat prod` | http://localhost:8000 |
| Windows service | `.\windows\install-service.ps1` (elevated) | http://127.0.0.1:8000 |

The launcher also supports `-Stop`, `-Status`, `-Logs`, `-Kill`, `-NoBrowser` and `-NoSupervise`. See the header of `start.ps1`.

**Manual dev** needs two terminals. Vite on `:5173` proxies `/api` to `:8000`:

```bash
cd backend  && .venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev
```

**Manual single port** — build the SPA once. The backend then serves `frontend/dist/` alongside the API:

```bash
cd frontend && npm run build
cd ../backend && .venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
```

Always run uvicorn from `backend/` with the `app.main:app` module path, because imports are package-relative.

**First login.** The first account you register becomes **admin** and must enroll TOTP (scan the QR code in an authenticator app). Store the recovery codes it shows you.

**Companions:**
- Browser extension — `cd extension && npm install && npm run build`, then load `dist-chrome/` or `dist-firefox/`. See [`extension/README.md`](extension/README.md).
- Desktop app — `.\windows\install-desktop.ps1`, then `cd desktop && npm start`. It needs the service running. See [`desktop/README.md`](desktop/README.md).
- Windows service — see [`windows/README.md`](windows/README.md) for NSSM verification, logs, updates and public exposure.

## Configuration

All backend configuration comes from `STOCKS_*` environment variables with defaults in [`backend/app/config.py`](backend/app/config.py), which is the full list.

- **Nothing loads a `.env` file.** There is no `python-dotenv`. [`backend/.env.example`](backend/.env.example) is documentation only, so export the variables in your shell.
- **Windows service:** put variables in `windows/service.env` (copy from `service.env.example`; the file is gitignored). The installer bakes it into the service's environment, and this is the only way a LocalSystem service receives them.
- **Secrets stay in the environment**, never in code or the database. Notification channels and optional sources quietly no-op (and log it) when their variables are unset.

| Variable | Default | Purpose |
|---|---|---|
| `STOCKS_DB_PATH` | `backend/stocks.db` | SQLite file |
| `STOCKS_LOG_DIR` | `backend/logs/` | Rotating log directory |
| `STOCKS_LOG_LEVEL` | `INFO` | Log level |
| `STOCKS_STATIC_DIR` | `frontend/dist/` | Built SPA served on `:8000` |
| `STOCKS_CORS_ORIGINS` | `http://localhost:5173` | Allowed origins, comma-separated. `*` is rejected. The first entry is also the OAuth post-login redirect |
| `STOCKS_REFRESH_SECONDS` | `180` | Default source cadence |
| `STOCKS_SCHEDULE_TZ` | `Asia/Jerusalem` | Default timezone for time-of-day schedules |
| `STOCKS_SEC_USER_AGENT` | *(maintainer's contact)* | Contact user agent required by SEC EDGAR — set your own |
| `STOCKS_REGISTRATION` | `open` | `open` \| `invite` \| `closed` |
| `STOCKS_INVITE_CODE` | — | Required when registration is `invite` |
| `STOCKS_COOKIE_SECURE` | `0` | Set to `1` when served over HTTPS |
| `STOCKS_TRUSTED_PROXY_IPS` | `127.0.0.1,::1` | Peers whose `X-Forwarded-For` header is trusted |
| `STOCKS_HSTS_SECONDS` | `0` | HSTS max-age, sent only on real HTTPS requests |
| `STOCKS_SMTP_*` | — | Email digest (host, port, user, password, from, STARTTLS) |
| `STOCKS_TWILIO_*` | — | SMS digest |
| `STOCKS_DIGEST_HOUR` / `_MINUTE` / `_TZ` | `7` / `30` / `America/New_York` | Pre-market digest time |
| `STOCKS_OAUTH_{GOOGLE,GITHUB,FACEBOOK}_CLIENT_ID` / `_SECRET` | — | Optional social login |
| `STOCKS_OAUTH_REDIRECT_BASE` | `http://localhost:8000` | Base of the OAuth callback URL registered with each provider |
| `STOCKS_UPDATE_MODE` | *(auto-detected)* | `service` \| `dev` — overrides the updater's detection of whether it runs under the NSSM service |
| `STOCKS_ALPHA_VANTAGE_KEY`, `STOCKS_FMP_KEY`, `STOCKS_X_BEARER` | — | Optional API keys |
| `STOCKS_BENCHMARKS` | `SPY,^TA125.TA` | Benchmark series the `ohlc` source always fetches |
| `STOCKS_COST_BPS_PER_SIDE` | `10` | The one cost model (basis points each side) for net returns, paper and bot fills, and the backtester |
| `STOCKS_BOT_COST_BPS` | `10` | Bot-only override of the cost per side (defaults to the above) |
| `STOCKS_BOT_MAX_BOTS` | `10` | Bots per user that aren't stopped |
| `STOCKS_BOT_INTERVAL_SECONDS` | `60` | Demo-bot engine cadence |
| `STOCKS_PAPER_START_CASH` / `_CURRENCY` | `100000` / `USD` | What "Fund / Reset" puts in the paper account |
| `STOCKS_SCANNER_UNIVERSES` | `majors,mine` | Scanner universe: a union of `majors`, `mine` (held + watched), `sp500`, `ndx100`, `ta125` |
| `STOCKS_SCANNER_MAX_TICKERS` / `_INTERVAL_SECONDS` | `600` / `300` | Scanner cap and cadence (it defers while every market in the universe is closed) |
| `STOCKS_SCANNER_ALERTS_PER_HOUR` | `3` | Default hourly cap for users who opt in to scanner alerts |
| `STOCKS_STREAM_MAX_SECONDS` | `300` | Lifetime of one `/api/stream` connection before the browser reconnects |
| `STOCKS_TELEGRAM_BOT_TOKEN` | — | Telegram bot token from @BotFather (secret); Telegram is off while unset |
| `STOCKS_ALPACA_KEY_ID` / `_SECRET_KEY` | — | Alpaca paper keys (secret); the Broker tab is off while unset |
| `STOCKS_ALPACA_OWNER` | *(first admin)* | Email of the one user the Alpaca account belongs to |

The regime thresholds (`STOCKS_REGIME_*`), auto-mute (`STOCKS_SIGNAL_*`), backtest history (`STOCKS_DAILY_MAX_*`) and the scanner's finer settings are listed with their defaults in [`backend/.env.example`](backend/.env.example).

**Ports:** the backend uses `8000` and the Vite dev server uses `5173`. Both can be changed with `start.ps1 -ApiPort` / `-WebPort`. In dev, `VITE_API_BASE` stays empty (same-origin through the proxy); set it only if you run a cross-origin backend.

**Paths** default relative to the source file, not the working directory. A Windows service starts in `C:\Windows\system32`, and a relative path there would silently create a second, empty database. The service stores its data in `C:\ProgramData\SignalDashboard\` (`db\`, `logs\`), separate from `backend\stocks.db`.

Per-source schedules are **not** environment variables. They are seeded from the registry and then edited on the Server page (the `source_schedules` table).

## Project structure

```
stock-dashboard/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app, SOURCES registry, scheduler wiring, SPA mount
│   │   ├── sources/           # one module per data source: fetch() -> list[Model]
│   │   ├── ingest.py          # run_source: the only ingestion orchestrator
│   │   ├── schedules.py       # pure schedule logic (triggers, next_due)
│   │   ├── db.py              # ALL SQLite access, schema + migrations
│   │   ├── models.py          # Pydantic models shared by sources, DB and API
│   │   ├── analysis.py, analyze.py, suggestions.py, suggestion_history.py, sentiment.py, backtest.py, alerts.py
│   │   ├── search.py, quotes.py, chart_data.py, currency.py, market_calendar.py, report.py, themes.py
│   │   ├── auth.py, routes_auth.py, routes_oauth.py, registration.py, security.py, validation.py
│   │   ├── routes_chart.py, routes_update.py, updater.py, notify.py, config.py, version.py
│   │   └── data/              # static reference data (contractor/major lists)
│   ├── tests/                 # pytest suite (conftest: conn fixture, authenticate helper)
│   ├── requirements.txt
│   └── run_server.py          # service entry point (in-process uvicorn)
├── frontend/
│   └── src/
│       ├── components/        # *Panel views + shared components, each with a .module.css
│       ├── hooks/             # useDashboardData, useAuth, useSettings, …
│       ├── lib/               # nav/routes, formatting, chart and drawing helpers
│       ├── context/           # SettingsContext
│       ├── api.js             # REST client
│       └── index.css          # design tokens and themes
├── extension/                 # MV3 browser companion (Chrome + Firefox)
├── desktop/                   # Electron window + tray over the running service (src/updates/: launch-time update check)
├── windows/                   # NSSM service install/control/update scripts, Common.ps1 (NSSM pin), service.env.example
│   └── setup/                 # SignalSetup.iss + bootstrap.ps1 (the one-click installer)
├── reports/                   # QA logs and release reports
├── .github/workflows/         # installer.yml: builds SignalSetup.exe
├── .claude/skills/            # repo checklists (e.g. adding-a-data-source)
├── start.ps1 / start.bat / stop.bat   # dev/prod launcher
├── CLAUDE.md                  # in-depth architecture notes
├── PRODUCT.md / DESIGN.md     # product principles and design system
├── fixes_plan_782026.md       # fix & feature plan (trading methodology + fixes)
└── LICENSE
```

## Core workflows

1. **Ingest.** A source's scheduler job (`src:<name>`) fires. `ingest.run_source` calls the source's `fetch()`, passes the results to its store function, stamps `source_status`, and appends a `source_runs` row. The outcome is `ok`, `error` (with the full traceback) or `deferred` (when a rate-limit cooldown applies), and it re-arms the job's next run. It never raises and never silently skips: a job that comes due while another is running queues and runs late.
2. **Derive.** After any upstream success, the `derived` job is pulled forward. `boom_score` recomputes the composite for every watched ticker, then `alerts` compares the new scores against the previous `alert_state` snapshot and fires each transition exactly once (deduped by `dedup_key`).
3. **Analyze and notify.** `daily_analysis` refreshes the stored analysis for holdings, and `daily_digest` sends the pre-market email/SMS through each user's notification profile.
4. **Look up any ticker.** Cmd/Ctrl+K calls `/api/search`, and `/api/analyze/{ticker}` returns stored analysis for holdings or builds a live one for other tickers (TTL-cached and **never persisted**).
5. **Browse and report.** Each view (sentiment, suggestions, news, trades, portfolio, server and others) is a panel. `/stock/<TICKER>` opens a per-ticker page in a new tab, and `/api/analysis/{ticker}/report` renders an HTML report.
6. **Onboard a user.** Register → enroll TOTP → verify → build watchlists and portfolio → set the notification profile.

## Database model

A single SQLite database, created and migrated by `db.init_schema`. The function is idempotent: it uses `CREATE TABLE IF NOT EXISTS` plus `_try_add_column` for additive migrations.

| Group | Tables | Responsibility |
|---|---|---|
| Auth | `users`, `sessions`, `recovery_codes`, `oauth_identities` | Accounts, hashed session tokens, single-use recovery codes, linked OAuth identities |
| Per user | `watchlists`, `watchlist`, `portfolio`, `transactions`, `cash_balances`, `equity_snapshots`, `bots`, `bot_events`, `notify_profile`, `alert_reads`, `fx_watch`, `drawings`, `drawing_drafts` | Named watchlists and their items, holdings per account (materialized from the `transactions` ledger), cash, daily equity, demo bots and their decision log, notification and base-currency settings, read receipts, FX carousel, chart drawings |
| Shared market data | `contracts`, `news`, `insider_trades`, `congress_trades`, `technical_signals`, `short_interest`, `social_sentiment`, `analyst_signals`, `fundamentals`, `company_holders`, `earnings`, `earnings_events`, `econ_events`, `ohlc_series`, `seasonality`, `x_posts`, `yield_curve`, `fear_greed`, `vix_daily`, `aaii_sentiment`, `put_call`, `margin_debt` | One table per source, written only through `upsert_*` helpers |
| Derived | `boom_scores`, `boom_score_history`, `alerts`, `alert_state`, `stock_analysis`, `suggestion_history`, `suggestion_log` | Scores, alert state, and analysis. `stock_analysis` is stored unsized and personalized at read time |
| Operations | `source_status`, `source_runs`, `source_schedules`, `job_runs`, `app_settings`, `data_migrations` | Source health, run history, editable schedules, job log, admin settings, one-off data migrations |

`db.delete_user` explicitly sweeps every per-user table (`_PER_USER_TABLES`), because only `sessions`, `recovery_codes` and `oauth_identities` cascade. Keep that list in step with the schema.

## Plugin system (data sources)

Data sources are the extension point. Each one is an isolated module that the `SOURCES` registry in `app/main.py` wires into the pipeline:

```python
SOURCES = { name: SourceSpec(fetch, store, min_interval, retry_interval, force_on_daily) }
```

- **Module contract** — `app/sources/<name>.py` exposes `fetch(...) -> list[Model]`. Pure parsing helpers stay separate from the throttled HTTP so they can be unit-tested without the network. A source **never writes to the database itself**. If it must wait, it raises `ingest.SourceDeferred(reason, retry_after_seconds)`.
- **Where results live** — in the source's own table (via `db.upsert_<name>`), with its health in `source_status` and every run in `source_runs`. Those are visible on the Server page and at `/api/server/sources`.
- **Cadence** — `min_interval` / `retry_interval` only seed the source's `source_schedules` row. After that, the row (editable in the UI) is what runs.

**Adding a source** (full checklist: [`.claude/skills/adding-a-data-source/SKILL.md`](.claude/skills/adding-a-data-source/SKILL.md)):

1. Create `app/sources/<name>.py` with `fetch()` and pure parse helpers.
2. Add the Pydantic model in `app/models.py`.
3. Add the table to `init_schema`, plus `upsert_<name>` / `get_<name>` in `app/db.py`.
4. Register it in `SOURCES` (before `boom_score` / `alerts`) and add `GET /api/<name>`.
5. Add pytest coverage for parsing (offline) and storage (the `conn` fixture).
6. In the frontend, add the fetch in `src/api.js`, wire it into `useDashboardData.js`, and add the name to `EXTERNAL_SOURCES`.

**Adding a view** — create a `*Panel.jsx` with a co-located `*.module.css` in `src/components/`, register it in `VIEWS` (`App.jsx`) and `TITLES` (`src/lib/routes.js`), and add it to `commandItems` (and, to stay in sync, `desktop/src/tray.js`).

## API overview

Interactive OpenAPI docs are served by FastAPI at **`/docs`** (and `/openapi.json`) on the backend port. Everything under `/api` requires an active session except `/api/health` and `/api/auth/*`.

| Area | Endpoints |
|---|---|
| Health | `GET /api/health` (version and commit) |
| Auth | `POST /api/auth/register`, `/login`, `/totp/verify`, `/totp/enable`, `/recovery`, `/logout`, `/onboarded`; `GET /api/auth/me`, `/status`, `/totp/setup`, `/registration`; OAuth: `GET /api/auth/oauth/providers`, `/{provider}/start`, `/{provider}/callback` |
| Watchlists | `GET/POST /api/watchlists`, `PATCH/DELETE /api/watchlists/{id}`, `GET/POST /api/watchlist`, `PATCH/DELETE /api/watchlist/{ticker}` |
| Portfolio and profile | `GET/POST /api/portfolio`, `PUT/DELETE /api/portfolio/{ticker}`, `POST /api/portfolio/{ticker}/sell`, `PUT /api/portfolio/{ticker}/category`, `GET/POST /api/portfolio/cash`, `PUT /api/portfolio/cash/{currency}`, `GET /api/portfolio/transactions` (all take `?account=`), `GET/PUT /api/profile`, `GET/PUT /api/fx-watch`, `GET /api/fx/rates` |
| Demo bots | `GET /api/bots`, `/bots/strategies`, `/bots/{id}/events`; `POST /api/bots`, `/bots/{id}/pause`, `/bots/{id}/resume`, `/bots/{id}/stop`, `/bots/stop-all` |
| Risk | `GET /api/portfolio/rules`, `GET/PUT/DELETE /api/portfolio/{ticker}/rules`, `GET /api/risk/status`, `PUT /api/risk/settings`, `POST /api/risk/drawdown/acknowledge`, `/risk/kill-switch`, `/risk/kill-switch/release` (fresh TOTP), `GET /api/automute`, `PUT /api/automute/{component}` |
| Paper trading | `GET /api/paper`, `/paper/ticket-defaults`; `POST /api/paper/reset`, `/paper/orders`, `/paper/orders/{id}/cancel` |
| Analytics | `GET /api/portfolio/analytics?account=` |
| Rules and backtests | `GET /api/rules/schema`, `GET/POST /api/rules`, `PUT/DELETE /api/rules/{id}`, `POST /api/rules/backtest` |
| Scanner and push | `GET /api/scanner`, `PUT /api/scanner/prefs`, `GET /api/stream` (server-sent events) |
| Audit | `GET /api/audit`, `/audit.csv`, `/server/audit/verify` (admin) |
| Telegram | `GET/PUT/DELETE /api/telegram`, `POST /api/telegram/link` |
| Broker (Alpaca paper) | `GET /api/broker`, `POST /api/broker/orders`, `/broker/orders/{id}/cancel`, `/broker/sync` |
| Lots and rebalance | `GET /api/portfolio/lots`, `POST /api/portfolio/lots/sale`, `GET/PUT /api/portfolio/targets`, `GET /api/portfolio/rebalance` |
| Time Machine and 13F | `POST /api/timemachine`, `GET /api/13f`, `/13f/{ticker}` |
| Signals | `GET /api/contracts`, `/news`, `/trades`, `/congress-trades`, `/signals`, `/short-interest`, `/social`, `/analyst`, `/fundamentals`, `/earnings`, `/earnings/{ticker}`, `/seasonality`, `/x-posts` |
| Market sentiment | `GET /api/sentiment`, `/fear-greed`, `/vix`, `/aaii`, `/put-call`, `/margin-debt`, `/yield-curve`, `/econ-calendar` |
| Scores and analysis | `GET /api/boom-scores`, `/boom-scores/history/{ticker}`, `/analysis`, `/analysis/{ticker}`, `/analysis/{ticker}/report`, `/analyze/{ticker}`, `/suggestions`, `/suggestions/history`, `/suggestions/log`, `/backtest/track-record`, `/backtest/signal-replay`; `POST /api/suggestions/send-test` |
| Search and charts | `GET /api/search?q=`, `/quotes`, `/sparklines`, `/chart/{ticker}`, `/chart/{ticker}/extended`, `/company/{ticker}`, `/company-names`; drawings under `/api/drawings/{ticker}` (drafts under `…/drafts` and `/api/drawings/drafts/{id}`) |
| Alerts | `GET /api/alerts`, `POST /api/alerts/read` |
| Operations | `POST /api/refresh/{source}` (`force=1` is admin-only), `GET /api/sources`, `GET /api/server/overview`, `/server/events`, `/server/sources`, `/server/sources/{source}/runs`, `/server/schedules`, `PUT /api/server/schedules/{source}`, `POST …/run-now`, `GET/PUT /api/settings` (PUT is admin-only) |
| Updates | `GET /api/update/status`, `POST /api/update/apply` (admin only) |

## Development workflow

```bash
# Backend — run from backend/
.venv/Scripts/python.exe -m pytest                       # full suite
.venv/Scripts/python.exe -m pytest tests/test_edgar.py   # one module

# Frontend — run from frontend/
npm run lint
npm run build

# Extension / desktop — run from their directories
npm run test
npm run lint
```

- **Tests** — each test gets a fresh temporary SQLite database via the `conn` fixture. API tests use `tests/conftest.py::authenticate`, which registers a user and enrolls TOTP with pyotp. Source tests exercise the parsers offline.
- **Formatting** — there is no auto-formatter; match the surrounding code. ESLint is the gate for all JavaScript apps.
- **Branches** — work on `feat/<topic>` or `fix/<topic>` branches and merge to `main` through a pull request. Commit messages follow Conventional Commits with a scope, e.g. `feat(chart): …`, `fix(ui): …`, `docs(reports): …`.
- **Builds** — `npm run build` in `frontend/` produces `frontend/dist/`, which the backend serves automatically. The extension builds to `dist-chrome/` and `dist-firefox/`.
- **Versioning** — `backend/app/version.py` is the source of truth, and `frontend/package.json` mirrors it.
- **Installer** — `.github/workflows/installer.yml` builds `SignalSetup.exe` on `v*` tags (attached to the release), on PRs touching `windows/setup/` or `windows/Common.ps1`, and on manual dispatch. It bundles `nssm.exe` only when the download matches the pin in `windows/Common.ps1`.
- **Debugging** — check the logs in `backend/logs/` (the service uses `C:\ProgramData\SignalDashboard\logs\`), `start.ps1 -Status` / `-Logs`, `/api/health`, and the **Server page**, which shows per-source status, run history with tracebacks, the queue, and system metrics.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Every `/api` call returns 401 | No active session: sign in and finish TOTP. With the desktop app, remember `localhost` and `127.0.0.1` are separate cookie jars. |
| CORS errors in dev | The frontend origin is missing from `STOCKS_CORS_ORIGINS`. `*` is rejected by design. |
| `:8000` serves the API but no UI | `frontend/dist/` doesn't exist. Run `npm run build` in `frontend/`. |
| A source shows `error` | This is intentional: the source failed and the app shows that rather than invent data. Open the source on the Server page for the full traceback. |
| EDGAR returns 403 | Set `STOCKS_SEC_USER_AGENT` to a real name and email. |
| GDELT shows `deferred` | It was rate-limited (429). The source retries on its own after the cooldown. |
| `margin_debt` fails with an install hint | Playwright or its Chromium is missing. Run `python -m playwright install chromium` in the venv. |
| Environment variables seem ignored | Nothing reads `.env`. Export them in the shell, or use `windows/service.env` for the service and reinstall it. |
| The service comes up with no data | It uses `C:\ProgramData\SignalDashboard\db\`, a separate database with separate accounts from `backend\stocks.db`. |
| `database is locked` or duplicated jobs | More than one uvicorn worker or process is running. Run exactly one (`start.ps1 -Status`, `-Kill`). |
| Registration is refused | `STOCKS_REGISTRATION` is `invite` (enter the code) or `closed`. |
| The in-app update returns 409 | The checkout isn't on `main`, has tracked changes, or can't fast-forward. |
| The desktop app offers an update but can't apply it | Only an admin can apply updates (Info → Updates). For admins, the reason is the same as the 409 above. |
| SmartScreen blocks `SignalSetup.exe` | The exe is unsigned. Choose *More info → Run anyway*. |
| NSSM checksum mismatch | The download doesn't match the pin in `windows/Common.ps1`, so the install stops. Verify the zip against nssm.cc before changing the pin (see `windows/README.md`). |
| Port already in use | Run `start.ps1 -Kill`, or change `-ApiPort` / `-WebPort`. |

## Security considerations

- **Data** — the app only ingests *public* data and makes no trades. Treat upstream responses as untrusted input: ticker inputs are whitelisted (`validation.clean_ticker`) before they reach outbound URLs.
- **Credentials** — passwords are hashed with Argon2id, TOTP 2FA is mandatory, and recovery codes are single-use and stored hashed. Session tokens are opaque, stored as SHA-256 hashes, delivered as httpOnly SameSite=Lax cookies, and rotated on every auth-state upgrade.
- **Secrets** — keep them in environment variables only, never in code or the database. `windows/service.env` is gitignored, so never commit it.
- **Privileged execution** — the Windows service runs as **LocalSystem** under NSSM. The NSSM 2.24 download is checked against the SHA-256 pinned in `windows/Common.ps1`, and a mismatch is fatal (see `windows/README.md`). `SignalSetup.exe` is unsigned, so verify you downloaded it from this repository's releases. The in-app updater is admin-only and spawns `windows/update.ps1`, which pulls from `origin/main`, so protect that branch.
- **Account creation** — `app/registration.py` is the single gate for both password and OAuth sign-up. On a reachable deployment, use `invite` or `closed`. The first account becomes admin, so register it before exposing the app.
- **Public exposure** — the app is designed to sit behind a tunnel (Tailscale Funnel). The bind stays on `127.0.0.1`. `X-Forwarded-For` is trusted only from `STOCKS_TRUSTED_PROXY_IPS`, and HSTS is sent only on requests that really arrived over HTTPS. Set `STOCKS_COOKIE_SECURE=1` there.
- **Abuse** — an in-memory, per-client rate limiter protects sensitive routes, and CORS uses an explicit allowlist only.

## Contributing

- **Signals, not predictions.** Never fabricate, placeholder or smooth over data. A failing source must surface as an error.
- **Backend conventions** — one module per source, all SQL in `app/db.py`, sources never write to the database, `ingest.run_source` is the only orchestrator, and schema changes are additive through `init_schema`.
- **Frontend conventions** — one component per file with a co-located CSS Module, design tokens from `src/index.css` (no CSS framework), and accessibility first (keyboard navigation, reduce-motion, dyslexia mode, WCAG AA contrast).
- **Keep the duplicated constants in sync** across `frontend/`, `extension/` and `desktop/`. The table is in [`CLAUDE.md`](CLAUDE.md).
- **Pull requests** — keep them focused, with tests for new logic. `pytest` and every touched app's `npm run lint` must pass. Branch from `main` as `feat/<topic>` or `fix/<topic>`.
- For deeper architecture notes, see [`CLAUDE.md`](CLAUDE.md), [`PRODUCT.md`](PRODUCT.md) and [`DESIGN.md`](DESIGN.md).

## License, ownership and support

- **License** — [MIT](LICENSE), © 2026 Michael Scharff.
- **Support** — open an issue on [Minka1902/stock-dashboard](https://github.com/Minka1902/stock-dashboard/issues).
- **Disclaimer** — this tool surfaces public information for research. It is not financial advice.
