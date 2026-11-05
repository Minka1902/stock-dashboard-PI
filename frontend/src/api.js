// Same-origin by default (single-port: the backend serves the built frontend).
// Set VITE_API_BASE to talk to a cross-origin backend (e.g. the Vite dev server
// proxies "" → :8000; see vite.config.js).
const BASE = import.meta.env.VITE_API_BASE || "";
// Cap every request so a stalled backend surfaces as a clean, predictable error
// (and drives the "Backend unreachable" banner) instead of hanging forever.
const REQUEST_TIMEOUT_MS = 20000;

// `fields` carries field-level messages ({ stop_price: "must be below …" })
// when the backend answers with detail = { message, errors }.
export class ApiError extends Error {
  constructor(message, status, fields = null) {
    super(message);
    this.status = status;
    this.fields = fields;
  }
}

// FastAPI's own 422s send detail as an array of { loc, msg }.
function detailMessage(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg).filter(Boolean).join("; ");
  return detail?.message || null;
}

async function request(path, { method = "GET", body } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      credentials: "include", // session cookie
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
  } catch (e) {
    // Abort (timeout) or a network-level failure ("Failed to fetch").
    throw new ApiError(
      e.name === "AbortError" ? `request timed out: ${path}` : (e.message || `network error: ${path}`),
      0,
    );
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    // A 401 outside the auth endpoints means the session expired mid-use:
    // tell useAuth so the app returns to the login screen cleanly.
    if (res.status === 401 && !path.startsWith("/api/auth/")) {
      window.dispatchEvent(new Event("auth:expired"));
    }
    const detail = (await res.json().catch(() => ({}))).detail;
    const fields = detail?.errors && Object.keys(detail.errors).length ? detail.errors : null;
    throw new ApiError(detailMessage(detail) || `request failed: ${path}`, res.status, fields);
  }
  return res.json();
}

const getJSON = (path) => request(path);

export const getContracts = () => getJSON("/api/contracts");
export const getSources = () => getJSON("/api/sources");
// Admin-only server introspection (the Server page).
export const getServerOverview = () => getJSON("/api/server/overview");
export const getServerSources = () => getJSON("/api/server/sources");
// `filter` = { kind: "source" | "job", id } narrows the log server-side
// ("Show similar"), so the page gets a full page of that one thing.
export const getServerEvents = (limit = 60, filter = null) => {
  const q = new URLSearchParams({ limit: String(limit) });
  if (filter?.kind) q.set("kind", filter.kind);
  if (filter?.id) q.set("id", filter.id);
  return getJSON(`/api/server/events?${q}`);
};
export const getServerSourceRuns = (source, limit = 10) =>
  getJSON(`/api/server/sources/${encodeURIComponent(source)}/runs?limit=${limit}`);
export const getServerSchedules = () => getJSON("/api/server/schedules");
export const putServerSchedule = (source, patch) =>
  request(`/api/server/schedules/${encodeURIComponent(source)}`, { method: "PUT", body: patch });
export const runScheduleNow = (source) =>
  request(`/api/server/schedules/${encodeURIComponent(source)}/run-now`, { method: "POST" });
export const getNews = () => getJSON("/api/news");
export const getTrades = () => getJSON("/api/trades");
export const getWatchlist = (listId) =>
  getJSON(`/api/watchlist${listId != null ? `?list_id=${encodeURIComponent(listId)}` : ""}`);
