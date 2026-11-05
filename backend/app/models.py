"""Normalized data models shared across the app."""
from pydantic import BaseModel


class ContractRecord(BaseModel):
    # Unique, stable id from USASpending (used for dedup/upsert).
    external_id: str
    award_id: str
    recipient_name: str
    amount: float
    awarding_agency: str
    start_date: str  # ISO date string, may be ""
    source: str = "usaspending"


class NewsArticle(BaseModel):
    url: str  # unique key
    title: str
    domain: str
    seendate: str  # ISO timestamp
    sourcecountry: str
    image: str  # may be ""
    ticker: str = ""  # "" = macro/geopolitics; else the portfolio/watchlist ticker it matched


class InsiderTrade(BaseModel):
    accession: str  # unique filing id
    ticker: str
    company: str
    owner: str
    role: str  # e.g. "Exec VP-Leasing", "Director", "10% Owner"
    transaction_date: str
    transaction_type: str  # "Buy", "Sell", or "Other"
    shares: float
    value: float  # total $ across the filing's open-market transactions
    filing_url: str
    filed_at: str  # ISO timestamp from the filing feed


class WatchItem(BaseModel):
    ticker: str
    note: str
    added_at: str


class SourceStatus(BaseModel):
    source: str
    last_refreshed_at: str | None  # ISO timestamp of the last ATTEMPT, None if never run
    status: str                    # "ok" or "error: <msg>"
    record_count: int
    error_detail: str | None = None  # full error + traceback for the Info page
    # Last attempt that actually succeeded. Distinct from last_refreshed_at on
    # purpose: a throttle keyed off "last attempt" freezes a source for its whole
    # min_interval after a single failure, which is how margin_debt went quiet.
    last_success_at: str | None = None
    last_duration_ms: int | None = None
    # When the scheduler will try again after an error or a deferral. None
    # while healthy (the schedule's normal next run applies).
    next_attempt_at: str | None = None


class SourceRun(BaseModel):
    """One recorded execution of a source.

    Outcomes are ok | error | deferred. `skipped` only appears on rows written
    before the per-source scheduler existed: the schedule is now the only gate,
    so a scheduled run always fetches. `error_detail` keeps the full traceback
    for *this* run (source_status only holds the latest one).
    """

    id: int
    source: str
    started_at: str
    finished_at: str
    outcome: str        # "ok" | "error" | "deferred" (legacy rows: "skipped")
    duration_ms: int
    record_count: int
    detail: str | None = None
    error_detail: str | None = None
    next_attempt_at: str | None = None


class SourceSchedule(BaseModel):
    """When one source (or the derived boom_score -> alerts step) runs.

    mode "interval": every `interval_seconds`, optionally only on `days`.
    mode "times": at each HH:MM in `times`, on `days`, wall-clock in `tz`.
    `retry_seconds`: how soon to try again after an error (None -> default,
    see schedules.retry_after_seconds).
    """

    source: str
    mode: str = "interval"
    interval_seconds: int | None = None
    times: list[str] = []
    days: list[str] = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    tz: str = "UTC"
    enabled: bool = True
    retry_seconds: int | None = None
    updated_at: str | None = None


class JobRun(BaseModel):
    """A scheduler job lifecycle event (executed / error / missed / max_instances)."""

    id: int
    job_id: str
    event: str
    at: str
    duration_ms: int | None = None
    detail: str | None = None


class YieldPoint(BaseModel):
    date: str           # YYYY-MM-DD — PRIMARY KEY
    yr3m: float | None = None   # 3-month bill: the risk-free rate for Sharpe/Sortino
    yr2: float | None
    yr10: float | None
    yr30: float | None
    spread: float | None  # yr10 - yr2


class TechnicalSignal(BaseModel):
    ticker: str           # PRIMARY KEY
    fetched_at: str
    price: float | None
    change_pct: float | None
    ma50: float | None
    ma200: float | None
    golden_cross: bool | None   # True = ma50 > ma200
    rsi14: float | None
    high_52w: float | None
    low_52w: float | None
    prices_json: str              # JSON array of last 100 closes for sparkline
    macd: float | None = None
    macd_signal: float | None = None
    macd_crossover: bool | None = None
    rel_volume: float | None = None
    volume_json: str = "[]"       # JSON array of last 20 daily volumes


