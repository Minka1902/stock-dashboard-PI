"""Demo trading bots: strategy decisions, the engine, allocation and stop."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import bots, db, ledger
from app.models import Bot, LiveQuote, OHLCBar, OHLCSeries, StockAnalysis, WatchItem
from tests.conftest import authenticate

LIVE = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)    # Tue 11:00 New York
CLOSED = datetime(2026, 10, 27, 3, 0, tzinfo=timezone.utc)   # Mon 23:00 New York


def _bars(closes, end=date(2026, 10, 26), highs=None):
    """Daily bars ending on `end` (the last completed session before LIVE)."""
    out, d = [], end
    for i, c in enumerate(reversed(closes)):
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        h = (list(reversed(highs))[i] if highs else c)
        out.append(OHLCBar(date=d.isoformat(), open=c, high=h, low=c, close=c, volume=1e6))
        d -= timedelta(days=1)
    return list(reversed(out))


def _seed_bars(conn, ticker, bars):
    db.upsert_ohlc(conn, [OHLCSeries(ticker=ticker, interval="daily",
                                     bars_json=json.dumps([b.model_dump() for b in bars]),
                                     fetched_at="t")])


def _quote(ticker, price, market_state="LIVE", fetched_at=LIVE):
    return LiveQuote(ticker=ticker, price=price, regular_price=price, change_pct=0.0,
                     previous_close=price, market_state=market_state,
                     fetched_at=fetched_at.isoformat(timespec="seconds"),
                     currency="ILS" if ticker.endswith(".TA") else "USD")


def _quotes(prices):
    return lambda tickers: [_quote(t, prices[t]) for t in tickers if prices.get(t)]


UP = [100 + i for i in range(60)]          # steady uptrend: 20-day avg > 50-day avg
DOWN = [160 - i for i in range(60)]


@pytest.fixture(autouse=True)
def _fresh_skip_log():
    # Bot ids restart at 1 in every test DB, so the engine's skip dedup would
    # otherwise carry one test's last skip into the next.
    bots._LAST_SKIP.clear()


@pytest.fixture
def user(conn):
    u = db.create_user(conn, "bot@x.co", "h", "t")
    ledger.record(conn, u.id, ticker="USD", side="DEPOSIT", qty=10_000, currency_code="USD")
    return u


def _make_bot(conn, user_id, strategy="trend", tickers=("AAPL",), amount=10_000, **params):
    bot, errors = bots.validate_new(conn, user_id, {
        "strategy": strategy, "tickers": list(tickers), "amount": amount, "currency": "USD",
        "params": params})
    assert errors == {}, errors
    return db.create_bot(conn, bot)


def _cash(conn, user_id, account="manual"):
    return {c.currency: round(c.balance, 4) for c in db.get_cash(conn, user_id, account)}


# ---------- pure strategy decisions ----------

P = {s: bots.params_for(s, {}) for s in bots.STRATEGIES}


def test_trend_buys_in_an_uptrend_with_a_stop_and_sells_when_it_turns():
    d = bots.decide("trend", P["trend"], _bars(UP), 160.0, None)
    assert d.action == "buy" and "20-day average" in d.reason
    assert d.stop is None  # a percentage stop is set from the fill, by the engine
    d = bots.decide("trend", P["trend"], _bars(DOWN), 100.0, {"entry": 150, "stop": 90})
    assert d.action == "sell" and d.reason.startswith("Trend turned down")


def test_trend_waits_for_enough_history():
    d = bots.decide("trend", P["trend"], _bars(UP[:30]), 130.0, None)
    assert d.action is None and d.reason == "needs 50 days of history"


def test_stop_beats_every_other_rule():
    d = bots.decide("trend", P["trend"], _bars(UP), 91.0, {"entry": 100, "stop": 92})
    assert d.action == "sell" and d.reason.startswith("Stop hit")


def test_dip_buys_oversold_and_sells_on_recovery():
    d = bots.decide("dip", P["dip"], _bars(DOWN), 100.0, None)
    assert d.action == "buy" and d.reason.startswith("Oversold")
    d = bots.decide("dip", P["dip"], _bars(UP), 160.0, {"entry": 120})
    assert d.action == "sell" and d.reason.startswith("RSI recovered")


def test_breakout_buys_above_the_window_high_and_trails_the_high():
    flat = [100.0] * 25 + [104.0]
    d = bots.decide("breakout", P["breakout"], _bars(flat, highs=[101.0] * 25 + [104.0]), 104.5, None)
    assert d.action == "buy" and "20-day high of 101.00" in d.reason
    d = bots.decide("breakout", P["breakout"], _bars(flat), 107.0, {"entry": 104, "high": 120.0})
    assert d.action == "sell" and d.reason.startswith("Trailing stop")
    d = bots.decide("breakout", P["breakout"], _bars(flat), 110.0, {"entry": 104, "high": 120.0})
    assert d.action is None


def test_follow_uses_the_analysis_stop_and_target():
    a = StockAnalysis(ticker="AAPL", computed_at="t", price=100.0, recommendation="buy", rr_pass=True,
                      rr=3.4, conviction=4, stop=90.0, target=130.0)
    d = bots.decide("follow", P["follow"], [], 100.0, None, a)
    assert (d.action, d.stop, d.target) == ("buy", 90.0, 130.0)
    d = bots.decide("follow", P["follow"], [], 131.0, {"entry": 100, "stop": 90, "target": 130}, a)
    assert d.action == "sell" and d.reason.startswith("Target reached")
    sell = a.model_copy(update={"recommendation": "sell"})
    d = bots.decide("follow", P["follow"], [], 110.0, {"entry": 100, "stop": 90, "target": 130}, sell)
    assert d.action == "sell"
    assert bots.decide("follow", P["follow"], [], 100.0, None, None).action is None


def test_costs_move_both_sides_against_the_bot_equally():
    buy, sell = bots.fill_price(100.0, "BUY"), bots.fill_price(100.0, "SELL")
    assert buy - 100 == pytest.approx(100 - sell) == pytest.approx(0.1)


# ---------- allocation ----------

def test_creating_a_bot_moves_its_cash_out_of_manual(conn, user):
    bot = _make_bot(conn, user.id, amount=4_000)
    assert _cash(conn, user.id) == {"USD": 6_000}
    assert _cash(conn, user.id, bot.account) == {"USD": 4_000}
    sides = [(t.account, t.side, t.source) for t in db.get_transactions(conn, user.id)]
    assert sides[:2] == [(bot.account, "DEPOSIT", "bot_allocate"), ("manual", "WITHDRAW", "bot_allocate")]


def test_a_bot_cant_take_more_cash_than_there_is(conn, user):
    _, errors = bots.validate_new(conn, user.id, {
        "strategy": "trend", "tickers": ["AAPL"], "amount": 10_001, "currency": "USD"})
    assert "10,000.00 USD" in errors["amount"]
    bot = Bot(user_id=user.id, name="x", strategy="trend", currency="USD", allocated=10_001,
              created_at="t")
    with pytest.raises(ledger.LedgerError):
        db.create_bot(conn, bot)
    assert db.get_bots(conn, user.id) == [] and _cash(conn, user.id) == {"USD": 10_000}


def test_validation_names_every_bad_field(conn, user):
    _, errors = bots.validate_new(conn, user.id, {
        "strategy": "trend", "tickers": "TEVA.TA, aapl", "amount": -1, "currency": "USD",
        "params": {"fast": 60, "slow": 50, "stop_pct": 99}})
    assert set(errors) == {"amount", "tickers", "params.fast", "params.stop_pct"}
    _, errors = bots.validate_new(conn, user.id, {"strategy": "dip", "amount": 5, "currency": "USD"})
    assert "tickers" in errors
    assert bots.validate_new(conn, user.id, {"strategy": "nope"})[1].keys() == {"strategy"}


# ---------- engine ----------

def test_engine_buys_sized_to_equity_and_never_twice(conn, user):
    _seed_bars(conn, "AAPL", _bars(UP))
    bot = _make_bot(conn, user.id, size_pct=20)
    get = _quotes({"AAPL": 100.0})
    assert bots.run(conn, LIVE, get) == 1
    assert bots.run(conn, LIVE, get) == 0
    pos = db.get_portfolio(conn, user.id, bot.account)
    assert [(h.ticker, h.shares, h.avg_cost) for h in pos] == [("AAPL", 19, 100.1)]  # 2000 / 100.10
    assert _cash(conn, user.id, bot.account) == {"USD": round(10_000 - 19 * 100.1, 4)}
    buy = db.get_bot_events(conn, bot.id)[0]
    assert buy.kind == "buy" and "Uptrend" in buy.reason and "Stop 92.09" in buy.reason


def test_engine_does_nothing_while_the_market_is_closed(conn, user):
    _seed_bars(conn, "AAPL", _bars(UP))
    _make_bot(conn, user.id)
    assert bots.run(conn, CLOSED, _quotes({"AAPL": 100.0})) == 0


def test_a_missing_price_is_a_single_skip_not_a_guess(conn, user):
    _seed_bars(conn, "AAPL", _bars(UP))
    bot = _make_bot(conn, user.id)
    bots.run(conn, LIVE, _quotes({}))
    bots.run(conn, LIVE, _quotes({}))
    events = db.get_bot_events(conn, bot.id)
    assert [(e.kind, e.reason) for e in events] == [("skip", "AAPL: no live price")]


def test_max_positions_caps_the_entries(conn, user):
    for t in ("AAPL", "MSFT", "NVDA"):
        _seed_bars(conn, t, _bars(UP))
    bot = _make_bot(conn, user.id, tickers=("AAPL", "MSFT", "NVDA"), max_positions=2)
    bots.run(conn, LIVE, _quotes({"AAPL": 100.0, "MSFT": 100.0, "NVDA": 100.0}))
    assert len(db.get_portfolio(conn, user.id, bot.account)) == 2


def test_a_non_live_or_stale_quote_is_a_skip_not_a_fill(conn, user):
    _seed_bars(conn, "AAPL", _bars(UP))
    bot = _make_bot(conn, user.id)
    assert bots.run(conn, LIVE, lambda ts: [_quote("AAPL", 100.0, market_state="POST")]) == 0
    old = LIVE - timedelta(seconds=bots.config.ENGINE_QUOTE_MAX_AGE_SECONDS + 1)
    assert bots.run(conn, LIVE, lambda ts: [_quote("AAPL", 100.0, fetched_at=old)]) == 0
    reasons = [e.reason for e in db.get_bot_events(conn, bot.id)]
    assert reasons[1] == "AAPL: the quote says the market is POST"
    assert reasons[0].startswith("AAPL: the quote is ") and reasons[0].endswith("s old")
    assert db.get_portfolio(conn, user.id, bot.account) == []


def test_a_stale_quote_never_trips_a_stop(conn, user):
    _seed_bars(conn, "AAPL", _bars(UP))
    bot = _make_bot(conn, user.id)
    bots.run(conn, LIVE, _quotes({"AAPL": 100.0}))
    old = LIVE - timedelta(seconds=bots.config.ENGINE_QUOTE_MAX_AGE_SECONDS + 1)
    assert bots.run(conn, LIVE, lambda ts: [_quote("AAPL", 50.0, fetched_at=old)]) == 0
    assert [h.ticker for h in db.get_portfolio(conn, user.id, bot.account)] == ["AAPL"]


def _follow_setup(conn, user, picks):
    for t, conviction, rr in picks:
        db.add_watch(conn, user.id, WatchItem(ticker=t, note="", added_at="t"))
        db.upsert_analyses(conn, [StockAnalysis(
            ticker=t, computed_at="t", price=100.0, recommendation="buy", rr_pass=True,
            rr=rr, conviction=conviction, stop=90.0, target=130.0)])
    return _make_bot(conn, user.id, strategy="follow", tickers=(), max_positions=1)


def test_follow_buys_the_highest_conviction_first(conn, user):
    bot = _follow_setup(conn, user, [("AAPL", 50, 3.1), ("MSFT", 70, 3.5), ("NVDA", 70, 3.2)])
    bots.run(conn, LIVE, _quotes({"AAPL": 100.0, "MSFT": 100.0, "NVDA": 100.0}))
    assert [h.ticker for h in db.get_portfolio(conn, user.id, bot.account)] == ["MSFT"]


def test_follow_skips_names_already_held_by_hand(conn, user):
    bot = _follow_setup(conn, user, [("AAPL", 50, 3.1), ("MSFT", 70, 3.5)])
    ledger.record(conn, user.id, ticker="MSFT", side="BUY", qty=1, price=100.0, currency_code="USD")
    assert "MSFT" not in bots.universe(conn, db.get_bot(conn, bot.id))
    bots.run(conn, LIVE, _quotes({"AAPL": 100.0, "MSFT": 100.0}))
    assert [h.ticker for h in db.get_portfolio(conn, user.id, bot.account)] == ["AAPL"]


def test_cooldowns_from_earlier_days_are_dropped(conn, user):
    _seed_bars(conn, "AAPL", _bars(UP))
    bot = _make_bot(conn, user.id, tickers=("AAPL", "MSFT"))
    db.set_bot_state(conn, bot.id, {"pos": {}, "cooldown": {"AAPL": "2026-10-26", "MSFT": "2026-10-27"}})
    bots.run(conn, LIVE, _quotes({}))
    assert db.get_bot(conn, bot.id).state["cooldown"] == {"MSFT": "2026-10-27"}


def test_typed_bot_tickers_join_the_analysis_universe_until_the_bot_stops(conn, user):
    bot = _make_bot(conn, user.id, tickers=("ZZZZ",))
    assert "ZZZZ" in db.get_analysis_universe(conn)
    bots.stop(conn, db.get_bot(conn, bot.id), LIVE, _quotes({}))
    assert "ZZZZ" not in db.get_analysis_universe(conn)


def test_paused_and_stopped_bots_dont_trade(conn, user):
    _seed_bars(conn, "AAPL", _bars(UP))
    bot = _make_bot(conn, user.id)
    db.set_bot_status(conn, bot.id, "paused", ("active",))
    assert bots.run(conn, LIVE, _quotes({"AAPL": 100.0})) == 0
    db.set_bot_status(conn, bot.id, "active", ("paused",))
    bots.stop(conn, db.get_bot(conn, bot.id), LIVE, _quotes({}))
    assert bots.run(conn, LIVE, _quotes({"AAPL": 100.0})) == 0


def test_a_stopped_out_ticker_is_not_rebought_the_same_day(conn, user):
    _seed_bars(conn, "AAPL", _bars(UP))
    bot = _make_bot(conn, user.id, stop_pct=5)
    bots.run(conn, LIVE, _quotes({"AAPL": 100.0}))
    assert bots.run(conn, LIVE + timedelta(minutes=1), _quotes({"AAPL": 90.0})) == 1
    assert bots.run(conn, LIVE + timedelta(minutes=2), _quotes({"AAPL": 99.0})) == 0
    assert db.get_portfolio(conn, user.id, bot.account) == []
    sell = db.get_bot_events(conn, bot.id)[0]
    assert sell.kind == "sell" and sell.price == pytest.approx(89.91) and "Stop hit" in sell.reason


def test_todays_bar_never_reaches_the_signal(conn, user):
    """No look-ahead: a bar for the session in progress (or any later one) is
    ignored, so a wild print in it changes nothing."""
    _seed_bars(conn, "AAPL", _bars(DOWN))
    bot = _make_bot(conn, user.id)
    bars = _bars(DOWN) + [OHLCBar(date="2026-10-27", open=1e4, high=1e4, low=1e4, close=1e4,
                                  volume=1)]
    _seed_bars(conn, "AAPL", bars)
    assert bots.run(conn, LIVE, _quotes({"AAPL": 100.0})) == 0
    assert db.get_portfolio(conn, user.id, bot.account) == []


def test_one_failing_bot_doesnt_stop_the_others(conn, user, monkeypatch):
    _seed_bars(conn, "AAPL", _bars(UP))
    first = _make_bot(conn, user.id, amount=5_000)
    _make_bot(conn, user.id, amount=5_000)
    real = bots._run_bot

    def flaky(conn_, bot, *a):
        if bot.id == first.id:
            raise RuntimeError("boom")
        return real(conn_, bot, *a)

    monkeypatch.setattr(bots, "_run_bot", flaky)
    assert bots.run(conn, LIVE, _quotes({"AAPL": 100.0})) == 1
    assert db.get_bot_events(conn, first.id)[0].kind == "error"


# ---------- stop ----------

def test_stop_closes_positions_and_returns_cash_with_the_pnl(conn, user):
    _seed_bars(conn, "AAPL", _bars(UP))
    bot = _make_bot(conn, user.id, amount=10_000)
    bots.run(conn, LIVE, _quotes({"AAPL": 100.0}))
    returned = bots.stop(conn, db.get_bot(conn, bot.id), LIVE, _quotes({"AAPL": 110.0}))
    expected = 10_000 - 19 * 100.1 + 19 * 109.89
    assert returned == pytest.approx(expected)
    assert _cash(conn, user.id) == {"USD": round(expected, 4)}
    assert db.get_portfolio(conn, user.id, bot.account) == []
    stopped = db.get_bot(conn, bot.id)
    assert stopped.status == "stopped" and stopped.returned == round(expected, 2)
    with pytest.raises(bots.BotError, match="already stopped"):
        bots.stop(conn, stopped, LIVE, _quotes({}))
    positions, cash = ledger.replay(db.get_transactions(conn, user.id, limit=10_000))
    assert positions == {} and cash[("manual", "USD")] == pytest.approx(expected)


def test_stop_without_a_live_quote_uses_the_last_close_and_says_so(conn, user):
    _seed_bars(conn, "AAPL", _bars(UP))
    bot = _make_bot(conn, user.id)
    bots.run(conn, LIVE, _quotes({"AAPL": 100.0}))
    bots.stop(conn, db.get_bot(conn, bot.id), LIVE, _quotes({}))
    sell = next(e for e in db.get_bot_events(conn, bot.id) if e.kind == "sell")
    assert "2026-10-26 close (no live quote)" in sell.reason


def test_view_reports_equity_and_pnl(conn, user):
    _seed_bars(conn, "AAPL", _bars(UP))
    bot = _make_bot(conn, user.id)
    bots.run(conn, LIVE, _quotes({"AAPL": 100.0}))
    v = bots.view(conn, db.get_bot(conn, bot.id), {"AAPL": _quote("AAPL", 110.0)})
    assert v["equity"] == pytest.approx(10_000 - 19 * 100.1 + 19 * 110, abs=0.01)
    assert v["positions"][0]["stop"] == pytest.approx(100.1 * 0.92)
    assert v["strategy_label"] == "Trend" and v["events"][0]["kind"] == "buy"


# ---------- API ----------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKS_DB_PATH", str(tmp_path / "bots.db"))
    import importlib

    from app import config, main as main_module, quotes
    importlib.reload(config)
    importlib.reload(main_module)
    monkeypatch.setattr(quotes, "get_quotes", lambda tickers, ttl_seconds=None: [])
    with TestClient(main_module.app) as c:
        authenticate(c)
        c.post("/api/portfolio/cash", json={"currency": "USD", "amount": 5000, "side": "deposit"})
        yield c


def test_api_create_list_pause_resume_stop(client):
    assert {s["key"] for s in client.get("/api/bots/strategies").json()["strategies"]} == {
        "follow", "trend", "dip", "breakout", "rule"}
    r = client.post("/api/bots", json={"strategy": "trend", "tickers": "AAPL, MSFT",
                                       "amount": 2000, "currency": "USD"})
    assert r.status_code == 200, r.text
    bot = r.json()["bots"][0]
    assert (bot["status"], bot["cash"], bot["universe"]) == ("active", 2000.0, ["AAPL", "MSFT"])
    assert client.get("/api/portfolio/cash").json() == [{"currency": "USD", "balance": 3000.0}]
    assert client.post(f"/api/bots/{bot['id']}/pause").json()["bots"][0]["status"] == "paused"
    assert client.post(f"/api/bots/{bot['id']}/pause").status_code == 409
    assert client.post(f"/api/bots/{bot['id']}/resume").json()["bots"][0]["status"] == "active"
    stopped = client.post(f"/api/bots/{bot['id']}/stop").json()["bots"][0]
    assert (stopped["status"], stopped["returned"], stopped["pnl"]) == ("stopped", 2000.0, 0.0)
    assert client.get("/api/portfolio/cash").json() == [{"currency": "USD", "balance": 5000.0}]
    assert client.post(f"/api/bots/{bot['id']}/stop").status_code == 409


def test_api_returns_field_errors(client):
    r = client.post("/api/bots", json={"strategy": "trend", "amount": 9999, "currency": "USD",
                                       "params": {"fast": 80, "slow": 20}})
    assert r.status_code == 422
    errors = r.json()["detail"]["errors"]
    assert {"amount", "tickers", "params.fast"} <= set(errors)


def test_api_bots_are_private(client):
    client.post("/api/bots", json={"strategy": "follow", "amount": 1000, "currency": "USD"})
    bot_id = client.get("/api/bots").json()["bots"][0]["id"]
    client.post("/api/auth/logout")
    authenticate(client, email="other@example.com")
    assert client.get("/api/bots").json()["bots"] == []
    assert client.post(f"/api/bots/{bot_id}/stop").status_code == 404
    assert client.get(f"/api/bots/{bot_id}/events").status_code == 404


def test_every_bot_fill_is_a_filled_order_linked_to_its_ledger_row(conn, user):
    _seed_bars(conn, "AAPL", _bars(UP))
    bot = _make_bot(conn, user.id)
    bots.run(conn, LIVE, _quotes({"AAPL": 100.0}))
    [order] = db.get_orders(conn, user.id, bot.account)
    assert (order.status, order.side, order.qty, order.fill_price, order.slippage_bps, order.source) == (
        "filled", "BUY", 19, 100.1, 10.0, "bot")
    tx = next(t for t in db.get_transactions(conn, user.id, account=bot.account) if t.side == "BUY")
    assert tx.order_id == order.id
