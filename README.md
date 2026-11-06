# Stock Signal Dashboard (Raspberry Pi)

A self-hosted, multi-user dashboard that aggregates **public** market signals into explainable, per-ticker reads, built for self-directed retail investors who want to act deliberately instead of impulsively. This edition runs headless on a **Raspberry Pi**: it starts at boot, runs every source on its own schedule for as long as the Pi has power and network, and is reached privately over Tailscale.

## Overview

The signals that say "something is happening with this stock" are scattered across dozens of public sources: SEC Form 4 insider filings, federal contract awards, congressional trade disclosures, news, technicals, short interest, social chatter, analyst ratings, fundamentals, and market-wide sentiment. This project pulls them all on a schedule, stores them in one place, and surfaces them per ticker with the source and reasoning attached.

The core principle is **signals, not predictions**. Every number shows where it came from and why it matters. Nothing is a black-box score, and nothing is ever fabricated or filled with placeholders: when a source is unavailable, it records an error status that the UI shows as-is.

Architecturally it is a **FastAPI + SQLite + APScheduler** backend that ingests every source on its own schedule, runs a derived scoring and alerting step, and serves a **React 19 + Vite** single-page app. On the Pi it is a hardened systemd service; GitHub Actions builds the web app so the Pi never runs Node.

## Key features

- **20+ public data sources**: USAspending contracts, SEC EDGAR Form 4, congressional trades (House Clerk PTRs), GDELT news, technicals, short interest, social sentiment, analyst ratings, fundamentals, earnings, OHLC, seasonality, X posts, 13F holdings, and market sentiment (Fear & Greed, VIX, AAII, put/call, margin debt, yield curve, economic calendar). Each source's health, last run and full error traceback are on the Server page.
- **Boom Score**: a weighted `-90…+100` composite per watched ticker. Every contributing component is stored, so the UI can explain the score, and each run is kept as history.
- **Alerts and digests**: transition-based alerts that fire exactly once, plus an optional pre-market email/SMS digest and Telegram.
- **Analysis for any ticker**: a Cmd/Ctrl+K search palette and on-demand analysis (2-year bars, seasonality anchors, HTML report), including for tickers you don't watch.
- **Portfolio and watchlists**: multiple named watchlists and a multi-currency portfolio, per user. Every trade, correction and cash movement is a ledger row, with realized P&L and a daily equity snapshot.
- **Trading Bot page** (no real orders; Alpaca paper only): demo bots (Follow, Trend, Dip-buy, Breakout, Custom rule), risk exits and kill switches, a paper account, performance analytics, a rule builder with honest backtests, a market-wide scanner over server-sent events, and a hash-chained audit log.
- **Multi-user with mandatory 2FA** and an **approval queue**: Argon2id passwords, TOTP, recovery codes, optional OAuth. Anyone on your tailnet can sign up, but sees nothing until the admin approves the account.
- **Built to run unattended**: crash and hang recovery, hardware watchdog, network-outage-aware scheduling, nightly verified backups, a Raspberry Pi health card (power, temperature, memory, disk), and a one-click in-app updater with rollback.
- **Works on a phone**: bottom tab bar, one-row top bar, 40px touch targets below 640px.

## Architecture

```
                         ┌────────────── stocks-dashboard.service (one uvicorn worker, user "stocks") ──────────────┐
 public sources          │                                                                                         │
 (SEC, USAspending,  ──► │ app/sources/<name>.py ──► ingest.run_source ──► app/db.py ──► SQLite (WAL)              │
  GDELT, Yahoo, …)       │   fetch() -> [Model]       store + status          ▲             │                      │
                         │        ▲                   + source_runs row       │             ▼                      │
                         │        │                                           │   derived step:                    │
                         │  APScheduler: one job per source (src:<name>),     │   boom_score -> alerts -> regime   │
                         │  derived / daily_analysis / daily_digest /         │   -> automute -> rules             │
                         │  prune_history / db_backup — on ONE serial thread; │                                    │
                         │  netwatch, update_check on the default executor    └── analysis / suggestions / notify  │
                         │                                                                                         │
                         │ FastAPI /api/* (auth + approval gate, rate limiting) + the web app from frontend/dist   │
                         └───────────────────────────────────────┬─────────────────────────────────────────────────┘
                                                                 │ 127.0.0.1:8000
                                          tailscale serve (HTTPS, tailnet only) ── your phone / PC browser
```

