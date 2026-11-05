"""Demo trading bots: preset strategies trading simulated fills.

Each bot trades in a ledger account of its own ("bot:<id>"), funded by a cash
transfer out of the owner's manual account and handed back on stop, so every
fill, position and P&L goes through the same ledger as a manual trade.

Signals (entries and strategy exits) are read on *completed* daily closes, so
a bot changes its mind at most once a session and never sees a bar that
hasn't closed. Stops, targets and the fill price use the live quote. Fills
are the live price moved against the bot by config.BOT_COST_BPS. A bot only
acts on a ticker while that ticker's own exchange is in its regular session,
and only on a quote that is itself LIVE and no older than
config.ENGINE_QUOTE_MAX_AGE_SECONDS (quotes.tradable_price).

New entries wait while the owner's risk switches are tripped or a risk-off
regime is gated (risk.entry_blockers); exits and stops never do.

Nothing is guessed: a missing price or history is a logged skip, never an
estimated fill.
"""
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app import config, costs, currency, db, execution, quotes, regime, risk, rules
from app.market_calendar import exchange_for_ticker, market_status
from app.models import Bot, BotEvent, OHLCBar, Order, Transaction
from app.sources.technical import compute_ma, compute_rsi
from app.validation import TICKER_RE

logger = logging.getLogger(__name__)

STATUSES = ("active", "paused", "stopped")
MAX_TICKERS = 25

_COMMON = [
    {"key": "size_pct", "label": "Position size", "unit": "% of bot equity",
     "default": 20, "min": 1, "max": 100, "step": 1},
    {"key": "max_positions", "label": "Max open positions", "unit": "",
     "default": 5, "min": 1, "max": 20, "step": 1},
]
_STOP = {"key": "stop_pct", "label": "Stop loss", "unit": "% below entry",
         "default": 8, "min": 1, "max": 50, "step": 0.5}

STRATEGIES = {
    "follow": {
        "label": "Follow Suggestions",
        "summary": "Buys the names the Suggestions page rates a buy (with R/R ≥ 3), using "
                   "their stop and target. Sells at the stop, at the target, or when the "
                   "analysis turns to sell.",
        "needs_tickers": False,
        "params": _COMMON,
    },
    "trend": {
        "label": "Trend",
        "summary": "Buys while the fast moving average is above the slow one (an uptrend) and "
                   "sells when it falls back below, or at the stop.",
        "needs_tickers": True,
        "params": [
            {"key": "fast", "label": "Fast average", "unit": "days", "default": 20,
             "min": 5, "max": 100, "step": 1},
            {"key": "slow", "label": "Slow average", "unit": "days", "default": 50,
             "min": 10, "max": 250, "step": 1},
            _STOP, *_COMMON,
        ],
    },
    "dip": {
        "label": "Dip-buy",
        "summary": "Buys when RSI(14) shows the stock oversold and sells once it recovers, "
                   "or at the stop.",
        "needs_tickers": True,
        "params": [
            {"key": "rsi_buy", "label": "Buy at RSI ≤", "unit": "", "default": 30,
             "min": 5, "max": 50, "step": 1},
            {"key": "rsi_exit", "label": "Sell at RSI ≥", "unit": "", "default": 55,
             "min": 30, "max": 95, "step": 1},
            _STOP, *_COMMON,
        ],
    },
    "breakout": {
        "label": "Breakout",
        "summary": "Buys when a stock closes above its highest high of the lookback window and "
                   "rides it with a trailing stop under the highest price since.",
        "needs_tickers": True,
        "params": [
            {"key": "lookback", "label": "Breakout window", "unit": "days", "default": 20,
             "min": 5, "max": 120, "step": 1},
            {"key": "trail_pct", "label": "Trailing stop", "unit": "% below the high",
             "default": 10, "min": 1, "max": 50, "step": 0.5},
            *_COMMON,
        ],
    },
    "rule": {
        "label": "Custom rule",
        "summary": "Trades one of your saved rules (Rules tab): buys when its entry conditions hold on "
                   "a completed close, sells on its exit conditions, or at its stop, target or "
                   "trailing stop.",
        "needs_tickers": True,
        "params": [
            {"key": "rule_id", "label": "Saved rule", "unit": "", "default": 0, "min": 1,
             "max": 1_000_000_000, "step": 1, "choices": "saved_rules"},
            *_COMMON,
        ],
    },
}