export const getYieldCurve = () => getJSON("/api/yield-curve");
export const getEconCalendar = () => getJSON("/api/econ-calendar");
export const getSignals = () => getJSON("/api/signals");
export const getFearGreed = () => getJSON("/api/fear-greed");
export const getVix = () => getJSON("/api/vix");
export const getAaii = () => getJSON("/api/aaii");
export const getPutCall = () => getJSON("/api/put-call");
export const getMarginDebt = () => getJSON("/api/margin-debt");
export const getSentiment = () => getJSON("/api/sentiment");
export const getQuotes = () => getJSON("/api/quotes");
export const getCongressTrades = () => getJSON("/api/congress-trades");
export const getShortInterest = () => getJSON("/api/short-interest");
export const getSocial = () => getJSON("/api/social");
export const getAnalyst = () => getJSON("/api/analyst");
export const getBoomScores = () => getJSON("/api/boom-scores");
export const getFundamentals = () => getJSON("/api/fundamentals");
export const getXPosts = () => getJSON("/api/x-posts");
export const getSeasonality = () => getJSON("/api/seasonality");
export const getAnalyses = () => getJSON("/api/analysis");
export const getAnalysis = (ticker) => getJSON(`/api/analysis/${encodeURIComponent(ticker)}`);
export const getBoomScoreHistory = (ticker) => getJSON(`/api/boom-scores/history/${encodeURIComponent(ticker)}`);
const accountQuery = (account) => (account && account !== "manual" ? `?account=${encodeURIComponent(account)}` : "");
export const getPortfolio = (account = "manual") => getJSON(`/api/portfolio${accountQuery(account)}`);
export const getCash = (account = "manual") => getJSON(`/api/portfolio/cash${accountQuery(account)}`);
export const getProfile = () => getJSON("/api/profile");
export const getSuggestions = () => getJSON("/api/suggestions");
export const getAppSettings = () => getJSON("/api/settings");
export const getChart = (ticker, interval, prepost = false) =>
  getJSON(`/api/chart/${encodeURIComponent(ticker)}?interval=${encodeURIComponent(interval)}${prepost ? "&prepost=1" : ""}`);
export const getSparklines = (tickers, range) =>
  getJSON(`/api/sparklines?tickers=${encodeURIComponent((tickers || []).join(","))}&range=${encodeURIComponent(range)}`);
export const analysisReportUrl = (ticker, { print = false } = {}) =>
  `${BASE}/api/analysis/${encodeURIComponent(ticker)}/report${print ? "?print=1" : ""}`;
export const getSuggestionLog = () => getJSON("/api/suggestions/log");
export const getSuggestionHistory = ({ ticker, months } = {}) => {
  const q = new URLSearchParams();
  if (ticker) q.set("ticker", ticker);
  if (months) q.set("months", months);
  const qs = q.toString();
  return getJSON(`/api/suggestions/history${qs ? `?${qs}` : ""}`);
};
// `version` picks a model version (the running one when omitted); the reply
// lists every version with data under `versions`.
export const getTrackRecord = (months = 12, version = null) =>
  getJSON(`/api/backtest/track-record?months=${months}${version ? `&version=${encodeURIComponent(version)}` : ""}`);
export const getSignalReplay = ({ horizon = 7, months = 12, version = null } = {}) =>
  getJSON(`/api/backtest/signal-replay?horizon=${horizon}&months=${months}${version ? `&version=${encodeURIComponent(version)}` : ""}`);
export const getOAuthProviders = () => getJSON("/api/auth/oauth/providers");
// "open" | "invite" | "closed" — decides whether to offer a sign-up tab at all.
export const getRegistrationMode = () => getJSON("/api/auth/registration");
export const oauthStartUrl = (provider) => `${BASE}/api/auth/oauth/${provider}/start`;
export const getAlerts = () => getJSON("/api/alerts");
export const getEarnings = ({ from, to, scope } = {}) => {
  const q = new URLSearchParams();
  if (from) q.set("date_from", from);
  if (to) q.set("date_to", to);
  if (scope) q.set("scope", scope);
  const qs = q.toString();
  return getJSON(`/api/earnings${qs ? `?${qs}` : ""}`);
};
export const getEarningsFor = (ticker) =>
  getJSON(`/api/earnings/${encodeURIComponent(ticker)}`);
// market: "all" | "us" | "tase" — one upstream search serves every filter.
export const searchStocks = (q, market = "all") =>
  getJSON(`/api/search?q=${encodeURIComponent(q)}${market && market !== "all" ? `&market=${market}` : ""}`);
