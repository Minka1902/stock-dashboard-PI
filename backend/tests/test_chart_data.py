"""Tests for the on-demand pro-chart bars (app/chart_data.py)."""
import pytest

from app import chart_data


def _payload(timestamps, closes, tz_offset=0):
    n = len(timestamps)
    return {
        "chart": {
            "result": [{
                "timestamp": timestamps,
                "indicators": {"quote": [{
                    "open": [c - 1 for c in closes],
                    "high": [c + 1 for c in closes],
                    "low": [c - 2 for c in closes],
                    "close": list(closes),
                    "volume": [1000] * n,
                }]},
            }]
        }
    }


def test_parse_chart_bars_intraday_keeps_epoch_time():
    bars = chart_data.parse_chart_bars(_payload([1751800000, 1751800060], [10.0, 10.5]), intraday=True)
    assert [b["time"] for b in bars] == [1751800000, 1751800060]
    assert bars[1]["close"] == 10.5
    assert bars[0]["high"] == 11.0


def test_parse_chart_bars_daily_uses_dates_and_dedupes():
    # two timestamps on the same UTC date (regular bar + live re-quote) -> one bar
    same_day = [1751500800, 1751522400]
    bars = chart_data.parse_chart_bars(_payload(same_day, [10.0, 10.7]), intraday=False)
    assert len(bars) == 1
    assert bars[0]["time"] == "2025-07-03"
    assert bars[0]["close"] == 10.7  # the later bar wins


def test_parse_chart_bars_drops_null_bars():
    payload = _payload([1751800000, 1751800060], [10.0, 10.5])
    payload["chart"]["result"][0]["indicators"]["quote"][0]["close"][1] = None
    bars = chart_data.parse_chart_bars(payload, intraday=True)
    assert len(bars) == 1


def test_parse_chart_bars_empty_payload():
    assert chart_data.parse_chart_bars({}, intraday=True) == []


def test_get_bars_caches_per_ticker_interval(monkeypatch):
    calls = []

    def stub_fetch(ticker, interval, prepost=False):
        calls.append((ticker, interval))
        return {"bars": [{"time": "2026-07-03", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}],
                "session": None}

    monkeypatch.setattr(chart_data, "fetch_chart", stub_fetch)
    chart_data._cache.clear()

    a = chart_data.get_bars("aapl", "1d")
    b = chart_data.get_bars("AAPL", "1d")  # case-insensitive cache hit
    assert a["ticker"] == "AAPL" and a["bars"] and a == b
    assert calls == [("AAPL", "1d")]

    chart_data.get_bars("AAPL", "5m")  # different interval -> new fetch
    assert calls == [("AAPL", "1d"), ("AAPL", "5m")]
    chart_data._cache.clear()


def test_get_bars_prepost_is_separate_cache_key(monkeypatch):
    calls = []

    def stub_fetch(ticker, interval, prepost=False):
        calls.append((ticker, interval, prepost))
        return {"bars": [{"time": 1751800000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}],
                "session": None}

    monkeypatch.setattr(chart_data, "fetch_chart", stub_fetch)
    chart_data._cache.clear()

    chart_data.get_bars("AAPL", "5m")               # prepost=False
    chart_data.get_bars("AAPL", "5m", prepost=True)  # distinct key -> new fetch
    chart_data.get_bars("AAPL", "5m", prepost=True)  # cache hit
    assert calls == [("AAPL", "5m", False), ("AAPL", "5m", True)]
    chart_data._cache.clear()


def test_fetch_bars_includes_prepost_only_for_intraday(monkeypatch):
    captured = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"chart": {"result": []}}

    class _Client:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None):
            captured["params"] = params
            return _Resp()

    monkeypatch.setattr(chart_data.httpx, "Client", _Client)

    chart_data.fetch_bars("AAPL", "5m", prepost=True)
    assert captured["params"]["includePrePost"] == "true"

    chart_data.fetch_bars("AAPL", "1d", prepost=True)  # daily: no extended hours
    assert captured["params"]["includePrePost"] == "false"


