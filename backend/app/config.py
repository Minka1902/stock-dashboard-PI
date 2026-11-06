"""Central configuration. Override via environment variables."""
import os
from datetime import date, timedelta
from pathlib import Path

# SQLite file location (one file, no server). Defaults to backend/stocks.db,
# resolved from this file rather than the cwd — the same way LOG_DIR below is.
# A cwd-relative default would silently create a second, empty database
# whenever the process starts somewhere else. On the Pi, pi/stocks.env points
# it at /var/lib/stocks-dashboard/db/stocks.db.
DB_PATH = os.environ.get("STOCKS_DB_PATH") or str(
    Path(__file__).resolve().parents[1] / "stocks.db")

# Log level for the app-wide logging config (see app/logging_config.py).
LOG_LEVEL = os.environ.get("STOCKS_LOG_LEVEL", "INFO")

# Where the rotating log file lives. Defaults to backend/logs/ (this file is
# backend/app/config.py, so parents[1] is backend/); the Pi uses
# /var/log/stocks-dashboard. The updater's status and request files live here too.
LOG_DIR = Path(os.environ.get("STOCKS_LOG_DIR") or (Path(__file__).resolve().parents[1] / "logs"))
LOG_MAX_BYTES = int(os.environ.get("STOCKS_LOG_MAX_BYTES", str(5 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(os.environ.get("STOCKS_LOG_BACKUP_COUNT", "5"))

# Scheduling (one APScheduler job per source; see app/schedules.py).
# misfire_grace_time is None on every job: a job delayed by a busy refresh
# thread or a sleeping laptop runs late rather than being dropped. (APScheduler's
# own default is 1 second, which silently drops it.)
#
# First runs after startup (and after a schedule edit) are held back at least
# this long, staggered one second per source, so a restart never stampedes the
# single refresh thread and a test client never triggers real network fetches.
SCHEDULER_STARTUP_DELAY_SECONDS = int(
    os.environ.get("STOCKS_SCHEDULER_STARTUP_DELAY_SECONDS", "30"))
# boom_score -> alerts is one "derived" step. After any upstream source
# succeeds it is pulled forward to run this many seconds later, so a burst of
# sources finishing together coalesces into one recompute.
DERIVED_DEBOUNCE_SECONDS = int(os.environ.get("STOCKS_DERIVED_DEBOUNCE_SECONDS", "30"))
# Timezone new schedule rows are created in (only matters for "at times" mode
# and day filters). Editable per source on the Server page.
SCHEDULE_DEFAULT_TZ = os.environ.get("STOCKS_SCHEDULE_TZ", "Asia/Jerusalem")

# Retention for the run-history tables that feed the Server page. They are
# append-only and would otherwise grow without bound.
SOURCE_RUN_RETENTION_DAYS = int(os.environ.get("STOCKS_SOURCE_RUN_RETENTION_DAYS", "30"))
JOB_RUN_RETENTION_DAYS = int(os.environ.get("STOCKS_JOB_RUN_RETENTION_DAYS", "90"))
BOOM_HISTORY_RETENTION_DAYS = int(os.environ.get("STOCKS_BOOM_HISTORY_RETENTION_DAYS", "400"))

# --- Running unattended on the Pi ---
# /api/health reports "degraded" once work has waited this long for the single
# refresh thread (the job in front of it hung); pi/healthcheck.sh then restarts.
HEALTH_STUCK_SECONDS = int(os.environ.get("STOCKS_HEALTH_STUCK_SECONDS", "1800"))
# Nightly online backup (db_backup job). Point the directory at a USB stick so
# a dead SD card doesn't take the backups with it.
BACKUP_DIR = Path(os.environ.get("STOCKS_BACKUP_DIR") or (Path(DB_PATH).parent / "backups"))
BACKUP_KEEP = int(os.environ.get("STOCKS_BACKUP_KEEP", "7"))
BACKUP_HOUR = int(os.environ.get("STOCKS_BACKUP_HOUR", "3"))
BACKUP_MINUTE = int(os.environ.get("STOCKS_BACKUP_MINUTE", "47"))
# Connectivity probe (app/netwatch.py): plain TCP connects, no DNS, so a DNS
# outage still reads as online and surfaces as honest source errors instead.
NETCHECK_HOSTS = [
    h.strip() for h in os.environ.get("STOCKS_NETCHECK_HOSTS", "1.1.1.1:443,8.8.8.8:53").split(",")
    if h.strip()
]
NETCHECK_INTERVAL_SECONDS = int(os.environ.get("STOCKS_NETCHECK_INTERVAL_SECONDS", "30"))
NETCHECK_TIMEOUT_SECONDS = float(os.environ.get("STOCKS_NETCHECK_TIMEOUT_SECONDS", "3"))
# How long an offline deferral waits if netwatch itself never sees the network
# come back (it normally re-arms the deferred sources the moment it does).
NETCHECK_OFFLINE_RETRY_SECONDS = int(os.environ.get("STOCKS_NETCHECK_OFFLINE_RETRY_SECONDS", "3600"))
# Raspberry Pi firmware flags (under-voltage, throttling); absent off a Pi.
PI_THROTTLED_PATH = os.environ.get(
    "STOCKS_PI_THROTTLED_PATH", "/sys/devices/platform/soc/soc:firmware/get_throttled")

# --- Web security ---
# Comma-separated list of allowed browser origins. Because the session cookie
# rides on allow_credentials=True, a wildcard here would be a security hole —
# reject it outright rather than silently weaken auth.
CORS_ORIGINS = [
    o.strip()
    for o in os.environ.get("STOCKS_CORS_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
]
if "*" in CORS_ORIGINS:
    raise ValueError(
        "STOCKS_CORS_ORIGINS must list explicit origins; '*' is not allowed "
        "because the API uses credentialed (cookie) requests."
    )

# Set to 1 when serving over HTTPS so session cookies are marked Secure.
COOKIE_SECURE = os.environ.get("STOCKS_COOKIE_SECURE", "0") in ("1", "true", "True")

# IPs whose X-Forwarded-For we believe (see app/security.py::_client_ip). The
# only proxy we ever expect is one terminating TLS on this machine — Tailscale
# Funnel, nginx, Caddy — so loopback is the default and anything else is opt-in.
# run_server.py passes the same list to uvicorn's ProxyHeadersMiddleware, so
# both layers agree on one trust boundary.
TRUSTED_PROXY_IPS = frozenset(
    p.strip()
    for p in os.environ.get("STOCKS_TRUSTED_PROXY_IPS", "127.0.0.1,::1").split(",")
    if p.strip()
)

# Strict-Transport-Security max-age, in seconds; 0 disables the header. Only
# ever sent on requests that actually arrived over HTTPS — an HSTS header on
# http://localhost would pin that origin to HTTPS in the browser profile
# permanently and break local dev in a way that is very hard to diagnose.
HSTS_SECONDS = int(os.environ.get("STOCKS_HSTS_SECONDS", "0"))

# Who may create an account: "open" (anyone), "invite" (must present
# STOCKS_INVITE_CODE), "closed" (nobody). Being reachable and being open for
# signup are separate decisions — see app/registration.py.
REGISTRATION_MODE = os.environ.get("STOCKS_REGISTRATION", "open").strip().lower()
if REGISTRATION_MODE not in ("open", "invite", "closed"):
    raise ValueError(
        "STOCKS_REGISTRATION must be one of: open, invite, closed "
        f"(got {REGISTRATION_MODE!r})"
    )
INVITE_CODE = os.environ.get("STOCKS_INVITE_CODE", "")
if REGISTRATION_MODE == "invite" and not INVITE_CODE:
    raise ValueError(
        "STOCKS_REGISTRATION=invite requires STOCKS_INVITE_CODE. An empty code "
        "would compare equal to an empty submission and open registration to "
        "anyone with the URL."
    )

# Lifetime of a fully-authenticated session. Default 14 days.
SESSION_TTL_SECONDS = int(os.environ.get("STOCKS_SESSION_TTL_SECONDS", str(14 * 86400)))
# Lifetime of the short-lived session between password check and TOTP entry.
PENDING_SESSION_TTL_SECONDS = int(os.environ.get("STOCKS_PENDING_SESSION_TTL_SECONDS", "300"))

# Default cadence for sources without a slower one of their own (and for the
# derived boom_score -> alerts step), in seconds. Default 3 min. Only seeds the
# per-source schedule rows; each is then editable on the Server page.
REFRESH_INTERVAL_SECONDS = int(os.environ.get("STOCKS_REFRESH_SECONDS", "180"))

# How many days back to pull contracts on each run.
CONTRACTS_LOOKBACK_DAYS = int(os.environ.get("STOCKS_CONTRACTS_LOOKBACK_DAYS", "30"))

# Max contracts to pull per refresh.
CONTRACTS_LIMIT = int(os.environ.get("STOCKS_CONTRACTS_LIMIT", "50"))

# --- News (GDELT) ---
NEWS_QUERY = os.environ.get(
    "STOCKS_NEWS_QUERY",
    # GDELT requires multi-word phrases to be quoted.
    '("stock market" OR "federal reserve" OR economy OR sanctions OR "defense spending")',
)
NEWS_LIMIT = int(os.environ.get("STOCKS_NEWS_LIMIT", "40"))
# Per-ticker news pulled for every portfolio/watchlist symbol (kept small to
# stay polite with GDELT: one extra request per symbol per refresh).
NEWS_PER_TICKER_LIMIT = int(os.environ.get("STOCKS_NEWS_PER_TICKER_LIMIT", "8"))
# Cap how many tickers get a per-ticker GDELT query per fast refresh cycle. Each
# is a blocking HTTP call, so an unbounded watchlist could make the news step
# outlast the whole refresh interval and starve the single worker.
NEWS_MAX_TICKERS = int(os.environ.get("STOCKS_NEWS_MAX_TICKERS", "12"))
# GDELT per-request timeout. Kept short so a hung/slow GDELT can't pin a worker
# thread for 30s each.
GDELT_TIMEOUT_SECONDS = float(os.environ.get("STOCKS_GDELT_TIMEOUT_SECONDS", "8"))
# After GDELT returns 429 (rate limited), skip it entirely for this long instead
# of hammering it every cycle (which just earns more 429s and burns worker time).
GDELT_COOLDOWN_SECONDS = float(os.environ.get("STOCKS_GDELT_COOLDOWN_SECONDS", "600"))
# GDELT rate-limits hard, so refresh news at most once a day rather than every
# cycle. The daily deep run no longer forces this (force_on_daily=False in the
# SOURCES registry): forcing it defeated the gate the rate limiting requires.
GDELT_MIN_INTERVAL_SECONDS = int(os.environ.get("STOCKS_GDELT_MIN_INTERVAL_SECONDS", str(86400)))
# Retry sooner than the daily gate after a failure — a transient timeout should
# not cost a whole day of headlines.
GDELT_RETRY_INTERVAL_SECONDS = int(os.environ.get("STOCKS_GDELT_RETRY_INTERVAL_SECONDS", "1800"))

# --- Insider trades (SEC EDGAR Form 4) ---
# SEC requires a descriptive User-Agent with contact info for fair-access.
SEC_USER_AGENT = os.environ.get(
    "STOCKS_SEC_USER_AGENT", "Signal Dashboard minka.scharff@gmail.com"
)
# How many recent Form 4 filings to parse per refresh (each costs ~2 requests).
EDGAR_LIMIT = int(os.environ.get("STOCKS_EDGAR_LIMIT", "25"))


# --- Technical signals (Alpha Vantage) ---
# Set to a free AV key to use as primary; falls back to Yahoo Finance if unset.
ALPHA_VANTAGE_KEY: str | None = os.environ.get("STOCKS_ALPHA_VANTAGE_KEY") or None

# --- Congressional trades ---
CONGRESS_LOOKBACK_DAYS = int(os.environ.get("STOCKS_CONGRESS_LOOKBACK_DAYS", "90"))
CONGRESS_MIN_INTERVAL_SECONDS = int(os.environ.get("STOCKS_CONGRESS_MIN_INTERVAL_SECONDS", "21600"))
# House PTR PDFs read per run (each once), and where pdftotext (poppler) is
# when it isn't on PATH, e.g. for the Windows service.
CONGRESS_PTR_PER_RUN = int(os.environ.get("STOCKS_CONGRESS_PTR_PER_RUN", "25"))
PDFTOTEXT = os.environ.get("STOCKS_PDFTOTEXT", "")
# Famous investors' 13F holdings (sources/holdings13f.py): narrow the built-in
# filer list to these CIKs (comma-separated); empty keeps all of them.
FILERS_13F = [c.strip() for c in os.environ.get("STOCKS_13F_FILERS", "").split(",") if c.strip()]

# --- Yield curve ---
YIELD_CURVE_MONTHS = int(os.environ.get("STOCKS_YIELD_CURVE_MONTHS", "3"))

# --- Economic calendar ---
# Optional Financial Modeling Prep key. When set, we use FMP's economic_calendar
# (which carries official Low/Medium/High impact ratings); otherwise we fall back
# to Nasdaq's keyless endpoint and classify importance ourselves.
FMP_KEY: str | None = os.environ.get("STOCKS_FMP_KEY") or None
ECON_CALENDAR_DAYS_AHEAD = int(os.environ.get("STOCKS_ECON_CALENDAR_DAYS_AHEAD", "7"))
ECON_CALENDAR_DAYS_BACK = int(os.environ.get("STOCKS_ECON_CALENDAR_DAYS_BACK", "1"))
# Calendar entries move slowly — refresh at most hourly.
ECON_CALENDAR_MIN_INTERVAL_SECONDS = int(
    os.environ.get("STOCKS_ECON_CALENDAR_MIN_INTERVAL_SECONDS", "3600")
)
# Comma-separated country allowlist (matches the app's US-equity orientation).
# Empty string = keep every country. "Israel" is a valid entry (e.g.
# "United States,Israel") but only the FMP path carries Israeli releases —
# the keyless Nasdaq feed has none (checked 2026-09).
ECON_CALENDAR_COUNTRIES = [
    c.strip()
    for c in os.environ.get("STOCKS_ECON_CALENDAR_COUNTRIES", "United States").split(",")
    if c.strip()
]

# --- Seasonality ---
# Yahoo chart range for deep history ("max" so the "all years" lookback is meaningful).
SEASONALITY_RANGE = os.environ.get("STOCKS_SEASONALITY_RANGE", "max")
# Deep history barely changes intraday — refresh at most every 12 hours.
SEASONALITY_MIN_INTERVAL_SECONDS = int(
    os.environ.get("STOCKS_SEASONALITY_MIN_INTERVAL_SECONDS", "43200")
)


# --- Live quotes ---
# Server-side cache TTL; slightly under the frontend's 30s poll so each poll
# gets at most one fresh Yahoo fetch and concurrent clients share it.
QUOTES_TTL_SECONDS = int(os.environ.get("STOCKS_QUOTES_TTL_SECONDS", "25"))
QUOTES_TIMEOUT_SECONDS = float(os.environ.get("STOCKS_QUOTES_TIMEOUT_SECONDS", "6"))
# Yahoo quote fetches run in a bounded thread pool so a cold miss for N tickers
# costs ~one timeout, not N sequential timeouts, on the request thread.
QUOTES_MAX_WORKERS = int(os.environ.get("STOCKS_QUOTES_MAX_WORKERS", "8"))

# FX pairs shown in the ticker carousel, in Yahoo's "<BASE><QUOTE>=X" form.
# Deliberately NOT watchlist entries: a watchlist ticker is fed to the whole
# analysis pipeline (technicals, boom score, earnings), none of which means
# anything for a currency pair. These ride the same keyless quote endpoint and,
# like the rest of quotes.py, are cached in memory and never persisted.
#
# These are now only the *seed* for each user's own FX watch list (table
# fx_watch, edited in Settings): dollar and euro against the shekel.
FX_PAIRS = [
    p.strip().upper()
    for p in os.environ.get("STOCKS_FX_PAIRS", "USDILS=X,EURILS=X").split(",")
    if p.strip()
]

# Market-overview indexes appended to every user's carousel, as
# "SYMBOL=Label" pairs. TA35.TA is the symbol Yahoo actually serves for the
# TA-35 (^TA35 404s); set STOCKS_TICKER_INDEXES="" to show none.
TICKER_INDEXES: dict[str, str] = {
    sym.strip().upper(): (label.strip() or sym.strip().upper())
    for sym, _, label in (
        item.partition("=")
        for item in os.environ.get("STOCKS_TICKER_INDEXES", "TA35.TA=TA-35").split(",")
        if item.strip()
    )
}


# --- Market sentiment indicators ---
# Yahoo chart range for VIX daily history.
VIX_RANGE = os.environ.get("STOCKS_VIX_RANGE", "6mo")
# AAII survey updates weekly — check at most every 6 hours.
AAII_MIN_INTERVAL_SECONDS = int(os.environ.get("STOCKS_AAII_MIN_INTERVAL_SECONDS", "21600"))
# Put/call is a 5-day average — hourly refresh is plenty.
PUT_CALL_MIN_INTERVAL_SECONDS = int(os.environ.get("STOCKS_PUT_CALL_MIN_INTERVAL_SECONDS", "3600"))

# Contrarian signal thresholds.
SENT_FG_BUY = float(os.environ.get("STOCKS_SENT_FG_BUY", "25"))           # extreme fear → buy
SENT_FG_SELL = float(os.environ.get("STOCKS_SENT_FG_SELL", "75"))         # extreme greed → sell
SENT_VIX_ALERT = float(os.environ.get("STOCKS_SENT_VIX_ALERT", "19"))     # market entering a move
SENT_VIX_EXTREME = float(os.environ.get("STOCKS_SENT_VIX_EXTREME", "30")) # historical turning point
SENT_AAII_EXTREME_PCT = float(os.environ.get("STOCKS_SENT_AAII_EXTREME_PCT", "45"))
SENT_AAII_SPREAD = float(os.environ.get("STOCKS_SENT_AAII_SPREAD", "20"))
SENT_PC_BUY = float(os.environ.get("STOCKS_SENT_PC_BUY", "1.0"))          # heavy puts = fear → buy
SENT_PC_SELL = float(os.environ.get("STOCKS_SENT_PC_SELL", "0.8"))        # complacency → sell

# FINRA publishes margin debt monthly, roughly three to four weeks after the
# month closes, so asking daily only burned requests. Weekly (the intended
# schedule is Mon 06:00 Asia/Jerusalem) picks up each release within days, and
# keeps the headless-browser tier to one Chromium launch a week.
MARGIN_DEBT_MIN_INTERVAL_SECONDS = int(
    os.environ.get("STOCKS_MARGIN_DEBT_MIN_INTERVAL_SECONDS", str(7 * 86400))
)
# Total budget for the headless-browser tier (launch + page + Cloudflare
# clearance + workbook download), in seconds.
MARGIN_DEBT_BROWSER_TIMEOUT_SECONDS = float(
    os.environ.get("STOCKS_MARGIN_DEBT_BROWSER_TIMEOUT_SECONDS", "60")
)
# Retry cadence after a *failed* fetch, which is a different question from how
# often fresh data appears: a week-long success interval must not become a
# week-long outage when FINRA returns a 401/403.
MARGIN_DEBT_RETRY_INTERVAL_SECONDS = int(
    os.environ.get("STOCKS_MARGIN_DEBT_RETRY_INTERVAL_SECONDS", "21600")  # 6h
)
# Direct link to FINRA's margin-statistics workbook. Normally discovered from
# the statistics page, but that page is currently Cloudflare-blocked (403), so
# this is the escape hatch that keeps the workbook tier usable.
MARGIN_DEBT_WORKBOOK_URL = os.environ.get("STOCKS_MARGIN_DEBT_WORKBOOK_URL", "")
SENT_MARGIN_SELL = float(os.environ.get("STOCKS_SENT_MARGIN_SELL", "45"))     # %YoY: overbought
SENT_MARGIN_EXTREME = float(os.environ.get("STOCKS_SENT_MARGIN_EXTREME", "60"))  # pre-crash leverage
SENT_MARGIN_BUY = float(os.environ.get("STOCKS_SENT_MARGIN_BUY", "-20"))      # deleveraging washout


# --- Daily suggestion digest (email + SMS) ---
# All delivery is gated on these being set; unset channels safely no-op + log.
# Email via SMTP (works with a Gmail app password or any SMTP host):
SMTP_HOST: str | None = os.environ.get("STOCKS_SMTP_HOST") or None
SMTP_PORT = int(os.environ.get("STOCKS_SMTP_PORT", "587"))
SMTP_USER: str | None = os.environ.get("STOCKS_SMTP_USER") or None
SMTP_PASSWORD: str | None = os.environ.get("STOCKS_SMTP_PASSWORD") or None
SMTP_FROM: str | None = os.environ.get("STOCKS_SMTP_FROM") or os.environ.get("STOCKS_SMTP_USER") or None
SMTP_STARTTLS = os.environ.get("STOCKS_SMTP_STARTTLS", "1") not in ("0", "false", "False")

# SMS via Twilio REST API (called with httpx — no twilio library needed):
TWILIO_ACCOUNT_SID: str | None = os.environ.get("STOCKS_TWILIO_ACCOUNT_SID") or None
TWILIO_AUTH_TOKEN: str | None = os.environ.get("STOCKS_TWILIO_AUTH_TOKEN") or None
TWILIO_FROM_PHONE: str | None = os.environ.get("STOCKS_TWILIO_FROM_PHONE") or None

# When to send the pre-market digest for the next trading day.
DIGEST_HOUR = int(os.environ.get("STOCKS_DIGEST_HOUR", "7"))
DIGEST_MINUTE = int(os.environ.get("STOCKS_DIGEST_MINUTE", "30"))
DIGEST_TZ = os.environ.get("STOCKS_DIGEST_TZ", "America/New_York")
# How many opportunity / holding suggestions to include.
SUGGESTIONS_COUNT = int(os.environ.get("STOCKS_SUGGESTIONS_COUNT", "5"))

# --- X (Twitter) watcher ---
# Accounts to monitor for market-moving posts / cashtags.
X_ACCOUNTS = [
    a.strip().lstrip("@")
    for a in os.environ.get("STOCKS_X_ACCOUNTS", "realDonaldTrump,aistocksavvy").split(",")
    if a.strip()
]
# Official X API v2 bearer token. When set we use the official API and stamp the
# source status "ok"; when unset we fall back to unofficial Nitter-style RSS
# mirrors and stamp the status as an error-style warning (data is still stored).
X_BEARER: str = os.environ.get("STOCKS_X_BEARER", "")
# Nitter-style mirror base URLs (RSS at {mirror}/{account}/rss). Comma list.
X_MIRRORS = [
    m.strip().rstrip("/")
    for m in os.environ.get(
        "STOCKS_X_MIRRORS",
        "https://nitter.net,https://nitter.poast.org,https://nitter.privacydev.net",
    ).split(",")
    if m.strip()
]
# Max posts to keep per account per refresh.
X_POSTS_LIMIT = int(os.environ.get("STOCKS_X_POSTS_LIMIT", "30"))
# X posts change slowly relative to the fast cycle; the 180s cycle still calls
# x_posts, but this min-interval gate spaces real fetches to at most hourly (the
# daily force-run bypasses it). Hourly keeps us polite with the mirrors/API.
X_MIN_INTERVAL_SECONDS = int(os.environ.get("STOCKS_X_MIN_INTERVAL_SECONDS", "3600"))
X_TIMEOUT_SECONDS = float(os.environ.get("STOCKS_X_TIMEOUT_SECONDS", "8"))


# --- Background technical analysis (OHLC + analysis over the full universe) ---
# The analysis universe is portfolio ∪ watchlist ∪ signal-source candidates. OHLC
# and analysis both run hourly (the 180s cycle calls them; these gates space the
# real work). OHLC_MAX_TICKERS bounds the Yahoo fan-out per run — larger universes
# rotate stalest-first across successive hourly runs. OPPORTUNITY_CANDIDATES caps
# how many recent signal-source tickers get pulled into the universe.
OHLC_MIN_INTERVAL_SECONDS = int(os.environ.get("STOCKS_OHLC_MIN_INTERVAL_SECONDS", "3600"))
ANALYSIS_MIN_INTERVAL_SECONDS = int(os.environ.get("STOCKS_ANALYSIS_MIN_INTERVAL_SECONDS", "3600"))
OHLC_MAX_TICKERS = int(os.environ.get("STOCKS_OHLC_MAX_TICKERS", "60"))
# Raised from 20: suggestions now rank tracked names and brand-new ideas in
# separate pools, so the candidate list has to be deep enough to fill both.
OPPORTUNITY_CANDIDATES = int(os.environ.get("STOCKS_OPPORTUNITY_CANDIDATES", "40"))
# Trading costs (app/costs.py): every simulated fill and every track-record
# outcome pays this many basis points per side, plus half the spread when a
# bid/ask is known. One model, so a backtest and a paper fill cost the same.
COST_BPS_PER_SIDE = float(os.environ.get("STOCKS_COST_BPS_PER_SIDE", "10"))
# Below this many scored outcomes a hit rate is flagged "not enough data".
MIN_TRACK_SAMPLE = int(os.environ.get("STOCKS_MIN_TRACK_SAMPLE", "30"))
# Market regime (app/regime.py). Each input votes risk-on (+1), risk-off (−1)
# or neutral (0); fewer than REGIME_MIN_INPUTS available → "unknown", never a
# guess. A total of ±REGIME_SCORE_THRESHOLD or beyond sets the label. An input
# older than REGIME_MAX_INPUT_AGE_DAYS is treated as unavailable.
REGIME_VIX_RISK_ON = float(os.environ.get("STOCKS_REGIME_VIX_RISK_ON", "16"))
REGIME_VIX_RISK_OFF = float(os.environ.get("STOCKS_REGIME_VIX_RISK_OFF", "25"))
REGIME_VIX_TREND_PCT = float(os.environ.get("STOCKS_REGIME_VIX_TREND_PCT", "20"))  # 5-session move
REGIME_FG_RISK_OFF = float(os.environ.get("STOCKS_REGIME_FG_RISK_OFF", "25"))
REGIME_FG_RISK_ON = float(os.environ.get("STOCKS_REGIME_FG_RISK_ON", "55"))
REGIME_PC_RISK_OFF = float(os.environ.get("STOCKS_REGIME_PC_RISK_OFF", "1.0"))
REGIME_PC_RISK_ON = float(os.environ.get("STOCKS_REGIME_PC_RISK_ON", "0.8"))
REGIME_CURVE_RISK_ON = float(os.environ.get("STOCKS_REGIME_CURVE_RISK_ON", "0.25"))  # 10y−2y, pts
REGIME_SPY_MA_DAYS = int(os.environ.get("STOCKS_REGIME_SPY_MA_DAYS", "200"))
REGIME_MIN_INPUTS = int(os.environ.get("STOCKS_REGIME_MIN_INPUTS", "3"))
REGIME_SCORE_THRESHOLD = int(os.environ.get("STOCKS_REGIME_SCORE_THRESHOLD", "2"))
REGIME_MAX_INPUT_AGE_DAYS = int(os.environ.get("STOCKS_REGIME_MAX_INPUT_AGE_DAYS", "10"))
# Signal auto-mute (app/signals.py): a Boom component whose hit rate over the
# last SIGNAL_WINDOW_DAYS of firings (7-day forward, net of costs) falls below
# SIGNAL_MUTE_BELOW % on at least SIGNAL_MUTE_MIN_N outcomes stops counting;
# it counts again only above SIGNAL_MUTE_BELOW + SIGNAL_UNMUTE_MARGIN.
SIGNAL_WINDOW_DAYS = int(os.environ.get("STOCKS_SIGNAL_WINDOW_DAYS", "30"))
SIGNAL_MUTE_BELOW = float(os.environ.get("STOCKS_SIGNAL_MUTE_BELOW", "40"))
SIGNAL_MUTE_MIN_N = int(os.environ.get("STOCKS_SIGNAL_MUTE_MIN_N", "12"))
SIGNAL_UNMUTE_MARGIN = float(os.environ.get("STOCKS_SIGNAL_UNMUTE_MARGIN", "5"))
# Demo trading bots (app/bots.py): simulated fills move the live price this
# many basis points against the bot (each side; defaults to the shared cost
# model); at most this many bots per
# user that aren't stopped; the engine's cadence.
BOT_COST_BPS = float(os.environ.get("STOCKS_BOT_COST_BPS", str(COST_BPS_PER_SIDE)))
BOT_MAX_BOTS = int(os.environ.get("STOCKS_BOT_MAX_BOTS", "10"))
BOT_INTERVAL_SECONDS = int(os.environ.get("STOCKS_BOT_INTERVAL_SECONDS", "60"))
# Full adjusted daily history for the bar backtester (sources/ohlc.py
# MAX_SERIES): refreshed this often per ticker, at most this many per run.
DAILY_MAX_REFRESH_DAYS = int(os.environ.get("STOCKS_DAILY_MAX_REFRESH_DAYS", "7"))
DAILY_MAX_PER_RUN = int(os.environ.get("STOCKS_DAILY_MAX_PER_RUN", "10"))
# Paper account (app/execution.py): what "Fund / Reset" deposits by default.
PAPER_START_CASH = float(os.environ.get("STOCKS_PAPER_START_CASH", "100000"))
PAPER_START_CURRENCY = os.environ.get("STOCKS_PAPER_START_CURRENCY", "USD").upper()
# Exit-rule evaluator (app/exits.py) cadence; resting paper orders are
# evaluated on the same minute pass.
EXITS_INTERVAL_SECONDS = int(os.environ.get("STOCKS_EXITS_INTERVAL_SECONDS", "60"))
# The oldest quote the 1-minute engines (bots, exits) act on. The quote cache
# serves stale-while-revalidate, so a ticker only an engine asks for is about
# one interval old on every pass — the default allows that and refuses anything
# older (a refresh that has been failing).
ENGINE_QUOTE_MAX_AGE_SECONDS = int(os.environ.get(
    "STOCKS_ENGINE_QUOTE_MAX_AGE_SECONDS", str(2 * BOT_INTERVAL_SECONDS)))
# Benchmark series fetched by the ohlc source whether or not anyone holds them
# (analytics beta / relative return, the regime gate's SPY vs 200-day MA).
BENCHMARK_TICKERS = [
    t.strip().upper() for t in os.environ.get("STOCKS_BENCHMARKS", "SPY,^TA125.TA").split(",")
    if t.strip()
]

# Optional override for the built frontend directory the backend serves (single
# port). Empty → default to <repo>/frontend/dist. Used by tests to point at a
# temp dist. See app/main.py static-serving block.
STATIC_DIR = os.environ.get("STOCKS_STATIC_DIR", "") or None

# --- Earnings calendar ---
# Who counts as "big". Comma-separated override; empty falls back to the
# curated list in app/data/majors.py.
EARNINGS_UNIVERSE = [
    t.strip().upper()
    for t in os.environ.get("STOCKS_EARNINGS_UNIVERSE", "").split(",")
    if t.strip()
]
# One Yahoo call per ticker, so the fan-out is capped and rotated stalest-first
# across runs rather than hammering the whole universe every cycle.
EARNINGS_MAX_TICKERS = int(os.environ.get("STOCKS_EARNINGS_MAX_TICKERS", "120"))
# Earnings dates move rarely; 6h is plenty and keeps the quota clear.
EARNINGS_MIN_INTERVAL_SECONDS = int(
    os.environ.get("STOCKS_EARNINGS_MIN_INTERVAL_SECONDS", "21600"))
EARNINGS_RETRY_INTERVAL_SECONDS = int(
    os.environ.get("STOCKS_EARNINGS_RETRY_INTERVAL_SECONDS", "1800"))

# --- Alerts ---
# Boom Score level whose upward crossing fires a high-severity alert.
ALERT_BOOM_THRESHOLD = int(os.environ.get("STOCKS_ALERT_BOOM_THRESHOLD", "60"))

# Market-wide scanner (app/scanner.py). Universes: "majors" (data/majors.py),
# "mine" (every holding and watchlist), "sp500" / "ndx100" / "ta125" (index
# constituents from Wikipedia, refreshed monthly by the `universe` source).
SCANNER_UNIVERSES = [u.strip().lower() for u in
                     os.environ.get("STOCKS_SCANNER_UNIVERSES", "majors,mine").split(",") if u.strip()]
SCANNER_MAX_TICKERS = int(os.environ.get("STOCKS_SCANNER_MAX_TICKERS", "600"))
SCANNER_INTERVAL_SECONDS = int(os.environ.get("STOCKS_SCANNER_INTERVAL_SECONDS", "300"))
SCANNER_QUOTE_BATCH = int(os.environ.get("STOCKS_SCANNER_QUOTE_BATCH", "150"))
# Daily bars the scans need, for tickers the OHLC job doesn't already keep:
# this many per run, refetched once older than this.
SCANNER_HISTORY_PER_RUN = int(os.environ.get("STOCKS_SCANNER_HISTORY_PER_RUN", "40"))
SCANNER_HISTORY_MAX_AGE_HOURS = int(os.environ.get("STOCKS_SCANNER_HISTORY_MAX_AGE_HOURS", "18"))
SCANNER_RVOL_MIN = float(os.environ.get("STOCKS_SCANNER_RVOL_MIN", "2.0"))
SCANNER_GAP_PCT = float(os.environ.get("STOCKS_SCANNER_GAP_PCT", "3.0"))
# Scanner alerts are opt-in per user; at most this many an hour by default,
# the rest batched into one digest an hour.
SCANNER_ALERTS_PER_HOUR = int(os.environ.get("STOCKS_SCANNER_ALERTS_PER_HOUR", "3"))
# Server-sent events (/api/stream): a stream ends after this long and the
# client reconnects, so no connection outlives a restart by more than this.
STREAM_MAX_SECONDS = int(os.environ.get("STOCKS_STREAM_MAX_SECONDS", "300"))

# Telegram bot (app/telegram.py): alerts out, read-only commands in. A secret,
# so env only; everything Telegram no-ops (and says so) while it's unset.
TELEGRAM_BOT_TOKEN = os.environ.get("STOCKS_TELEGRAM_BOT_TOKEN", "")
TELEGRAM_POLL_SECONDS = int(os.environ.get("STOCKS_TELEGRAM_POLL_SECONDS", "5"))
# Alpaca (app/alpaca.py), the first real broker: secrets, env only. Paper
# unless STOCKS_ALPACA_PAPER=0 (live is refused until STC-51's step-up).
# One account per instance, owned by STOCKS_ALPACA_OWNER (default: the admin).
ALPACA_KEY_ID = os.environ.get("STOCKS_ALPACA_KEY_ID", "")
ALPACA_SECRET_KEY = os.environ.get("STOCKS_ALPACA_SECRET_KEY", "")
ALPACA_PAPER = os.environ.get("STOCKS_ALPACA_PAPER", "1") != "0"
ALPACA_OWNER = os.environ.get("STOCKS_ALPACA_OWNER", "")
ALPACA_RECONCILE_SECONDS = int(os.environ.get("STOCKS_ALPACA_RECONCILE_SECONDS", "900"))

# --- OAuth social login (optional; each provider no-ops when unset) ---
# Base URL the provider redirects back to (the backend origin). The callback is
# f"{OAUTH_REDIRECT_BASE}/api/auth/oauth/{provider}/callback".
OAUTH_REDIRECT_BASE = os.environ.get("STOCKS_OAUTH_REDIRECT_BASE", "http://localhost:8000")
OAUTH_GITHUB_CLIENT_ID = os.environ.get("STOCKS_OAUTH_GITHUB_CLIENT_ID", "")
OAUTH_GITHUB_CLIENT_SECRET = os.environ.get("STOCKS_OAUTH_GITHUB_CLIENT_SECRET", "")
OAUTH_GOOGLE_CLIENT_ID = os.environ.get("STOCKS_OAUTH_GOOGLE_CLIENT_ID", "")
OAUTH_GOOGLE_CLIENT_SECRET = os.environ.get("STOCKS_OAUTH_GOOGLE_CLIENT_SECRET", "")
OAUTH_FACEBOOK_CLIENT_ID = os.environ.get("STOCKS_OAUTH_FACEBOOK_CLIENT_ID", "")
OAUTH_FACEBOOK_CLIENT_SECRET = os.environ.get("STOCKS_OAUTH_FACEBOOK_CLIENT_SECRET", "")
OAUTH_TIMEOUT_SECONDS = float(os.environ.get("STOCKS_OAUTH_TIMEOUT_SECONDS", "10"))


def contracts_date_window() -> tuple[str, str]:
    """Return (start_date, end_date) ISO strings for the lookback window."""
    end = date.today()
    start = end - timedelta(days=CONTRACTS_LOOKBACK_DAYS)
    return start.isoformat(), end.isoformat()