export const getAnalyze = (ticker) =>
  getJSON(`/api/analyze/${encodeURIComponent(ticker)}`);
export const getCompany = (ticker) =>
  getJSON(`/api/company/${encodeURIComponent(ticker)}`);
export const getCompanyNames = () => getJSON("/api/company-names");
// Chart annotations, per user + ticker.
export const getDrawings = (ticker) =>
  getJSON(`/api/drawings/${encodeURIComponent(ticker)}`);
export const saveDrawings = (ticker, shapes) =>
  request(`/api/drawings/${encodeURIComponent(ticker)}`, { method: "PUT", body: { shapes } });

export const markAlertsRead = (payload = { all: true }) =>
  request("/api/alerts/read", { method: "POST", body: payload });
export const saveAppSettings = (settings) =>
  request("/api/settings", { method: "PUT", body: settings });
export const saveProfile = (profile) =>
  request("/api/profile", { method: "PUT", body: profile });
// avg_cost is in `currency` (the position's native one). currency null =
// let the server detect it (".TA" → ILS, bare → USD, else Yahoo).
// payFromCash debits cost + fees from that currency's cash balance.
export const addHolding = (ticker, shares, avg_cost, currency = null, { fees = 0, payFromCash = false } = {}) =>
  request("/api/portfolio", {
    method: "POST",
    body: { ticker, shares, avg_cost, fees, pay_from_cash: payFromCash, ...(currency ? { currency } : {}) },
  });
// toCash credits the proceeds (less fees) to that currency's cash balance.
export const sellHolding = (ticker, { shares, price, fees = 0, toCash = false }) =>
  request(`/api/portfolio/${encodeURIComponent(ticker)}/sell`, {
    method: "POST", body: { shares, price, fees, to_cash: toCash },
  });
export const moveCash = (currency, amount, side, account = "manual") =>
  request("/api/portfolio/cash", { method: "POST", body: { currency, amount, side, account } });
export const setCash = (currency, balance, account = "manual") =>
  request(`/api/portfolio/cash/${encodeURIComponent(currency)}`, {
    method: "PUT", body: { balance, account },
  });
// ---------- demo trading bots ----------
export const getBotStrategies = () => getJSON("/api/bots/strategies");
export const getBots = () => getJSON("/api/bots");
// body: { strategy, name, params, tickers | watchlist_id, amount, currency }.
// A 422 carries field errors on ApiError.fields ("amount", "params.fast", …).
export const createBot = (body) => request("/api/bots", { method: "POST", body });
export const pauseBot = (id) => request(`/api/bots/${id}/pause`, { method: "POST" });
export const resumeBot = (id) => request(`/api/bots/${id}/resume`, { method: "POST" });
export const stopBot = (id) => request(`/api/bots/${id}/stop`, { method: "POST" });
export const stopAllBots = () => request("/api/bots/stop-all", { method: "POST" });
export const getBotEvents = (id, limit = 100) => getJSON(`/api/bots/${id}/events?limit=${limit}`);

// ---------- exit rules & risk switches ----------
const rulePath = (ticker, account) =>
  `/api/portfolio/${encodeURIComponent(ticker)}/rules${accountQuery(account)}`;
// { account, items: [{ ticker, held, avg_cost, currency, price, price_source, rule, levels, suggested }] }
export const getExitRules = (account = "manual") => getJSON(`/api/portfolio/rules${accountQuery(account)}`);
export const getExitRule = (ticker, account = "manual") => getJSON(rulePath(ticker, account));
// body: { stop_price | stop_pct, take_profit, trailing_pct, active }. A 422
// carries field errors on ApiError.fields; returns the same shape as getExitRule.
export const saveExitRule = (ticker, body, account = "manual") =>
  request(rulePath(ticker, account), { method: "PUT", body });
