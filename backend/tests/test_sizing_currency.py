"""Position sizing compares like with like: account size is in the user's
base currency, risk per share in the ticker's trading currency."""
import pytest

from app import analysis
from app.models import StockAnalysis


def _a(ticker, risk_ps=2.0):
    return StockAnalysis(ticker=ticker, computed_at="t", price=100.0, trend="up",
                         ma_alignment="stacked_up", risk_per_share=risk_ps)


def test_same_currency_sizes_directly():
    out = analysis.apply_sizing(_a("AAPL"), 10_000, 1.0, account_currency="USD",
                                rate_fn=lambda f, t: pytest.fail("no FX needed"))
    assert out.suggested_shares == 50           # 100 risk / 2 per share
    assert (out.currency, out.account_currency) == ("USD", "USD")
    assert out.sizing_note == ""


def test_usd_account_sizes_a_shekel_stock_after_conversion():
    out = analysis.apply_sizing(_a("TEVA.TA"), 10_000, 1.0, account_currency="USD",
                                rate_fn=lambda f, t: 3.0 if (f, t) == ("USD", "ILS") else None)
    # $100 risk = ₪300 risk; ₪2 per share -> 150 shares.
    assert out.suggested_shares == 150
    assert out.currency == "ILS"
    assert "3.0000" in out.sizing_note


def test_missing_fx_leaves_the_position_unsized_and_says_so():
    out = analysis.apply_sizing(_a("TEVA.TA"), 10_000, 1.0, account_currency="USD",
                                rate_fn=lambda f, t: None)
    assert out.suggested_shares is None
    assert "FX unavailable" in out.sizing_note


def test_legacy_call_without_account_currency_is_unchanged():
    out = analysis.apply_sizing(_a("AAPL"), 10_000, 1.0)
    assert out.suggested_shares == 50