- **Frontend** (`frontend/`): a React 19 SPA. `src/hooks/useDashboardData.js` owns dashboard state; routing uses real History API paths (`src/lib/nav.js`).
- **Backend** (`backend/app/`): FastAPI routes are mostly thin `db.get_* → model_dump()` reads. The `SOURCES` registry in `app/main.py` drives the ingestion pipeline.
- **Database**: one SQLite file in WAL mode (`synchronous=NORMAL`). All SQL lives in `app/db.py`.
- **On the Pi** (`pi/`): systemd units, `install.sh`, the healthcheck timer and the updater. See [`pi/README.md`](pi/README.md).

> **Run exactly one uvicorn worker.** The scheduler, TTL caches, rate limiter and SQLite connections are all in-process.

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.13 (3.11+), FastAPI 0.115, uvicorn, Pydantic 2, httpx, APScheduler 3.11, numpy, xlrd / openpyxl |
| Auth | argon2-cffi (Argon2id), pyotp (TOTP), segno (QR codes), OAuth (Google / GitHub / Facebook) |
| Database | SQLite (WAL, stdlib `sqlite3`) |
| On the Pi | Raspberry Pi OS Lite 64-bit, systemd, Tailscale, polkit, zram, system Chromium + Playwright (margin-debt tier), poppler (`pdftotext`) |
| Frontend | React 19, Vite 8, CSS Modules with design tokens, lightweight-charts, Recharts, Motion, anime.js |
| CI | GitHub Actions: backend tests on an aarch64 runner, frontend lint/build, web-dist publishing |
| Testing / lint | pytest, ESLint, shellcheck |

## Install on a Raspberry Pi

```bash
git clone https://github.com/Minka1902/stock-dashboard-PI.git
```

```bash
sudo stock-dashboard-PI/pi/install.sh --admin-email you@example.com
```

Tailscale must be installed and logged in first, with HTTPS certificates enabled for the tailnet. The full guide covers hardware, first sign-in, settings, how it stays up, updates, backups and troubleshooting: **[`pi/README.md`](pi/README.md)**.

## Which Pi?

Every server feature runs without a GPU. The Pi 3's limit is its 1 GB of RAM and its slow cores, not storage. RAM figures are estimates until measured on a real Pi; the backend itself is expected to use about 150–250 MB.

| Feature | Pi 3 (1 GB, A53) | Pi 4 (2–8 GB, A72) | Pi 5 (4–16 GB, A76) |
|---|---|---|---|
| Every scheduled source, derived step, alerts, bots, backups | ✅ | ✅ | ✅ |
| Building the web app on the device | ❌ RAM: built in CI instead | ✅ 2 GB+ | ✅ |
| margin_debt headless-Chromium tier | ⚠️ marginal (≈300–450 MB, slow challenge); other tiers unaffected | ✅ 2 GB+ | ✅ |
| Scanner over 600 tickers every 5 min | ⚠️ shares the one refresh thread; default `majors,mine` | ✅ | ✅ |
| Backtests / Time Machine / analytics (on demand) | ✅ est. 3–5× slower than a PC | ✅ | ✅ |
| Users at once | ✅ about 2–5 | ✅ dozens | ✅ |
| Dashboard on a screen attached to the Pi | ❌ RAM + GPU | ✅ 4 GB, reduce-motion | ✅ |

## Developing on your PC

```bash
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
cd backend && .venv/bin/python -m uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend && npm ci && npm run dev
```

Vite on `:5173` proxies `/api` to `:8000`. For the single-port mode the Pi uses, run `npm run build` and the backend serves `frontend/dist/` on `:8000`. Always run uvicorn from `backend/` with the `app.main:app` module path (imports are package-relative). On Windows the venv's interpreter is `.venv\Scripts\python.exe`.

```bash
cd backend && .venv/bin/python -m pytest
```

```bash
cd frontend && npm run lint && npm run build
```