export const deleteExitRule = (ticker, account = "manual") =>
  request(rulePath(ticker, account), { method: "DELETE" });
export const getRiskStatus = () => getJSON("/api/risk/status");
// body: { max_drawdown_pct } (null turns the kill switch off). Returns the status.
export const saveRiskSettings = (body) => request("/api/risk/settings", { method: "PUT", body });
export const acknowledgeDrawdown = () => request("/api/risk/drawdown/acknowledge", { method: "POST" });
// The global kill switch: halt cancels open paper orders and pauses every bot;
// release needs a fresh authenticator code. Both return the risk status.
export const haltTrading = (reason = "") => request("/api/risk/kill-switch", { method: "POST", body: { reason } });
export const releaseTrading = (code) => request("/api/risk/kill-switch/release", { method: "POST", body: { code } });
// { entries: [{ id, at, kind, detail (JSON), prev_hash, hash }], verify: { ok, n, broken_at, reason } }
export const getAudit = (limit = 200) => getJSON(`/api/audit?limit=${limit}`);
export const AUDIT_CSV_URL = `${BASE}/api/audit.csv`;
// Admin: { chains: [{ user_id, ok, n, broken_at, reason }] }
export const verifyAllAudit = () => getJSON("/api/server/audit/verify");
// ---------- rule builder ----------
// { indicators: [{ name, label, period, bounds }], ops, max_conditions, min_trades, in_sample }
export const getRuleSchema = () => getJSON("/api/rules/schema");
export const getSavedRules = () => getJSON("/api/rules");
// A 422 carries field errors keyed by the spec path ("entry.conditions.0...").
export const saveRule = (spec, active = true, id = null) =>
  request(id ? `/api/rules/${id}` : "/api/rules", { method: id ? "PUT" : "POST", body: { spec, active } });
export const deleteRule = (id) => request(`/api/rules/${id}`, { method: "DELETE" });
export const backtestRule = (spec, ticker) => request("/api/rules/backtest", { method: "POST", body: { spec, ticker } });

// ---------- paper account ----------
// { mode, modes, funded, cash, positions, open_orders, recent_orders, slippage, defaults }
export const getPaper = () => getJSON("/api/paper");
// body: { amount, currency } — funds a new paper account or starts it over.
export const resetPaper = (body = {}) => request("/api/paper/reset", { method: "POST", body });
// { price, price_source, currency, qty, stop, target, held, sizing_note, risk_pct }
export const getTicketDefaults = (ticker, side = "BUY") =>
  getJSON(`/api/paper/ticket-defaults?ticker=${encodeURIComponent(ticker)}&side=${side}`);
// body: { ticker, side, qty, type, limit_price, stop_price, tif, suggestion_for_date }.
// A 422 carries field errors on ApiError.fields.
export const placePaperOrder = (body) => request("/api/paper/orders", { method: "POST", body });
export const cancelPaperOrder = (id) => request(`/api/paper/orders/${id}/cancel`, { method: "POST" });
// { performance, exposure, correlation, risk_read } for "manual" | "paper" | "bot:<id>"
export const getPortfolioAnalytics = (account = "manual") =>
  getJSON(`/api/portfolio/analytics${accountQuery(account)}`);
// { items: [{ component, label, weight, auto_muted, hit_rate, n, reason, override, counts }], performance, rules }
export const getAutomute = () => getJSON("/api/automute");
// mode: "auto" (follow the auto-mute) | "on" (always count) | "off" (never count)
export const setMuteOverride = (component, mode) =>
  request(`/api/automute/${encodeURIComponent(component)}`, { method: "PUT", body: { mode } });

export const getTransactions = ({ account, ticker, limit } = {}) => {
  const q = new URLSearchParams();
  if (account) q.set("account", account);
  if (ticker) q.set("ticker", ticker);
  if (limit) q.set("limit", limit);
  const qs = q.toString();
  return getJSON(`/api/portfolio/transactions${qs ? `?${qs}` : ""}`);
};
export const updateHolding = (ticker, shares, avg_cost, currency = null) =>
  request(`/api/portfolio/${encodeURIComponent(ticker)}`, {
    method: "PUT",
    body: { shares, avg_cost, ...(currency ? { currency } : {}) },
  });