_EXCHANGE_TZ = {"NYSE": ZoneInfo("America/New_York"), "TASE": ZoneInfo("Asia/Jerusalem")}
# (bot_id, ticker) -> last skip reason logged, so a standing condition is
# logged once rather than every minute.
_LAST_SKIP: dict[tuple[int, str], str] = {}


class BotError(ValueError):
    """A bot action that can't go ahead (e.g. no price to close a position at)."""


@dataclass(frozen=True)
class Decision:
    action: str | None   # "buy" | "sell" | None (hold)
    reason: str
    # Levels the signal itself sets (Follow: the analysis's). A stop_pct
    # strategy's stop is measured from the actual fill, so the engine sets it.
    stop: float | None = None
    target: float | None = None


def params_for(strategy: str, given: dict) -> dict:
    """The strategy's defaults overlaid with `given` (already validated)."""
    return {p["key"]: given.get(p["key"], p["default"]) for p in STRATEGIES[strategy]["params"]}


def _money(v: float) -> str:
    return f"{v:,.2f}"


def decide(strategy: str, p: dict, bars: list[OHLCBar], price: float,
           held: dict | None, analysis=None, rule: rules.RuleSpec | None = None) -> Decision:
    """What `strategy` does now, from completed daily `bars` (oldest first),
    the live `price` and the position's state (`held`: entry, stop, target,
    high; None when not held). `rule` is the saved rule a "rule" bot trades.
    Pure: no clock, no I/O."""
    closes = [b.close for b in bars]
    if strategy == "rule":
        return _decide_rule(rule, bars, price, held)
    if held is not None:
        stop, target = held.get("stop"), held.get("target")
        if stop is not None and price <= stop:
            return Decision("sell", f"Stop hit: {_money(price)} ≤ stop {_money(stop)}")
        if target is not None and price >= target:
            return Decision("sell", f"Target reached: {_money(price)} ≥ {_money(target)}")
        if strategy == "breakout":
            high = max(held.get("high") or price, price)
            trail = high * (1 - p["trail_pct"] / 100)
            if price <= trail:
                return Decision("sell", f"Trailing stop: {_money(price)} is {p['trail_pct']:g}% "
                                        f"below the high of {_money(high)}")
        elif strategy == "trend":
            fast, slow = compute_ma(closes, p["fast"]), compute_ma(closes, p["slow"])
            if fast is not None and slow is not None and fast < slow:
                return Decision("sell", f"Trend turned down: {p['fast']}-day average "
                                        f"{_money(fast)} fell below the {p['slow']}-day {_money(slow)}")
        elif strategy == "dip":
            rsi = compute_rsi(closes)
            if rsi is not None and rsi >= p["rsi_exit"]:
                return Decision("sell", f"RSI recovered to {rsi:.0f} (exit at ≥ {p['rsi_exit']:g})")
        elif strategy == "follow" and analysis is not None and analysis.recommendation == "sell":
            return Decision("sell", "The analysis turned to sell")
        return Decision(None, "hold")

    if strategy == "trend":
        fast, slow = compute_ma(closes, p["fast"]), compute_ma(closes, p["slow"])
        if fast is None or slow is None:
            return Decision(None, f"needs {p['slow']} days of history")
        if fast > slow:
            return Decision("buy", f"Uptrend: {p['fast']}-day average {_money(fast)} is above the "
                                   f"{p['slow']}-day {_money(slow)}")
        return Decision(None, "no uptrend")
    if strategy == "dip":
        rsi = compute_rsi(closes)
        if rsi is None:
            return Decision(None, "needs 15 days of history")
        if rsi <= p["rsi_buy"]:
            return Decision("buy", f"Oversold: RSI(14) {rsi:.0f} ≤ {p['rsi_buy']:g}")
        return Decision(None, "not oversold")
    if strategy == "breakout":
        n = int(p["lookback"])
        if len(bars) < n + 1:
            return Decision(None, f"needs {n + 1} days of history")
        prior_high = max(b.high for b in bars[-n - 1:-1])
        if bars[-1].close > prior_high:
            return Decision("buy", f"Breakout: closed at {_money(bars[-1].close)}, above the "
                                   f"{n}-day high of {_money(prior_high)}")
        return Decision(None, "no breakout")
    # follow
    if analysis is None:
        return Decision(None, "no stored analysis")
    if analysis.recommendation == "buy" and analysis.rr_pass and analysis.stop and analysis.stop < price:
        return Decision("buy", f"Suggestions rate it a buy (conviction {analysis.conviction}, "
                               f"R/R {analysis.rr:.1f})", stop=analysis.stop, target=analysis.target)
    return Decision(None, "not a buy")


