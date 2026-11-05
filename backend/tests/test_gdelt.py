from app.sources import gdelt

SAMPLE = {
    "articles": [
        {
            "url": "https://example.com/a",
            "title": "Fed holds rates",
            "domain": "example.com",
            "seendate": "20260622T124500Z",
            "sourcecountry": "United States",
            "socialimage": "https://example.com/a.jpg",
        },
        {
            "url": "https://example.com/a",  # duplicate url -> deduped
            "title": "dup",
            "domain": "example.com",
            "seendate": "20260622T130000Z",
            "sourcecountry": "United States",
            "socialimage": "",
        },
        {
            "url": "https://news.de/b",
            "title": "Sanctions update",
            "domain": "news.de",
            "seendate": "20260621T080000Z",
            "sourcecountry": "Germany",
            # no socialimage key
        },
    ]
}


def test_parse_normalizes_and_dedupes():
    arts = gdelt.parse_response(SAMPLE)
    assert len(arts) == 2  # duplicate url removed
    first = arts[0]
    assert first.url == "https://example.com/a"
    assert first.title == "Fed holds rates"
    assert first.seendate == "2026-06-22T12:45:00Z"  # normalized
    assert first.image == "https://example.com/a.jpg"


def test_parse_handles_missing_image():
    arts = gdelt.parse_response(SAMPLE)
    assert arts[1].image == ""


def test_parse_empty():
    assert gdelt.parse_response({"articles": []}) == []
    assert gdelt.parse_response({}) == []


# ---------- per-ticker (portfolio) news ----------

def test_clean_company_name_strips_suffixes():
    assert gdelt.clean_company_name("Tesla, Inc.") == "Tesla"
    assert gdelt.clean_company_name("Lockheed Martin Corporation") == "Lockheed Martin"
    assert gdelt.clean_company_name("ACME HOLDINGS CO") == "ACME"
    assert gdelt.clean_company_name("") == ""


def test_build_ticker_query_with_and_without_name():
    q = gdelt.build_ticker_query("tsla", "Tesla, Inc.")
    assert q == '("TSLA stock" OR "$TSLA" OR "Tesla")'
    # no (or too-short) company name -> cashtag + "X stock" phrases only
    assert gdelt.build_ticker_query("F", None) == '("F stock" OR "$F")'
    assert gdelt.build_ticker_query("GE", "GE") == '("GE stock" OR "$GE")'


def test_fetch_for_tickers_tags_and_survives_failures(monkeypatch):
    import httpx
    from app.models import NewsArticle

    def stub_fetch(query, limit):
        if '"$BAD"' in query:
            raise httpx.ConnectError("boom")
        return [NewsArticle(url=f"https://x/{query[:12]}", title="t", domain="d",
                            seendate="2026-07-06T00:00:00Z", sourcecountry="", image="")]

    monkeypatch.setattr(gdelt, "fetch", stub_fetch)
    out = gdelt.fetch_for_tickers(["NOC", "BAD", "LMT"], {"NOC": "Northrop Grumman Corp"}, 5)
    assert [a.ticker for a in out] == ["NOC", "LMT"]


def test_http_429_defers_the_source_instead_of_failing_it(monkeypatch):
    """A 429 is GDELT asking us to come back later — a deferral with a next
    attempt, not an error, and the cooldown then short-circuits the network."""
    import httpx
    import pytest
    from app import ingest

    hits = []
    real_client = httpx.Client

    def handler(request):
        hits.append(request.url)
        return httpx.Response(429, request=request)

    monkeypatch.setattr(gdelt, "_cooldown_until", 0.0)
    monkeypatch.setattr(
        gdelt.httpx, "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))

    with pytest.raises(ingest.SourceDeferred) as first:
        gdelt.fetch("q", 5)
    assert first.value.retry_after_seconds > 0
    assert "429" in first.value.reason
    assert len(hits) == 1

    with pytest.raises(gdelt.GdeltCoolingDown) as second:
        gdelt.fetch("q", 5)
    assert 0 < second.value.retry_after_seconds <= gdelt.config.GDELT_COOLDOWN_SECONDS
    assert len(hits) == 1  # cooling down: no second request