def test_get_bars_does_not_cache_empty(monkeypatch):
    calls = []

    def stub_fetch(ticker, interval, prepost=False):
        calls.append(1)
        return {"bars": [], "session": None}

    monkeypatch.setattr(chart_data, "fetch_chart", stub_fetch)
    chart_data._cache.clear()
    assert chart_data.get_bars("X", "1d")["bars"] == []
    assert chart_data.get_bars("X", "1d")["bars"] == []
    assert len(calls) == 2  # empty result was not cached
    chart_data._cache.clear()


def test_unknown_interval_raises_keyerror():
    with pytest.raises(KeyError):
        chart_data.get_bars("AAPL", "42h")


# ---- extended-hours session tagging (Task 3) ----

# One NY day as Yahoo reports it with includePrePost=true (EDT, gmtoffset -4h).
_PRE = {"start": 1790323200, "end": 1790343000, "gmtoffset": -14400}   # 04:00-09:30
_REG = {"start": 1790343000, "end": 1790366400, "gmtoffset": -14400}   # 09:30-16:00
_POST = {"start": 1790366400, "end": 1790380800, "gmtoffset": -14400}  # 16:00-20:00


def _session_payload(timestamps, meta):
    p = _payload(timestamps, [10.0 + i for i in range(len(timestamps))])
    p["chart"]["result"][0]["meta"] = meta
    return p


def _ny_meta(**extra):
    return {
        "gmtoffset": -14400, "exchangeTimezoneName": "America/New_York",
        "hasPrePostMarketData": True,
        "currentTradingPeriod": {"pre": _PRE, "regular": _REG, "post": _POST},
        "tradingPeriods": {"pre": [[_PRE]], "regular": [[_REG]], "post": [[_POST]]},
        **extra,
    }


def test_intraday_bars_are_tagged_with_their_session():
    ts = [_PRE["start"] + 60, _REG["start"], _REG["end"] - 300, _POST["start"] + 60]
    bars = chart_data.parse_chart_bars(_session_payload(ts, _ny_meta()), intraday=True)
    assert [b["session"] for b in bars] == ["pre", "regular", "regular", "post"]


def test_regular_only_trading_periods_list_form():
    # includePrePost=false: tradingPeriods is a bare list of regular spans.
    meta = _ny_meta(tradingPeriods=[[_REG]])
    bars = chart_data.parse_chart_bars(_session_payload([_REG["start"] + 60], meta), intraday=True)
    assert bars[0]["session"] == "regular"


def test_session_falls_back_to_time_of_day_of_the_latest_period():
    # A bar from the previous day, outside every explicit span, is placed by
    # the exchange's own hours projected onto its day.
    day_before_pre = _PRE["start"] - 86400 + 120
    meta = _ny_meta(tradingPeriods=None)
    bars = chart_data.parse_chart_bars(_session_payload([day_before_pre], meta), intraday=True)
    assert bars[0]["session"] == "pre"


def test_daily_bars_are_never_session_tagged():
    bars = chart_data.parse_chart_bars(_session_payload([_REG["start"]], _ny_meta()), intraday=False)
    assert "session" not in bars[0]


def test_no_metadata_means_no_tag_rather_than_a_guess():
    bars = chart_data.parse_chart_bars(_payload([_REG["start"]], [10.0]), intraday=True)
    assert "session" not in bars[0]


def test_session_info_reports_extended_and_regular_hours():
    info = chart_data.parse_session_info(_session_payload([], _ny_meta()))
    assert info["has_extended"] is True
    assert info["timezone"] == "America/New_York"
    assert info["regular"] == {"start_min": 570, "end_min": 960}