def _decide_rule(rule: rules.RuleSpec | None, bars: list[OHLCBar], price: float,
                 held: dict | None) -> Decision:
    if rule is None:
        return Decision(None, "its saved rule no longer exists")
    if len(bars) < 2:
        return Decision(None, "needs more history")
    arr = rules.arrays(bars)
    if held is not None:
        stop, target = held.get("stop"), held.get("target")
        if stop is not None and price <= stop:
            return Decision("sell", f"Stop hit: {_money(price)} ≤ stop {_money(stop)}")
        if target is not None and price >= target:
            return Decision("sell", f"Target reached: {_money(price)} ≥ {_money(target)}")
        if rule.trail_pct:
            high = max(held.get("high") or price, price)
            if price <= high * (1 - rule.trail_pct / 100):
                return Decision("sell", f"Trailing stop: {_money(price)} is {rule.trail_pct:g}% or more "
                                        f"below the high of {_money(high)}")
        if rule.exit is not None and rules.evaluate(rule.exit, arr)[-1]:
            return Decision("sell", f"Rule \"{rule.name}\" exit conditions held on the {bars[-1].date} close")
        return Decision(None, "hold")
    if rules.evaluate(rule.entry, arr)[-1]:
        return Decision("buy", f"Rule \"{rule.name}\" entry conditions held on the {bars[-1].date} close",
                        stop=price * (1 - rule.stop_pct / 100) if rule.stop_pct else None,
                        target=price * (1 + rule.target_pct / 100) if rule.target_pct else None)
    return Decision(None, "no entry")


def fill_price(price: float, side: str) -> float:
    """The simulated fill: the quote moved against the bot by the cost model."""
    return costs.apply(price, side, config.BOT_COST_BPS)


def _local_today(ticker: str, now: datetime):
    return now.astimezone(_EXCHANGE_TZ.get(exchange_for_ticker(ticker), timezone.utc)).date()


def _is_live(ticker: str, now: datetime) -> bool:
    exchange = exchange_for_ticker(ticker)
    return exchange is not None and market_status(now, exchange) == "LIVE"


def _rank(analysis) -> tuple:
    """Follow's entry order: highest conviction, then R/R, like Suggestions."""
    if analysis is None:
        return (1, 0, 0)
    return (0, -analysis.conviction, -(analysis.rr or 0))


def universe(conn, bot: Bot) -> list[str]:
    """The tickers a bot may buy: its own list, else a watchlist, else (Follow
    Suggestions) the same pool Suggestions draws from. Only listings in the
    bot's currency — it holds cash in exactly one."""
    if bot.tickers:
        names = bot.tickers
    elif bot.watchlist_id is not None:
        names = [w.ticker for w in db.get_watchlist(conn, bot.user_id, bot.watchlist_id)]
    else:
        # Like Suggestions, nothing you already hold by hand: a bot buying it
        # too would only double the exposure.
        held = {h.ticker for h in db.get_portfolio(conn, bot.user_id)}
        names = sorted((set(db.get_watched_tickers_for_user(conn, bot.user_id))
                        | set(db.get_signal_candidate_tickers(conn, config.OPPORTUNITY_CANDIDATES)))
                       - held)
    return [t for t in names if currency.currency_for_symbol(t) == bot.currency]