class LiveQuote(BaseModel):
    ticker: str
    price: float                # last 1m close incl. pre/post; fallback regularMarketPrice
    change_pct: float | None    # vs previous regular close
    previous_close: float | None
    market_state: str           # "PRE" | "LIVE" | "POST" | "CLOSED"
    regular_price: float | None = None       # meta.regularMarketPrice (session close)
    extended_change_pct: float | None = None  # PRE: vs prev close; POST: vs regular close
    fetched_at: str
    # "equity" | "fx". FX quotes come from the same endpoint but need different
    # display: more decimals, and they must not drive the market-open badge,
    # since currencies trade ~24/5 and would report REGULAR at 3am.
    kind: str = "equity"        # "equity" | "fx" | "index"
    label: str = ""             # display name; "USD/ILS" for USDILS=X, "TA-35"
    # Major-unit ISO currency of every price above ("ILS" for TASE — Yahoo's
    # ILA agorot are divided out in app/currency.py before this is built).
    currency: str | None = None
    market: str = "US"          # "US" | "TASE" | "FX" | "OTHER" (symbol convention)


class FearGreedSnapshot(BaseModel):
    captured_at: str   # ISO timestamp — PRIMARY KEY
    score: float       # 0–100
    rating: str        # "Extreme Fear" | "Fear" | "Neutral" | "Greed" | "Extreme Greed"


class VixPoint(BaseModel):
    date: str    # YYYY-MM-DD — PRIMARY KEY
    close: float


class AaiiSentiment(BaseModel):
    week_ending: str   # YYYY-MM-DD — PRIMARY KEY
    bullish: float     # percentages 0–100
    neutral: float
    bearish: float
    fetched_at: str


class PutCallPoint(BaseModel):
    date: str    # YYYY-MM-DD — PRIMARY KEY
    ratio: float  # 5-day average total put/call ratio


class MarginDebtPoint(BaseModel):
    month: str             # "YYYY-MM" — PRIMARY KEY
    debit_balances: float  # $ millions (FINRA margin account debit balances)


class EconEvent(BaseModel):
    event_id: str          # PRIMARY KEY — stable hash of date+country+event
    date: str              # YYYY-MM-DD (local release date)
    time: str              # "HH:MM" UTC, or "" when only a date is known
    country: str           # e.g. "United States"
    event: str             # e.g. "CPI (YoY)", "FOMC Rate Decision"
    importance: str        # "high" | "medium" | "low"
    importance_source: str # "fmp" (official) | "curated" (our keyword classification)
    actual: str | None     # released value, when available (kept as strings — units vary)
    forecast: str | None   # consensus / estimate
    previous: str | None
    source: str            # "fmp" | "nasdaq"
    fetched_at: str


class CongressTrade(BaseModel):
    trade_hash: str        # PRIMARY KEY (content hash)
    representative: str
    party: str             # "D", "R", "I", or ""
    state: str
    ticker: str
    asset_description: str
    transaction_date: str  # YYYY-MM-DD
    transaction_type: str  # "Purchase" | "Sale" | "Exchange"
    amount_range: str      # e.g. "$1,001 - $15,000"
    filed_at: str
    chamber: str           # "house" | "senate"
    doc_id: str | None = None      # the House Clerk PTR it came from
    source_url: str | None = None  # that PTR (or the aggregator's link)


class CongressFiling(BaseModel):
    """One House Periodic Transaction Report from the Clerk's index, and
    whether it has been read (each is downloaded once)."""
    doc_id: str
    chamber: str
    member: str
    district: str
    filed_at: str
    url: str
    status: str = "unread"     # unread | read | no rows (a scanned filing)
    rows: int = 0
    checked_at: str = ""


class ShortInterest(BaseModel):
    ticker: str                      # PRIMARY KEY
    fetched_at: str
    shares_short: int | None
    short_pct_float: float | None    # 0.15 = 15%
    days_to_cover: float | None      # short ratio (days to unwind)
    prior_month_shares: int | None
    squeeze_flag: bool               # short_pct_float > 0.15


