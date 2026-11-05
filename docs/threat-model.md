# Threat model: orders, guardrails and the audit trail

This covers what the trading features protect against today, with paper
trading and demo bots only and no live broker. It also covers what has to be
true before a live broker (STC-55) is connected.

## Assets
- **Your money, once a live broker exists.** Today nothing can reach a market.
  `execution.validate` refuses account `live` outright.
- **The book's integrity.** The ledger (`transactions`) is the only write path
  for positions and cash, and `ledger.replay` must reproduce the tables.
- **The record of what happened.** `audit_log` is one hash chain per user.
- **Credentials:** session cookies, TOTP secrets, SMTP/Twilio/Telegram tokens,
  OAuth client secrets and (later) broker keys.

## Trust boundaries
1. **Browser → API.**
   - Everything under `/api` needs an `active` session (password + TOTP).
   - Every order-changing route takes the user from the session, never from
     the body.
   - An order, bot, rule or audit row of another account answers 404 or 403,
     like a missing one.
2. **API → order path.**
   - Every order goes through `execution.submit`: validation, then
     `execution.CHECKS` (the guardrails in `app/guardrails.py`), then the broker.
   - The bots use the same `submit`; there is no second path.
   - Importing `execution` installs the guardrails, so no import order can skip
     them.
3. **Scheduler → order path.**
   - The minute job fills resting paper orders and runs the bots.
   - It acts only while the ticker's own exchange is LIVE, and only on a quote
     that is itself LIVE and fresh (`quotes.tradable_price`).

## Threats and what stops them
| Threat | Control | Test |
|---|---|---|
| A runaway or buggy bot keeps buying | Guardrails per order and per day (count and notional), max position %, a market-hours rule; the global kill switch pauses every bot | `test_guardrails.py` |
| A stale or after-hours quote trips a stop or fills an order | `quotes.tradable_price` (LIVE and fresh only) in the bots, the exit rules and the paper broker | `test_bots.py`, `test_exits.py`, `test_paper.py` |
| A fill races a cancel and both win | `db.fill_order` / `set_order_status` change only an open order, inside one `BEGIN IMMEDIATE` | `test_paper.py` |
| A stolen session re-enables trading after a kill switch | Release needs a fresh TOTP code (`auth.verify_step_up`); a code can't be replayed; a failure is audited | `test_guardrails.py` |
| Someone edits or deletes history | Hash chain per user; `audit.verify` names the first broken row; admins verify every chain from the Server page | `test_guardrails.py` |
| Secrets leak into logs | `logging_config.RedactSecrets` on every handler: known secret values, `key=value` credentials, bot tokens and bearer headers, including tracebacks | `test_guardrails.py` |
| Deleting an account strands data that a reissued id would inherit | `db.delete_user` sweeps every per-user table, including `orders` and `audit_log` | `test_delete_user.py` |

## Before a live broker is connected (STC-48, STC-49, STC-51, STC-55)
- **Broker keys** go in the OS keyring under the service account, never in
  SQLite, never in an API response and never in a log. Add the key names to
  `_SECRET_SETTINGS`.
- **Key scope:** refuse keys that allow withdrawals or transfers, and show the
  scopes that were detected.
- **TOTP step-up** to enable live mode and for live orders above a notional
  threshold. The mechanism exists: `auth.verify_step_up`.
- **Calibrate costs:** `COST_BPS_PER_SIDE` from measured live slippage
  (`execution.median_slippage(..., "live")`), never from paper.
- **Re-review this document.**

## Out of scope today
- Multi-process deployments. The rate limiter, caches and scheduler are
  in-process by design (one uvicorn worker).
- A compromised host. Anyone with the machine has the database and the
  service environment.