def _skip(conn, bot: Bot, ticker: str | None, reason: str, at: str) -> None:
    key = (bot.id, ticker)
    if _LAST_SKIP.get(key) == reason:
        return
    _LAST_SKIP[key] = reason
    db.add_bot_event(conn, BotEvent(bot_id=bot.id, user_id=bot.user_id, at=at, kind="skip",
                                    ticker=ticker, reason=reason))


def _run_bot(conn, bot: Bot, names: list[str], live: set, by_ticker: dict, now: datetime,
             halted: list[str] = ()) -> int:
    at = now.isoformat(timespec="seconds")
    p = params_for(bot.strategy, bot.params)
    old_cooldown = bot.state.get("cooldown", {})
    # A cooldown only ever blocks a same-day rebuy, so earlier days are dropped.
    cooldown = {t: d for t, d in old_cooldown.items() if d >= _local_today(t, now).isoformat()}
    state = {"pos": dict(bot.state.get("pos", {})), "cooldown": cooldown}
    positions = {h.ticker: h for h in db.get_portfolio(conn, bot.user_id, bot.account)}
    cash = next((c.balance for c in db.get_cash(conn, bot.user_id, bot.account)
                 if c.currency == bot.currency), 0.0)
    analyses = ({t: db.get_analysis(conn, t) for t in {*names, *positions}}
                if bot.strategy == "follow" else {})
    saved = db.get_saved_rule(conn, int(p["rule_id"])) if bot.strategy == "rule" else None
    spec = rules.RuleSpec.model_validate_json(saved.spec) if saved and saved.user_id == bot.user_id else None
    trails = bot.strategy == "breakout" or (spec is not None and spec.trail_pct)
    if analyses:
        names = sorted(names, key=lambda t: _rank(analyses.get(t)))

    def valuation_of(t):
        q = by_ticker.get(t)
        return q.price if q and q.price else None

    equity = cash + sum(h.shares * (valuation_of(t) or h.avg_cost) for t, h in positions.items())
    fills = 0
    dirty = len(cooldown) != len(old_cooldown)

    def trade(side, ticker, qty, px, quoted, new_state, reason, stop=None, target=None):
        """Send the fill through execution.submit (its checks apply to bots
        too) and write it with the bot's state. "rejected" when a check
        refused it, None when the bot stopped meanwhile."""
        def write(order):
            tx = Transaction(user_id=bot.user_id, account=bot.account, ticker=ticker, side=side,
                             qty=qty, price=px, currency=bot.currency, executed_at=at, source="bot")
            return db.bot_trade(conn, bot, tx, new_state, BotEvent(
                bot_id=bot.id, user_id=bot.user_id, at=at, kind=side.lower(), ticker=ticker,
                qty=qty, price=px, reason=reason), order)

        order = Order(user_id=bot.user_id, account=bot.account, ticker=ticker, side=side, qty=qty,
                      currency=bot.currency, created_at=at, fill_price=px, reference_price=quoted,
                      source="bot", reason=reason, plan_stop=stop, plan_target=target)
        try:
            done = execution.submit(conn, order, now, fill=write)
        except execution.OrderRejected as exc:
            _skip(conn, bot, ticker, f"{ticker}: {side.lower()} refused — {exc.reason}", at)
            return "rejected"
        _LAST_SKIP.pop((bot.id, ticker), None)
        return done

    for t, h in list(positions.items()):
        if t not in live:
            continue
        price, why = quotes.tradable_price(by_ticker.get(t), now)
        if price is None:
            _skip(conn, bot, t, f"{t}: {why} — holding until a fresh one arrives", at)
            continue
        if t not in state["pos"]:
            state["pos"][t] = {"entry": h.avg_cost}
            dirty = True
        held = state["pos"][t]
        if trails and price > (held.get("high") or 0):
            held["high"] = price
            dirty = True
        bars = [b for b in db.get_ohlc(conn, t, "daily") if b.date < _local_today(t, now).isoformat()]
        d = decide(bot.strategy, p, bars, price, held, analyses.get(t), spec)
        if d.action != "sell":
            continue
        px = fill_price(price, "SELL")
        new_state = {"pos": {k: v for k, v in state["pos"].items() if k != t},
                     "cooldown": {**state["cooldown"], t: _local_today(t, now).isoformat()}}
        done = trade("SELL", t, h.shares, px, price, new_state,
                     f"Sold {h.shares:g} {t} at {_money(px)} — {d.reason}")
        if done is None:
            return fills
        if done == "rejected":
            continue
        state = new_state
        fills += 1
        dirty = False
        cash += h.shares * px
        del positions[t]

    holding = set(positions)
    if halted:
        # Exits above still ran; only new entries wait.
        _skip(conn, bot, None, f"New entries paused — {halted[0]}", at)
        names = []
    for t in names:
        if len(holding) >= int(p["max_positions"]):
            break
        if t in holding or t not in live or state["cooldown"].get(t) == _local_today(t, now).isoformat():
            continue
        price, why = quotes.tradable_price(by_ticker.get(t), now)
        if price is None:
            _skip(conn, bot, t, f"{t}: {why}", at)
            continue
        bars = [b for b in db.get_ohlc(conn, t, "daily") if b.date < _local_today(t, now).isoformat()]
        d = decide(bot.strategy, p, bars, price, None, analyses.get(t), spec)
        if d.action != "buy":
            if d.reason.startswith("needs") or d.reason in ("no stored analysis", "its saved rule no longer exists"):
                _skip(conn, bot, t, f"{t}: {d.reason}", at)
            continue
        px = fill_price(price, "BUY")
        budget = min(equity * p["size_pct"] / 100, cash)
        qty = math.floor(budget / px)
        if qty < 1:
            _skip(conn, bot, t, f"{t}: buy signal, but {_money(budget)} {bot.currency} "
                                f"buys less than one share at {_money(px)}", at)
            continue
        stop = d.stop if d.stop is not None else (
            px * (1 - p["stop_pct"] / 100) if "stop_pct" in p else None)
        new_state = {"pos": {**state["pos"], t: {"entry": px, "stop": stop, "target": d.target, "high": price}},
                     "cooldown": state["cooldown"]}
        stop_note = f" Stop {_money(stop)}." if stop else ""
        target_note = f" Target {_money(d.target)}." if d.target else ""
        done = trade("BUY", t, qty, px, price, new_state,
                     f"Bought {qty} {t} at {_money(px)} — {d.reason}.{stop_note}{target_note}",
                     stop=stop, target=d.target)
        if done is None:
            return fills
        if done == "rejected":
            continue
        state = new_state
        fills += 1
        dirty = False
        cash -= qty * px
        holding.add(t)

    if dirty:
        db.set_bot_state(conn, bot.id, state)
    return fills


