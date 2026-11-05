"""Config defaults that the plan pins explicitly."""
import importlib

from app import config


def test_x_min_interval_default_is_hourly():
    # X watcher now fetches at most hourly (was 15 min).
    assert config.X_MIN_INTERVAL_SECONDS == 3600


def test_background_analysis_defaults():
    assert config.OHLC_MIN_INTERVAL_SECONDS == 3600
    assert config.ANALYSIS_MIN_INTERVAL_SECONDS == 3600
    assert config.OHLC_MAX_TICKERS == 60
    # Deep enough to fill both suggestion pools (tracked names + new ideas).
    assert config.OPPORTUNITY_CANDIDATES == 40


def test_x_min_interval_overridable(monkeypatch):
    monkeypatch.setenv("STOCKS_X_MIN_INTERVAL_SECONDS", "1234")
    reloaded = importlib.reload(config)
    try:
        assert reloaded.X_MIN_INTERVAL_SECONDS == 1234
    finally:
        monkeypatch.delenv("STOCKS_X_MIN_INTERVAL_SECONDS", raising=False)
        importlib.reload(config)