class SocialSentiment(BaseModel):
    ticker: str           # PRIMARY KEY
    fetched_at: str
    mentions: int | None
    upvotes: int | None
    rank: int | None            # current rank (1 = most mentioned)
    rank_24h_ago: int | None
    rank_change: int | None     # rank_24h_ago - rank (positive = rising)


class AnalystSignal(BaseModel):
    ticker: str               # PRIMARY KEY
    fetched_at: str
    next_earnings: str | None  # ISO date YYYY-MM-DD
    rec_strong_buy: int | None
    rec_buy: int | None
    rec_hold: int | None
    rec_sell: int | None
    recent_upgrades: int       # count of "up" or "init" actions in last 30 days
    recent_downgrades: int     # count of "down" actions in last 30 days
    latest_action: str | None  # "up" | "down" | "init"
    latest_firm: str | None
    latest_to_grade: str | None  # e.g. "Buy", "Outperform"


class EarningsEvent(BaseModel):
    """One company earnings date — upcoming or already reported.

    `is_estimate` is load-bearing: Yahoo projects a forward date when the
    company hasn't confirmed one, and showing a guess as a fixture would be
    exactly the kind of false certainty this app avoids.
    """

    ticker: str            # PK part 1
    event_date: str        # PK part 2 — YYYY-MM-DD
    is_estimate: bool
    timing: str            # "bmo" | "amc" | "intraday" | "" when unknown
    eps_estimate: float | None = None
    eps_actual: float | None = None      # None until it has reported
    surprise_pct: float | None = None
    revenue_estimate: float | None = None
    quarter: str = ""      # e.g. "-1q" from Yahoo's history rows
    source: str = "yahoo"
    fetched_at: str = ""


class BoomScore(BaseModel):
    ticker: str         # PRIMARY KEY
    computed_at: str
    score: int          # can be negative (−90 … +100)
    components: str     # JSON dict of fired signals and their points (muted ones included)
    muted: str = "[]"   # JSON list of fired components not counted (auto-muted)
    # bullish signals
    golden_cross: bool
    rsi_recovery: bool
    insider_cluster_buy: bool
    congress_buy: bool
    short_squeeze: bool
    wsb_rising: bool
    analyst_upgrade: bool
    near_52w_high: bool = False
    macd_crossover: bool = False
    volume_confirmed: bool = False
    fear_greed_contrarian: bool = False
    yield_uninversion: bool = False
    contracts_catalyst: bool = False
    seasonal_tailwind: bool = False
    vix_spike_contrarian: bool = False
    aaii_bearish_extreme: bool = False
    put_call_fear: bool = False
    margin_debt_deleveraging: bool = False
    # bearish signals (fire when score is negative contribution)
    death_cross: bool = False
    insider_cluster_sell: bool = False
    overbought_rsi: bool = False
    congress_sale: bool = False
    analyst_downgrade_cluster: bool = False
    extreme_greed: bool = False
    aaii_bullish_euphoria: bool = False
    margin_debt_euphoria: bool = False
    # risk / meta flags
    earnings_soon: bool = False
    mixed_signals: bool = False
    # JSON list of component keys that cannot apply to this listing (e.g. SEC
    # insider / congress / federal-contract signals for a TASE stock) and a
    # plain-language note on how the score was renormalized because of it.
    not_applicable: str = "[]"
    score_note: str = ""


class Fundamentals(BaseModel):
    ticker: str           # PRIMARY KEY
    fetched_at: str
    sector: str | None
    industry: str | None
    pe_ratio: float | None
    forward_pe: float | None
    peg_ratio: float | None
    pb_ratio: float | None
    revenue_growth: float | None
    profit_margin: float | None
    market_cap: float | None
    # Company profile, parsed from the same Yahoo quoteSummary call. All
    # optional: rows written before these columns existed simply have none, and
    # a missing field renders as "—" rather than being invented.
    name: str | None = None
    website: str | None = None
    country: str | None = None
    city: str | None = None
    employees: int | None = None
    summary: str | None = None
    officers_json: str = ""        # JSON list of {name, title, age, pay}
    insider_pct: float | None = None      # fraction held by insiders (0-1)
    institution_pct: float | None = None  # fraction held by institutions (0-1)
    # ISO code the company reports its financials in (Yahoo financialData
    # .financialCurrency) — officer pay is in this currency, which for a TASE
    # listing can be ILS or USD independently of the ₪ share price.
    financial_currency: str | None = None