def run(conn, now: datetime | None = None, quote_getter=None) -> int:
    """One engine pass over every active bot. Returns the number of fills."""
    now = now or datetime.now(timezone.utc)
    bots = db.get_bots(conn, status="active")
    if not bots:
        return 0
    names = {b.id: universe(conn, b) for b in bots}
    held = {b.id: [h.ticker for h in db.get_portfolio(conn, b.user_id, b.account)] for b in bots}
    tickers = {t for b in bots for t in (*names[b.id], *held[b.id])}
    live = {t for t in tickers if _is_live(t, now)}
    if not live:
        return 0
    by_ticker = {q.ticker: q for q in (quote_getter or quotes.get_quotes)(sorted(live))}
    reg = regime.latest(conn)
    halts = {uid: risk.entry_blockers(conn, uid, reg) for uid in {b.user_id for b in bots}}
    fills = 0
    for bot in bots:
        try:
            fills += _run_bot(conn, bot, names[bot.id], live, by_ticker, now, halts[bot.user_id])
        except Exception as exc:  # noqa: BLE001 - one bot's failure must not stop the others
            logger.exception("bot %s failed", bot.id)
            db.add_bot_event(conn, BotEvent(
                bot_id=bot.id, user_id=bot.user_id, at=now.isoformat(timespec="seconds"),
                kind="error", reason=f"{type(exc).__name__}: {str(exc)[:200]}"))
    return fills


