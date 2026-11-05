"""Unit tests for the social sentiment source module."""
from app.sources.social import parse_response, fetch


def _page(results):
    return {"results": results, "next_page": 2}


def _item(ticker, rank, rank_24h, mentions=100, upvotes=50):
    return {
        "ticker": ticker,
        "rank": rank,
        "rank_24h_ago": rank_24h,
        "mentions": mentions,
        "upvotes": upvotes,
    }


def test_parse_response_basic():
    payload = _page([_item("GME", 1, 5, 500, 200), _item("AMC", 2, 3, 300, 100)])
    result = parse_response(payload)
    assert "GME" in result
    assert result["GME"]["rank"] == 1
    assert result["GME"]["rank_24h_ago"] == 5
    assert result["GME"]["rank_change"] == 4  # 5 - 1
    assert result["AMC"]["rank_change"] == 1  # 3 - 2


def test_parse_response_rising_rank():
    payload = _page([_item("TSLA", 2, 15)])
    result = parse_response(payload)
    assert result["TSLA"]["rank_change"] == 13  # 15 - 2 = big rise


def test_parse_response_falling_rank():
    payload = _page([_item("NVDA", 10, 3)])
    result = parse_response(payload)
    assert result["NVDA"]["rank_change"] == -7  # 3 - 10 = falling


def test_parse_response_missing_rank_gives_none():
    payload = _page([{"ticker": "AAPL", "mentions": 50, "upvotes": 10}])
    result = parse_response(payload)
    assert result["AAPL"]["rank_change"] is None


def test_parse_response_skips_empty_ticker():
    payload = _page([
        {"ticker": "", "rank": 1, "rank_24h_ago": 1, "mentions": 100, "upvotes": 50},
        _item("GME", 2, 2),
    ])
    result = parse_response(payload)
    assert "" not in result
    assert "GME" in result


def test_parse_response_normalizes_ticker_case():
    payload = _page([_item("gme", 1, 5)])
    result = parse_response(payload)
    assert "GME" in result


def test_fetch_filters_to_watchlist_tickers(monkeypatch):
    """fetch() should only return tickers in the provided watchlist."""
    import app.sources.social as social

    call_count = 0

    def fake_get(url, **kwargs):
        nonlocal call_count
        call_count += 1

        class FakeResp:
            def raise_for_status(self): pass
            def json(self):
                return _page([_item("GME", 1, 8), _item("AAPL", 2, 5), _item("MSFT", 3, 4)])
        return FakeResp()

    class FakeClient:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url, **kw): return fake_get(url)

    monkeypatch.setattr(social.httpx, "Client", lambda **kw: FakeClient())
    monkeypatch.setattr(social.time, "sleep", lambda s: None)

    results = social.fetch(["GME", "TSLA"])
    tickers = {r.ticker for r in results}
    assert "GME" in tickers
    assert "TSLA" not in tickers  # TSLA not in top 100 mock → skipped
    assert "AAPL" not in tickers  # not in watchlist