**First login.** In the default `open` mode the first account becomes admin. On the Pi, `STOCKS_REGISTRATION=approval` makes `STOCKS_ADMIN_EMAIL` the admin instead, whatever the order.

## Configuration

All configuration is `STOCKS_*` environment variables with defaults in [`backend/app/config.py`](backend/app/config.py). **Nothing loads a `.env` file**: on the Pi, systemd passes `/etc/stocks-dashboard/stocks.env` to the service; in development, export variables in your shell. [`pi/stocks.env.example`](pi/stocks.env.example) is the annotated list of everything you'd set (keys, channels, sign-up mode, backups, the network probe). Secrets stay in the environment, never in code or the database. Per-source schedules are not environment variables: they're seeded from the registry and edited on the Server page.

## Project structure

```
stock-dashboard-PI/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app, SOURCES registry, scheduler wiring, SPA mount
│   │   ├── sources/           # one module per data source: fetch() -> list[Model]
│   │   ├── ingest.py          # run_source: the only ingestion orchestrator
│   │   ├── schedules.py       # pure schedule logic (triggers, next_due)
│   │   ├── db.py              # ALL SQLite access, schema + migrations
│   │   ├── netwatch.py        # online/offline probe; offline deferral + re-arm
│   │   ├── backup.py          # nightly verified online backup
│   │   ├── pihealth.py        # power / temperature / memory / disk for the Server page
│   │   ├── registration.py    # who may sign up; the one account factory (approval queue)
│   │   ├── updater.py         # update check + hand-off to stocks-update.service
│   │   └── …                  # analysis, suggestions, bots, risk, auth, routes_*
│   ├── tests/                 # pytest suite (conftest: conn fixture, authenticate helper)
│   ├── requirements.txt
│   └── run_server.py          # service entry point (in-process uvicorn)
├── frontend/src/              # components/ (*Panel views + shared), hooks/, lib/, api.js
├── pi/                        # install/uninstall, systemd units, healthcheck, update.py, polkit rule, stocks.env.example
├── .github/workflows/pi.yml   # arm64 tests, frontend build, web-dist publishing
├── docs/threat-model.md
├── CLAUDE.md                  # in-depth architecture notes
└── PRODUCT.md / DESIGN.md     # product principles and design system
```

## Core workflows

1. **Ingest.** A source's job (`src:<name>`) fires; `ingest.run_source` fetches, stores, stamps `source_status` and appends a `source_runs` row: `ok`, `error` (with the traceback) or `deferred` (a rate-limit cooldown, or the network being offline). It re-arms the job's next run. Nothing is silently skipped: a job due while another runs queues and runs late.
2. **Derive.** After any upstream success the `derived` job is pulled forward: Boom Score, alerts (each transition fires exactly once), regime, signal auto-mute, saved rules.
3. **Analyze and notify.** `daily_analysis` refreshes holdings' analysis; `daily_digest` sends the pre-market email/SMS.
4. **Stay up.** `netwatch` probes the network every 30 s and re-runs whatever an outage deferred; `db_backup` copies the database nightly; the healthcheck timer restarts a hung service.
5. **Onboard a user.** Sign up → enroll TOTP → (approval mode) wait for the admin → watchlists, portfolio, notification profile.

## Database model

A single SQLite database, created and migrated by `db.init_schema` (idempotent: `CREATE TABLE IF NOT EXISTS` plus additive column migrations).

| Group | Tables |
|---|---|
| Auth | `users` (incl. `approved_at`: NULL = pending), `sessions`, `recovery_codes`, `oauth_identities` |
| Per user | `watchlists`, `watchlist`, `portfolio`, `transactions`, `cash_balances`, `equity_snapshots`, `bots`, `bot_events`, `notify_profile`, `alert_reads`, `fx_watch`, `drawings`, `drawing_drafts`, and the trading tables (`orders`, `audit_log`, `position_rules`, `risk_settings`, `saved_rules`, …) |
| Shared market data | one table per source (`contracts`, `news`, `insider_trades`, `congress_trades`, `ohlc_series`, `fear_greed`, `margin_debt`, …) |
| Derived | `boom_scores`, `boom_score_history`, `alerts`, `alert_state`, `stock_analysis`, `suggestion_history`, `suggestion_log` |
| Operations | `source_status`, `source_runs`, `source_schedules`, `job_runs`, `app_settings`, `data_migrations` |