// Per-user FX pairs for the ticker carousel ("USDILS=X", …), in display order.
export const getFxWatch = () => getJSON("/api/fx-watch");
export const saveFxWatch = (pairs) =>
  request("/api/fx-watch", { method: "PUT", body: { pairs } });
// Live multipliers into `base`; a null rate means "FX unavailable".
export const getFxRates = (base, currencies = []) => {
  const q = new URLSearchParams();
  if (base) q.set("base", base);
  if (currencies.length) q.set("currencies", currencies.join(","));
  const qs = q.toString();
  return getJSON(`/api/fx/rates${qs ? `?${qs}` : ""}`);
};
export const setHoldingCategory = (ticker, category) =>
  request(`/api/portfolio/${encodeURIComponent(ticker)}/category`, {
    method: "PUT",
    body: { category },
  });
export const removeHolding = (ticker) =>
  request(`/api/portfolio/${encodeURIComponent(ticker)}`, { method: "DELETE" });
export const sendTestSuggestions = () =>
  request("/api/suggestions/send-test", { method: "POST" });
// Returns 202 { source, queued, status } — the work happens on a background
// executor server-side, so this resolving does NOT mean the refresh finished.
// Poll /api/sources (see useDashboardData.refreshOne) to observe completion.
// `force` bypasses the per-source throttle and is admin-only.
export const refreshSource = (name, { force = false } = {}) =>
  request(
    `/api/refresh/${encodeURIComponent(name)}${force ? "?force=1" : ""}`,
    { method: "POST" },
  );
export const addWatch = (ticker, note, listId) =>
  request("/api/watchlist", { method: "POST", body: { ticker, note, ...(listId != null ? { list_id: listId } : {}) } });
export const removeWatch = (ticker, listId) =>
  request(`/api/watchlist/${encodeURIComponent(ticker)}${listId != null ? `?list_id=${encodeURIComponent(listId)}` : ""}`, { method: "DELETE" });
// Move a ticker to another list and/or edit its note. Omit `toListId` to edit
// in place; omit `note` to keep the stored one. Returns the source list's items.
export const updateWatch = (ticker, { fromListId, toListId, note }) =>
  request(`/api/watchlist/${encodeURIComponent(ticker)}`, {
    method: "PATCH",
    body: {
      from_list_id: fromListId,
      ...(toListId != null ? { to_list_id: toListId } : {}),
      ...(note != null ? { note } : {}),
    },
  });
// Multiple named watchlists (Task 13).
export const getWatchlists = () => getJSON("/api/watchlists");
export const createWatchlist = (name) =>
  request("/api/watchlists", { method: "POST", body: { name } });
export const renameWatchlist = (id, name) =>
  request(`/api/watchlists/${encodeURIComponent(id)}`, { method: "PATCH", body: { name } });
export const deleteWatchlist = (id) =>
  request(`/api/watchlists/${encodeURIComponent(id)}`, { method: "DELETE" });

// ---------- auth ----------
export const getMe = () => getJSON("/api/auth/me");
export const register = (email, password, inviteCode) =>
  request("/api/auth/register", {
    method: "POST",
    body: { email, password, invite_code: inviteCode || "" },
  });
export const login = (email, password) =>
  request("/api/auth/login", { method: "POST", body: { email, password } });
export const totpSetup = () => getJSON("/api/auth/totp/setup");
export const totpEnable = (code) =>
  request("/api/auth/totp/enable", { method: "POST", body: { code } });
export const totpVerify = (code) =>
  request("/api/auth/totp/verify", { method: "POST", body: { code } });
export const useRecoveryCode = (code) =>
  request("/api/auth/recovery", { method: "POST", body: { code } });