def test_tase_has_no_extended_session():
    # TASE reports zero-length pre/post spans and hasPrePostMarketData=false.
    reg = {"start": 1790319000, "end": 1790346600, "gmtoffset": 10800}
    zero_pre = {"start": reg["start"], "end": reg["start"], "gmtoffset": 10800}
    zero_post = {"start": reg["end"], "end": reg["end"], "gmtoffset": 10800}
    meta = {"gmtoffset": 10800, "exchangeTimezoneName": "Asia/Jerusalem",
            "hasPrePostMarketData": False,
            "currentTradingPeriod": {"pre": zero_pre, "regular": reg, "post": zero_post},
            "tradingPeriods": {"pre": [[zero_pre]], "regular": [[reg]], "post": [[zero_post]]}}
    payload = _session_payload([reg["start"] + 60], meta)
    assert chart_data.parse_session_info(payload)["has_extended"] is False
    assert chart_data.parse_chart_bars(payload, intraday=True)[0]["session"] == "regular"


def _bar(t, close, session):
    return {"time": t, "open": close, "high": close, "low": close, "close": close,
            "volume": 1, "session": session}


def test_summarize_extended_reports_the_current_after_hours_print():
    bars = [_bar(1, 9.0, "pre"), _bar(2, 10.0, "regular"), _bar(3, 10.5, "post"), _bar(4, 10.7, "post")]
    out = chart_data.summarize_extended(bars)
    assert out == {"pre": None, "post": {"price": 10.7, "time": 4}, "regular_close": 10.0}


def test_summarize_extended_reports_pre_market_before_the_open():
    # Yesterday's post bars followed by this morning's pre bars: pre is current.
    bars = [_bar(1, 10.0, "regular"), _bar(2, 10.2, "post"), _bar(3, 9.8, "pre")]
    out = chart_data.summarize_extended(bars)
    assert out["pre"] == {"price": 9.8, "time": 3} and out["post"] is None


def test_summarize_extended_is_empty_during_the_regular_session():
    bars = [_bar(1, 9.0, "pre"), _bar(2, 10.0, "regular")]
    out = chart_data.summarize_extended(bars)
    assert out["pre"] is None and out["post"] is None and out["regular_close"] == 10.0


def test_summarize_extended_without_tags_invents_nothing():
    assert chart_data.summarize_extended([{"time": 1, "close": 5.0}]) == \
        {"pre": None, "post": None, "regular_close": None}


def test_get_extended_unsupported_listing_reports_nothing(monkeypatch):
    monkeypatch.setattr(chart_data, "get_bars", lambda t, i, prepost=False: {
        "ticker": t, "as_of": "x", "session": {"has_extended": False},
        "bars": [_bar(1, 10.0, "regular"), _bar(2, 11.0, "post")]})
    out = chart_data.get_extended("TEVA.TA")
    assert out["supported"] is False and out["post"] is None and out["pre"] is None


# ---------- currency (WS-B) ----------

def _ila_payload():
    return {"chart": {"result": [{
        "meta": {"currency": "ILA"},
        "timestamp": [1704067200, 1704153600],
        "indicators": {"quote": [{"open": [11900.0, 12000.0], "high": [12100.0, 12100.0],
                                  "low": [11800.0, 11950.0], "close": [12000.0, 12050.0],
                                  "volume": [10, 20]}]},
    }]}}


def test_parse_chart_bars_normalizes_agorot():
    bars = chart_data.parse_chart_bars(_ila_payload(), intraday=False)
    assert [b["close"] for b in bars] == [120.0, 120.5]
    assert bars[0]["volume"] == 10.0


def test_fetch_chart_reports_the_major_currency(monkeypatch):
    class _Resp:
        def raise_for_status(self): pass
        def json(self): return _ila_payload()

    class _Client:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, params=None): return _Resp()

    monkeypatch.setattr(chart_data.httpx, "Client", _Client)
    out = chart_data.fetch_chart("TEVA.TA", "1d")
    assert out["currency"] == "ILS"
    assert out["bars"][-1]["close"] == 120.5


def test_get_bars_payload_carries_currency_and_market(monkeypatch):
    chart_data._cache.clear()
    monkeypatch.setattr(chart_data, "fetch_chart", lambda t, i, prepost=False: {
        "bars": [{"time": "2026-01-01", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0}],
        "session": None, "currency": None})
    out = chart_data.get_bars("TEVA.TA", "1d")
    assert (out["currency"], out["market"]) == ("ILS", "TASE")
    chart_data._cache.clear()