class SuggestionHistoryEntry(BaseModel):
    """A suggestion that was made for a watched or held ticker on a given day.

    Intentionally minimal — enough to answer "what did we say, and what did the
    stock do afterwards". The outcome itself is computed from stored bars at
    read time, not persisted.
    """
    user_id: int
    ticker: str
    for_date: str        # YYYY-MM-DD, the trading day the call was for
    kind: str            # "holding" | "watchlist"
    action: str
    price: float | None
    created_at: str
    model_version: str = "legacy"   # app/model_version.py; rows before versioning are legacy
    muted: str = "[]"               # JSON list of the signals muted for this user at the time


class CompanyHolder(BaseModel):
    """A single institutional holder of a ticker (Yahoo institutionOwnership)."""
    ticker: str
    holder: str
    kind: str = "institution"   # PK is (ticker, kind, holder)
    pct_held: float | None = None
    shares: float | None = None
    value: float | None = None
    reported_at: str = ""


class XPost(BaseModel):
    account: str        # handle without @, e.g. "realDonaldTrump"
    post_id: str        # provider post id (PK with account)
    text: str
    url: str
    posted_at: str      # ISO timestamp
    tickers: str        # "" or comma-joined detected cashtags/matches
    fetched_at: str


class Seasonality(BaseModel):
    ticker: str            # PRIMARY KEY
    computed_at: str
    as_of: str             # "MM-DD" anchor (server compute date)
    history_years: int     # distinct years with >=1 usable window value
    windows_json: str      # JSON list of window objects (key/label/kind/per_year)
    anchors_json: str = "[]"  # JSON list of {years_ago: 1|2|5|"max", date, close}


class Holding(BaseModel):
    ticker: str        # PRIMARY KEY with (user_id, account)
    shares: float
    avg_cost: float    # in `currency` (the position's native trading currency)
    added_at: str
    currency: str = "USD"  # ISO code; legacy rows predate the column -> USD


class Transaction(BaseModel):
    """One ledger row. `portfolio` and `cash_balances` are materialized from
    these (app/ledger.py). DEPOSIT/WITHDRAW rows carry the currency code as
    `ticker`, the amount as `qty` and price 1."""
    id: int | None = None
    user_id: int
    account: str = "manual"      # manual | paper | live
    ticker: str
    side: str                    # BUY | SELL | ADJUST | DEPOSIT | WITHDRAW
    qty: float                   # ADJUST: the position's new share count
    price: float                 # ADJUST: the position's new avg_cost
    fees: float = 0.0
    currency: str
    executed_at: str
    source: str = "manual"       # manual | migration | adjust | paper_fill | paper_init ...
    order_id: int | None = None
    cash_delta: float = 0.0      # exact cash movement this row caused, in `currency`
    realized_pnl: float | None = None  # SELL only, net of fees
    flow: float = 0.0            # external capital in (+) / out (-), for time-weighted return


class Bot(BaseModel):
    """A demo trading bot. It trades in its own ledger account ("bot:<id>"),
    funded by a transfer out of the owner's manual cash; `state` keeps the
    per-position stop / target / high-water mark and re-entry cooldowns."""
    id: int | None = None
    user_id: int
    name: str
    strategy: str                  # follow | trend | dip | breakout
    params: dict = {}
    tickers: list[str] = []        # explicit universe; empty -> watchlist / strategy default
    watchlist_id: int | None = None
    currency: str = "USD"
    allocated: float
    status: str = "active"         # active | paused | stopped
    state: dict = {}
    returned: float | None = None  # cash handed back on stop
    created_at: str
    stopped_at: str | None = None

    @property
    def account(self) -> str:
        return f"bot:{self.id}"


class BotEvent(BaseModel):
    id: int | None = None
    bot_id: int
    user_id: int
    at: str
    kind: str                      # buy | sell | skip | error | info
    ticker: str | None = None
    qty: float | None = None
    price: float | None = None
    reason: str


class CashBalance(BaseModel):
    currency: str
    balance: float