def stop(conn, bot: Bot, now: datetime | None = None, quote_getter=None) -> float:
    """Close every position (live price, else the last daily close, said so in
    the event) and hand all the bot's cash back to manual. Returns the amount
    returned. Raises BotError when a position has no price at all."""
    now = now or datetime.now(timezone.utc)
    positions = db.get_portfolio(conn, bot.user_id, bot.account)
    by_ticker = {q.ticker: q for q in (quote_getter or quotes.get_quotes)(
        [h.ticker for h in positions])} if positions else {}
    fills = []
    for h in positions:
        q = by_ticker.get(h.ticker)
        if q and q.price:
            px, where = fill_price(q.price, "SELL"), "the live price"
        else:
            bars = db.get_ohlc(conn, h.ticker, "daily")
            if not bars:
                raise BotError(f"no price for {h.ticker} — try again once quotes are back")
            px, where = fill_price(bars[-1].close, "SELL"), f"the {bars[-1].date} close (no live quote)"
        fills.append((h.ticker, h.shares, px, f"Sold {h.shares:g} {h.ticker} at {_money(px)} — "
                                              f"bot stopped, closed at {where}"))
    returned = db.stop_bot(conn, bot, fills, now.isoformat(timespec="seconds"))
    if returned is None:
        raise BotError("this bot is already stopped")
    return returned


