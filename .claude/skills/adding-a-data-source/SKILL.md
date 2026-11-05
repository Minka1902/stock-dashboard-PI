---
name: adding-a-data-source
description: Step-by-step checklist for adding a new market-data source to the dashboard — the source module, Pydantic model, DB table and upsert/get helpers, SOURCES registry entry and API route, tests, and the frontend wiring. Use when adding, replacing, or removing a data source (contracts, news, insider trades, congress, technicals, sentiment, earnings, etc.).
---

# Adding a new data source

The whole pipeline hangs off the `SOURCES` registry in `app/main.py`:
`name -> SourceSpec(fetch, store, min_interval, retry_interval, force_on_daily)` (plain
3-tuples still work). The cadence fields only seed the source's `source_schedules` row; the
per-source scheduler job (`src:<name>`) then follows that row, which admins edit on the
Server page. A new source gets its row automatically on the next startup.

1. `app/sources/<name>.py` with `fetch(...) -> list[<Model>]` (pure parse helpers kept
   separate from the throttled HTTP, so they can be unit-tested without network).
2. Add the Pydantic model to `app/models.py`.
3. In `app/db.py`: add the table to `init_schema`, plus `upsert_<name>` / `get_<name>`
   (and any `get_<name>_for(ticker)` helpers Boom Score needs).
4. Register in the `SOURCES` dict in `app/main.py` (before `boom_score`/`alerts`) and add
   a `GET /api/<name>` route.
5. Add a `pytest` test (parsing logic without network; storage via the `conn` fixture).
6. Frontend: add the fetch in `src/api.js`, wire it into `src/hooks/useDashboardData.js`
   (state + `Promise.all` load), add the source name to `EXTERNAL_SOURCES` there, and add
   a panel/view.

## Ordering

Each source runs on its own schedule; registry order is only the startup order. `boom_score`,
`alerts`, `regime`, `automute` and `rules` are the **derived** step
(`schedules.DERIVED_MEMBERS`): they run together, in that order, after upstream sources
succeed — so a source Boom Score reads needs no ordering work, and must never be added to
`DERIVED_MEMBERS`. A source nothing derived reads (the scanner's, equity snapshots) passes
`requests_derived=False` so its successes don't pull the derived step forward.

## Non-negotiables

- Sources never write to the DB themselves — `ingest.run_source` is the only orchestrator,
  and it never raises: any exception becomes that source's `error: ...` status.
- Never fabricate or placeholder a value. If the upstream is unavailable, let the source
  record an error status so the UI can show it. That is the core product principle and it
  applies to every new source without exception.
- Give slow or rate-limited upstreams a `min_interval` (and a shorter `retry_interval`) so
  their seeded schedule is polite. If the upstream says "come back later" (a 429 cooldown),
  raise `ingest.SourceDeferred(reason, retry_after_seconds)` — it is recorded as a visible
  `deferred` run with its next attempt, not an error and never a silent skip.