## API overview

Interactive OpenAPI docs are at **`/docs`**. Everything under `/api` requires an active, approved session except `/api/health` and `/api/auth/*`.

| Area | Endpoints |
|---|---|
| Health | `GET /api/health` (version, commit, checks: db, scheduler, refresh_thread) |
| Auth | `POST /api/auth/register`, `/login`, `/totp/verify`, `/totp/enable`, `/recovery`, `/logout`, `/onboarded`; `GET /api/auth/me` (incl. `approved`), `/status`, `/totp/setup`, `/registration`; OAuth under `/api/auth/oauth/*` |
| Approval queue (admin) | `GET /api/server/signups`, `POST /api/server/signups/{id}/approve`, `/reject` |
| Watchlists, portfolio, profile | `/api/watchlists`, `/api/watchlist`, `/api/portfolio` (+ `/cash`, `/transactions`, `/lots`, `/targets`, `/rebalance`), `/api/profile`, `/api/fx-watch`, `/api/fx/rates` |
| Trading | `/api/bots`, `/api/paper`, `/api/risk/*`, `/api/rules`, `/api/scanner`, `/api/stream`, `/api/audit`, `/api/broker`, `/api/telegram`, `/api/timemachine`, `/api/13f` |
| Signals and sentiment | `/api/contracts`, `/news`, `/trades`, `/congress-trades`, `/signals`, `/short-interest`, `/social`, `/analyst`, `/fundamentals`, `/earnings`, `/seasonality`, `/x-posts`, `/sentiment`, `/fear-greed`, `/vix`, `/aaii`, `/put-call`, `/margin-debt`, `/yield-curve`, `/econ-calendar` |
| Scores and analysis | `/api/boom-scores`, `/analysis`, `/analyze/{ticker}`, `/suggestions`, `/backtest/*`, `/search`, `/quotes`, `/chart/{ticker}`, `/alerts` |
| Operations (admin) | `POST /api/refresh/{source}`, `GET /api/server/overview` (incl. `network`, `pi`), `/server/events`, `/server/sources`, `/server/schedules`, `PUT /api/settings` |
| Updates | `GET /api/update/status`, `POST /api/update/apply` (admin) |

## Security considerations

- **Exposure**: uvicorn binds `127.0.0.1`; the only way in is `tailscale serve` (HTTPS, your tailnet). Cookies are `Secure`; `X-Forwarded-For` is trusted only from loopback.
- **Accounts**: Argon2id passwords, mandatory TOTP, single-use hashed recovery codes, rotating opaque session tokens. With `approval`, only `STOCKS_ADMIN_EMAIL` is admin; everyone else waits for approval.
- **Privileges**: the service runs as the unprivileged `stocks` user under `ProtectSystem=strict`; its polkit rule allows only starting the update unit and restarting itself.
- **Updates** come only from `origin/main` with a CI-built web app, and roll back on failure. Protect `main`.
- **Secrets** live in `/etc/stocks-dashboard/stocks.env` (`root:stocks`, 640), never in code or the database. Physical access to the SD card or backup stick means access to the data.
- See [`docs/threat-model.md`](docs/threat-model.md).

## Contributing

- **Signals, not predictions.** Never fabricate, placeholder or smooth over data. A failing source must surface as an error.
- **Backend conventions**: one module per source, all SQL in `app/db.py`, sources never write to the database, `ingest.run_source` is the only orchestrator, schema changes are additive.
- **Frontend conventions**: one component per file with a co-located CSS Module, design tokens from `src/index.css`, accessibility first.
- **Checks**: `pytest`, `npm run lint`, `npm run build`, and `shellcheck pi/*.sh` for scripts. Commits follow Conventional Commits with a scope (`feat(pi): …`).

## License, ownership and support

- **License**: [MIT](LICENSE), © 2026 Michael Scharff. Ported code is credited in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
- **Disclaimer**: this tool surfaces public information for research. It is not financial advice.