class EquitySnapshot(BaseModel):
    user_id: int
    account: str
    date: str                # YYYY-MM-DD (America/New_York)
    base_currency: str
    value_base: float        # invested_base + cash_base
    cash_base: float
    invested_base: float     # market value of positions
    flow_base: float         # net external flows since the previous day's snapshot
    computed_at: str


class Order(BaseModel):
    """One order (STC-41). Status: new → submitted → filled | partially_filled |
    cancelled | rejected. Paper orders rest as `submitted` until a quote
    crosses them; a bot's fill is priced by its engine and written filled."""
    id: int | None = None
    user_id: int
    account: str                  # paper | bot:<id> (live once a broker exists)
    ticker: str
    side: str                     # BUY | SELL
    qty: float
    type: str = "market"          # market | limit | stop
    limit_price: float | None = None
    stop_price: float | None = None
    tif: str = "day"              # day (expires after its session) | gtc
    status: str = "new"
    broker_order_id: str | None = None
    currency: str = "USD"
    created_at: str
    submitted_at: str | None = None
    filled_at: str | None = None
    fill_price: float | None = None
    fees: float = 0.0
    suggestion_id: int | None = None    # suggestion_history rowid it came from
    reference_price: float | None = None  # the price the decision was made at
    slippage_bps: float | None = None   # fill vs reference, + = cost to you
    source: str = "ticket"        # ticket | bot
    reason: str = ""              # why it was placed (bots), rejected or cancelled
    plan_stop: float | None = None    # the protective stop the entry is planned with
    plan_target: float | None = None  # and its target; both feed the per-trade limits
    filled_qty: float = 0.0           # a broker order's cumulative fill so far


class PositionRule(BaseModel):
    """A holding's exit rule: alerts first; orders once execution exists.
    Prices are in the holding's own currency (ILS, never agorot, for .TA)."""
    user_id: int
    account: str = "manual"
    ticker: str
    stop_price: float | None = None   # exclusive with stop_pct
    stop_pct: float | None = None     # % below the position's average cost
    take_profit: float | None = None
    trailing_pct: float | None = None
    trail_high: float | None = None   # highest live price since the trail began; only rises
    fired: list[str] = []             # kinds already alerted for this version of the rule
    active: bool = True
    created_at: str
    updated_at: str                   # a new value re-arms every kind


class SavedRule(BaseModel):
    """A user's saved trading rule (STC-67): its spec as JSON and the model
    version its signals and suggestions are tracked under."""
    id: int | None = None
    user_id: int
    name: str
    spec: str            # RuleSpec JSON
    model_version: str   # rules.version(spec)
    active: bool = True  # evaluated in the derived step
    variants_tested: int = 0   # read from rule_variants, never stored
    created_at: str
    updated_at: str


class RuleSignal(BaseModel):
    """A saved rule's entry or exit firing on a ticker's completed bar."""
    user_id: int
    rule_id: int
    ticker: str
    day: str             # the bar's date
    kind: str            # entry | exit
    model_version: str
    computed_at: str


class AuditEntry(BaseModel):
    """One append-only audit row (STC-52). Each user's rows form a hash chain:
    hash = sha256(prev_hash + the row's canonical content)."""
    id: int | None = None
    user_id: int
    at: str
    kind: str
    detail: str          # JSON
    prev_hash: str
    hash: str


class RegimeSnapshot(BaseModel):
    date: str            # YYYY-MM-DD — PRIMARY KEY; the day's latest computation
    label: str           # risk_on | neutral | risk_off | unknown
    score: int           # sum of the input votes
    inputs: str          # JSON list of {key, label, value, vote, detail, as_of}
    computed_at: str


class SignalPerformance(BaseModel):
    component: str
    horizon_days: int
    window_days: int
    n: int
    hits: int
    hit_rate: float | None
    mean_net: float | None   # mean net-of-cost return in the component's direction, %
    computed_at: str


class SignalMute(BaseModel):
    component: str
    muted: bool
    since: str | None
    hit_rate: float | None
    n: int
    reason: str
    updated_at: str