export const markOnboarded = () =>
  request("/api/auth/onboarded", { method: "POST" });
export const logout = () => request("/api/auth/logout", { method: "POST" });

// ---------- in-app update ----------
// Status is readable by any signed-in user ("an update is available");
// apply is admin-only and answers 409 with the reason when it can't run.
export const getUpdateStatus = (refresh = false) =>
  getJSON(`/api/update/status${refresh ? "?refresh=true" : ""}`);
export const applyUpdate = () => request("/api/update/apply", { method: "POST" });
// Public liveness probe; its `commit` changes once a restarted server is
// running the updated code.
export const getHealth = () => getJSON("/api/health");

// ---------- chart workspace: extended-hours print + drawing drafts ----------
// Latest pre-market / after-hours print for the D/W/M chart's price lines.
export const getChartExtended = (ticker) =>
  getJSON(`/api/chart/${encodeURIComponent(ticker)}/extended`);
// Named per-user snapshots of a ticker's drawings.
export const listDrawingDrafts = (ticker) =>
  getJSON(`/api/drawings/${encodeURIComponent(ticker)}/drafts`);
export const createDrawingDraft = (ticker, { title, description = "", timeframe = "", shapes = [] }) =>
  request(`/api/drawings/${encodeURIComponent(ticker)}/drafts`, {
    method: "POST", body: { title, description, timeframe, shapes },
  });
export const updateDrawingDraft = (id, patch) =>
  request(`/api/drawings/drafts/${encodeURIComponent(id)}`, { method: "PUT", body: patch });
export const deleteDrawingDraft = (id) =>
  request(`/api/drawings/drafts/${encodeURIComponent(id)}`, { method: "DELETE" });

// Market-wide scanner (STC-69) and the server-sent events stream (STC-73).
export const getScanner = () => getJSON("/api/scanner");
export const saveScannerPrefs = (prefs) => request("/api/scanner/prefs", { method: "PUT", body: prefs });
export const streamUrl = () => `${BASE}/api/stream`;

// Telegram (STC-79): link a chat for alerts and read-only commands.
export const getTelegram = () => getJSON("/api/telegram");
export const linkTelegram = () => request("/api/telegram/link", { method: "POST" });
export const setTelegramEnabled = (enabled) => request("/api/telegram", { method: "PUT", body: { enabled } });
export const unlinkTelegram = () => request("/api/telegram", { method: "DELETE" });

// Famous investors' 13F holdings.
export const get13F = () => getJSON("/api/13f");
export const get13FHolders = (ticker) => getJSON(`/api/13f/${encodeURIComponent(ticker)}`);

// The Time Machine: replay a trade plan from the stock's own days.
export const runTimeMachine = (plan) => request("/api/timemachine", { method: "POST", body: plan });

// Tax lots, a sale check, target weights and the rebalance plan.
export const getLots = (account, minLoss) =>
  getJSON(`/api/portfolio/lots?account=${encodeURIComponent(account)}&min_loss=${minLoss}`);
export const checkSale = (body) => request("/api/portfolio/lots/sale", { method: "POST", body });
export const getTargets = (account) => getJSON(`/api/portfolio/targets?account=${encodeURIComponent(account)}`);
export const saveTargets = (account, targets) =>
  request("/api/portfolio/targets", { method: "PUT", body: { account, targets } });
export const getRebalance = (account, band, minTrade) =>
  getJSON(`/api/portfolio/rebalance?account=${encodeURIComponent(account)}&band=${band}&min_trade=${minTrade}`);

// The Alpaca broker account (paper first).
export const getBroker = () => getJSON("/api/broker");
export const placeBrokerOrder = (body) => request("/api/broker/orders", { method: "POST", body });
export const cancelBrokerOrder = (id) => request(`/api/broker/orders/${id}/cancel`, { method: "POST" });
export const syncBroker = () => request("/api/broker/sync", { method: "POST" });