def validate_new(conn, user_id: int, body: dict) -> tuple[Bot | None, dict]:
    """(Bot to create, {}) or (None, {field: message})."""
    errors: dict[str, str] = {}
    strategy = body.get("strategy")
    spec = STRATEGIES.get(strategy)
    if spec is None:
        return None, {"strategy": f"pick one of: {', '.join(s['label'] for s in STRATEGIES.values())}"}

    name = str(body.get("name") or spec["label"]).strip()[:60]
    params = {}
    for p in spec["params"]:
        raw = (body.get("params") or {}).get(p["key"], p["default"])
        try:
            v = float(raw)
        except (TypeError, ValueError):
            v = math.nan
        if not math.isfinite(v) or not p["min"] <= v <= p["max"]:
            errors[f"params.{p['key']}"] = f"{p['label']} must be between {p['min']:g} and {p['max']:g}"
            continue
        params[p["key"]] = int(v) if float(p["step"]).is_integer() else v
    if strategy == "trend" and "fast" in params and "slow" in params and params["fast"] >= params["slow"]:
        errors["params.fast"] = "The fast average must be shorter than the slow one"
    if strategy == "dip" and "rsi_buy" in params and "rsi_exit" in params \
            and params["rsi_buy"] >= params["rsi_exit"]:
        errors["params.rsi_exit"] = "Sell level must be above the buy level"
    if strategy == "rule":
        saved = db.get_saved_rule(conn, int(params.get("rule_id") or 0))
        if saved is None or saved.user_id != user_id:
            errors["params.rule_id"] = "Pick one of your saved rules (Rules tab)"

    ccy = str(body.get("currency") or "").strip().upper()
    cash = {c.currency: c.balance for c in db.get_cash(conn, user_id)}
    if ccy not in cash or cash[ccy] <= 0:
        errors["currency"] = "You have no cash in that currency — add some on the Portfolio page"
    try:
        amount = float(body.get("amount"))
    except (TypeError, ValueError):
        amount = math.nan
    if not math.isfinite(amount) or amount <= 0:
        errors["amount"] = "Enter an amount above 0"
    elif ccy in cash and amount > cash[ccy] + 1e-9:
        errors["amount"] = f"You have {_money(cash[ccy])} {ccy} cash available"

    raw_tickers = body.get("tickers") or []
    if isinstance(raw_tickers, str):
        raw_tickers = re.split(r"[\s,]+", raw_tickers)
    tickers = []
    for raw in raw_tickers:
        t = str(raw).strip().upper()
        if not t:
            continue
        if not TICKER_RE.fullmatch(t):
            errors["tickers"] = f"{raw!r} isn't a ticker symbol"
            break
        if ccy and currency.currency_for_symbol(t) not in (None, ccy):
            errors["tickers"] = f"{t} trades in {currency.currency_for_symbol(t)}; this bot holds {ccy}"
            break
        if t not in tickers:
            tickers.append(t)
    if len(tickers) > MAX_TICKERS:
        errors["tickers"] = f"At most {MAX_TICKERS} tickers per bot"

    watchlist_id = body.get("watchlist_id")
    if watchlist_id is not None:
        if watchlist_id not in {w.id for w in db.get_watchlists(conn, user_id)}:
            errors["watchlist_id"] = "Pick one of your watchlists"
        elif tickers:
            errors["tickers"] = "Use either a ticker list or a watchlist, not both"
    if spec["needs_tickers"] and not tickers and watchlist_id is None and "tickers" not in errors:
        errors["tickers"] = "Add the tickers this bot trades, or pick a watchlist"

    running = [b for b in db.get_bots(conn, user_id) if b.status != "stopped"]
    if len(running) >= config.BOT_MAX_BOTS:
        errors["name"] = f"You already run {len(running)} bots (the limit is {config.BOT_MAX_BOTS})"

    if errors:
        return None, errors
    return Bot(
        user_id=user_id, name=name, strategy=strategy, params=params, tickers=tickers,
        watchlist_id=watchlist_id, currency=ccy, allocated=round(amount, 2),
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    ), {}


def view(conn, bot: Bot, by_ticker: dict, events: int = 20) -> dict:
    """The bot as the page shows it: cash, positions at live prices, P&L."""
    positions = []
    invested = 0.0
    priced = True
    for h in db.get_portfolio(conn, bot.user_id, bot.account):
        q = by_ticker.get(h.ticker)
        price = q.price if q and q.price else None
        value = h.shares * price if price is not None else None
        priced &= value is not None
        invested += value if value is not None else h.shares * h.avg_cost
        st = bot.state.get("pos", {}).get(h.ticker, {})
        positions.append({
            "ticker": h.ticker, "shares": h.shares, "avg_cost": h.avg_cost, "price": price,
            "value": value,
            "pl_pct": round((price / h.avg_cost - 1) * 100, 2) if price is not None and h.avg_cost else None,
            "stop": st.get("stop"), "target": st.get("target"), "high": st.get("high"),
        })
    cash = next((c.balance for c in db.get_cash(conn, bot.user_id, bot.account)
                 if c.currency == bot.currency), 0.0)
    if bot.status == "stopped":
        equity = bot.returned or 0.0
    else:
        equity = cash + invested
    pnl = equity - bot.allocated
    return {
        **bot.model_dump(exclude={"state"}),
        "strategy_label": STRATEGIES[bot.strategy]["label"],
        "params": params_for(bot.strategy, bot.params),
        "universe": universe(conn, bot) if bot.status != "stopped" else [],
        "cash": round(cash, 2),
        "positions": positions,
        "equity": round(equity, 2),
        "equity_priced": priced,
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl / bot.allocated * 100, 2) if bot.allocated else None,
        "events": [e.model_dump() for e in db.get_bot_events(conn, bot.id, events)],
    }