class RiskSettings(BaseModel):
    """Per-user risk switches. The drawdown kill switch is off while
    max_drawdown_pct is None; once tripped it holds until acknowledged.
    regime_gate: what a risk-off regime does to new buys (off | downgrade | pause)."""
    user_id: int
    max_drawdown_pct: float | None = None
    drawdown_tripped_at: str | None = None
    drawdown_pct: float | None = None          # the drawdown when it tripped
    drawdown_acknowledged_at: str | None = None
    regime_gate: str = "downgrade"
    # Order guardrails (STC-50), enforced in execution.submit for paper and bots.
    # Notional limits are in the user's base currency.
    max_order_notional: float = 25_000.0
    max_orders_per_day: int = 50
    max_notional_per_day: float = 100_000.0
    max_position_pct: float = 50.0
    allow_outside_hours: bool = False
    # Per-trade limits (STC-82), each off while None / False. They apply to
    # entries only: an order that just reduces a holding is never blocked.
    max_loss_per_trade: float | None = None      # loss at the plan stop, base currency
    max_daily_loss: float | None = None          # today's entries' losses at their stops
    min_reward_risk: float | None = None         # (target − entry) / (entry − stop)
    require_stop: bool = False
    no_entry_after_open_min: int | None = None   # minutes after the session opens
    no_entry_before_close_min: int | None = None # minutes before the session closes
    # Global kill switch (STC-53): set = every new order refused, bots paused.
    trading_halted_at: str | None = None
    trading_halt_reason: str | None = None
    updated_at: str = ""


class NotifyProfile(BaseModel):
    email: str | None = None
    phone: str | None = None       # E.164, e.g. +14155551234
    email_enabled: bool = False
    sms_enabled: bool = False
    # Position sizing (Trading & risk settings). risk_pct clamped 0.1–10 in the API.
    account_size: float | None = None   # in base_currency
    risk_pct: float = 1.0
    updated_at: str = ""
    # Currency the portfolio totals (and account_size) are expressed in.
    base_currency: str = "USD"          # "USD" | "ILS"


class AppSettings(BaseModel):
    """Single-row app settings (id=1). Times are wall-clock in analysis_tz."""
    analysis_time: str = "15:30"          # "HH:MM", 24h
    analysis_tz: str = "Asia/Jerusalem"   # IANA timezone name
    quotes_refresh_seconds: int = 30      # live-quote poll cadence, clamped 10–300 in the API
    x_accounts: list[str] = []            # monitored X handles (empty → env default)
    updated_at: str = ""


# ---------- Accounts & sessions ----------

class User(BaseModel):
    id: int
    email: str
    password_hash: str
    totp_secret: str | None = None
    totp_enabled: bool = False
    is_admin: bool = False
    created_at: str = ""
    onboarded: bool = False  # has this account seen the guided app tour?

    def public(self) -> dict:
        """The shape safe to return to the browser."""
        return {
            "id": self.id,
            "email": self.email,
            "is_admin": self.is_admin,
            "created_at": self.created_at,
            "onboarded": self.onboarded,
        }


class WatchlistMeta(BaseModel):
    """A named watchlist a user owns (its tickers live in the watchlist table)."""
    id: int
    name: str
    created_at: str = ""
    sort_order: int = 0


class OAuthIdentity(BaseModel):
    """Link between an external OAuth account and a local user."""
    provider: str            # "github" | "google" | "facebook"
    provider_user_id: str    # stable id at the provider (PK with provider)
    user_id: int
    email: str = ""
    created_at: str


class AuthSession(BaseModel):
    token_hash: str    # sha256 of the raw cookie token; raw value never stored
    user_id: int
    state: str         # 'totp_setup' | 'pending_totp' | 'active'
    created_at: str
    expires_at: str
    last_seen_at: str


# ---------- Portfolio technical analysis ----------

class OHLCBar(BaseModel):
    date: str          # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: float


class OHLCSeries(BaseModel):
    ticker: str
    interval: str      # "daily" | "weekly"  (PK is ticker+interval)
    bars_json: str     # JSON array of OHLCBar dicts, oldest first
    fetched_at: str


class PatternCriterion(BaseModel):
    """One requirement of a pattern, and whether price has met it yet."""

    name: str              # e.g. "price below neckline"
    met: bool
    detail: str = ""       # the actual vs required reading, in words


class PatternHit(BaseModel):
    name: str              # machine key, e.g. "cup_handle"
    label: str             # human, e.g. "Cup & Handle"
    direction: str         # "bullish" | "bearish" | "neutral"
    confidence: float      # 0..1
    pivots: list[dict]     # [{date, price, role}] — the points that define it
    measured_move: float | None = None  # classic projected target price, if any
    note: str = ""
    # "confirmed" = every requirement met, and the only kind that feeds
    # conviction. "forming" = the shape is on the chart but the trigger hasn't
    # happened yet — what the stock is heading towards, not what it has done.
    status: str = "confirmed"
    criteria: list[PatternCriterion] = []


class SRLevel(BaseModel):
    price: float
    kind: str              # "support" | "resistance"
    touches: int
    last_touch: str        # date
    source: str = "pivot"  # "pivot" | "round" | "gap" | "ma"


class GapEvent(BaseModel):
    date: str
    from_price: float      # prior close
    to_price: float        # open
    pct: float
    kind: str              # "up" | "down"
    filled: bool
    role: str = "common"           # "breakaway" | "runaway" | "exhaustion" | "common"
    acts_as: str | None = None     # "support" | "resistance" | None (when filled/overhead)
    note: str = ""                 # carries the ~90%-fill heuristic wording


class Evidence(BaseModel):
    """One explainable signal contributing to the read. A component with
    insufficient data is omitted entirely, never invented."""
    component: str         # methodology tag, e.g. "ma_structure", "breakout"
    signal: str            # "bullish" | "bearish" | "neutral"
    weight: int            # signed contribution to conviction
    detail: str            # the human sentence shown to the user
    data: dict = {}        # structured backup (levels, ratios, dates)


class CandleSignal(BaseModel):
    name: str              # hammer | shooting_star | doji | bullish_harami | bearish_harami
    label: str
    date: str
    direction: str         # "bullish" | "bearish" | "neutral"
    confidence: float      # 0..1
    note: str = ""


class TrendlineLevel(BaseModel):
    kind: str              # "support" | "resistance"
    touches: int
    confidence: float      # 0..1
    slope_per_bar: float
    pivots: list[dict]     # [{date, price, role}] — exactly two+ points for a line series
    current_value: float   # projected line value at the latest bar
    broken: bool = False


class VolumeRead(BaseModel):
    avg20: float
    last: float
    ratio: float           # last / avg20
    streak: int            # signed count of consecutive same-direction volume days
    state: str             # "expanding" | "contracting" | "flat"
    note: str = ""


class BreakoutState(BaseModel):
    direction: str         # "up" | "down"
    level: float
    level_source: str      # "horizontal" | "trendline" | "round" | "gap"
    status: str            # "approaching" | "broke_unconfirmed" | "confirmed" | "failed"
    volume_confirmed: bool = False
    note: str = ""


class TargetRung(BaseModel):
    r: float               # 3, 4, 5, 6
    price: float
    feasibility: str       # "base" | "likely" | "possible" | "unlikely"
    why: str


class StockAnalysis(BaseModel):
    ticker: str
    computed_at: str
    price: float | None
    # trend / moving averages
    ma20: float | None = None
    ma50: float | None = None
    ma150: float | None = None
    ma200: float | None = None
    ma_alignment: str = "mixed"   # "stacked_bull" | "stacked_bear" | "mixed"
    trend: str = "sideways"       # "up" | "down" | "sideways"
    atr14: float | None = None
    atr_pct: float | None = None  # atr / price
    # structure
    support: list[SRLevel] = []
    resistance: list[SRLevel] = []
    gaps: list[GapEvent] = []
    patterns: list[PatternHit] = []
    trendlines: list[TrendlineLevel] = []
    candles: list[CandleSignal] = []
    volume: VolumeRead | None = None      # None when bars carry no real volume
    # MA structure read (methodology A7/A8/A9)
    ma_state: str = "mixed"       # healthy_uptrend | topping | reclaiming | breaking_down | mixed
    ma_extension_atr: float | None = None  # (price − ma20) / ATR; mean-reversion magnet
    breakout: BreakoutState | None = None
    # trade plan
    recommendation: str = "hold"  # "buy" | "sell" | "hold" — headline label
    directive: str = "Hold"       # "Accumulate" | "Hold" | "Reduce" | "Avoid" (secondary detail)
    conviction: int = 0           # −100..+100
    entry: float | None = None
    stop: float | None = None
    stop_basis: str = ""          # "structure" | "atr" | "trendline"
    stop_atr: float | None = None
    stop_structure: float | None = None
    risk_per_share: float | None = None
    target: float | None = None   # 3R base target
    reward_per_share: float | None = None
    rr: float | None = None       # base reward:risk
    rr_threshold: float = 3.0     # professional R/R gate
    rr_pass: bool = False         # rr >= rr_threshold
    targets: list[TargetRung] = []
    staging_note: str = ""        # staged-entry guidance (methodology B3)
    suggested_shares: int | None = None
    account_size: float | None = None
    risk_pct: float | None = None
    # Sizing is like-for-like: account_size is in the user's base currency,
    # risk_per_share in the ticker's; apply_sizing converts one into the other.
    currency: str | None = None           # the ticker's trading currency
    account_currency: str | None = None   # the currency account_size is in
    sizing_note: str = ""                 # e.g. "FX unavailable — not sized"
    evidence: list[Evidence] = []
    reasons: list[str] = []
    disclaimer: str = "Rule-based technical read, not a prediction. Verify before trading."


class SuggestionLogEntry(BaseModel):
    created_at: str
    for_date: str                  # next trading day (YYYY-MM-DD)
    channel: str                   # "email" | "sms" | "alert"
    status: str                    # "sent" | "skipped: ..." | "error: ..."


class Alert(BaseModel):
    dedup_key: str          # stable id; UNIQUE → idempotent inserts
    created_at: str
    ticker: str
    type: str               # boom_cross | golden_cross | insider_cluster | earnings_soon | congress_buy
                            # | breakdown_warning | breakout_setup | breakout_confirmed
                            # | false_breakout | topping_formation | recommendation_change
    severity: str           # "high" | "medium"
    title: str
    message: str
    read: bool = False
    pushed: bool = False
    # None = a market event every account sees; set = one account's own alert
    # (an exit-rule hit, a risk trip), visible to and pushed to that user only.
    user_id: int | None = None


# ---------- Market-wide scanner (STC-69) ----------

class UniverseMember(BaseModel):
    """One constituent of a fetched index universe (sp500, ndx100, ta125)."""
    universe: str
    ticker: str
    name: str = ""
    refreshed_at: str


class IntradayQuote(BaseModel):
    """Today's session so far for one ticker, from Yahoo's batched quote API,
    in the listing's major currency."""
    ticker: str
    day: str                 # the session date (YYYY-MM-DD)
    price: float
    open: float
    high: float
    low: float
    volume: float
    previous_close: float | None = None
    market_state: str        # PRE | LIVE | POST | CLOSED
    currency: str | None = None
    fetched_at: str


class ScanHit(BaseModel):
    """A ticker matching a built-in scan in today's session. first_seen_at
    stays put; last_seen_at moves with every scan that still matches."""
    scan: str
    ticker: str
    day: str
    price: float
    change_pct: float | None = None
    metric: float | None = None
    first_seen_at: str
    last_seen_at: str


class ScannerPrefs(BaseModel):
    user_id: int
    alerts_enabled: bool = False
    per_hour: int = 3
    updated_at: str = ""


# ---------- Famous investors' 13F holdings ----------

class Filing13F(BaseModel):
    """A tracked filer's latest original 13F-HR."""
    cik: str
    filer: str
    person: str
    period: str            # the quarter's last day the holdings are as of
    filed: str
    accession: str
    prior_period: str | None = None
    positions: int
    total_value: float     # dollars
    checked_at: str


class Holding13F(BaseModel):
    """One position in a filer's latest 13F, and its change since the prior
    quarter. SOLD OUT rows are what the prior quarter held and this one doesn't."""
    cik: str
    cusip: str
    put_call: str = ""     # "" for shares, PUT / CALL for options
    issuer: str
    title: str
    ticker: str | None = None
    ticker_source: str = "unmatched"
    value: float           # dollars
    shares: float
    prev_shares: float = 0.0
    prev_value: float = 0.0
    pct: float             # of the filer's whole 13F
    change_pct: float | None = None
    status: str            # NEW | ADDED | TRIMMED | UNCHANGED | SOLD OUT
