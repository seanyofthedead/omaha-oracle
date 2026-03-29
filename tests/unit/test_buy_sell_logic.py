"""
Unit tests for buy/sell decision logic.

- All criteria pass → BUY
- One criterion fail → NO_BUY
- Thesis broken → SELL
- Price decline alone → HOLD (never panic sell)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from portfolio.allocation.buy_sell_logic import evaluate_buy, evaluate_sell


class TestEvaluateBuyAllPass:
    """All criteria pass → BUY."""

    def test_all_pass_returns_buy(self):
        analysis = {
            "margin_of_safety": 0.40,
            "moat_score": 8,
            "management_score": 7,
            "sector": "Technology",
        }
        portfolio_state = {
            "cash_available": 50_000,
            "portfolio_value": 100_000,
            "positions": [{"ticker": "AAPL"}],
            "sector_exposure": {"Technology": 0.20},
        }
        result = evaluate_buy("MSFT", analysis, portfolio_state)
        assert result["signal"] == "BUY"
        assert len(result["reasons_fail"]) == 0

    def test_sector_whitelist_empty_allows(self):
        analysis = {
            "margin_of_safety": 0.35,
            "moat_score": 8,
            "management_score": 6,
            "sector": "Tech",
        }
        portfolio_state = {
            "cash_available": 10_000,
            "portfolio_value": 50_000,
            "positions": [],
            "sector_exposure": {},
        }
        result = evaluate_buy("XYZ", analysis, portfolio_state)
        assert result["signal"] == "BUY"


class TestEvaluateBuyOneFail:
    """One criterion fail → NO_BUY."""

    def test_low_mos_no_buy(self):
        analysis = {
            "margin_of_safety": 0.20,
            "moat_score": 8,
            "management_score": 7,
            "sector": "Tech",
        }
        portfolio_state = {
            "cash_available": 50_000,
            "portfolio_value": 100_000,
            "positions": [],
            "sector_exposure": {},
        }
        result = evaluate_buy("X", analysis, portfolio_state)
        assert result["signal"] == "NO_BUY"
        assert any("MoS" in r for r in result["reasons_fail"])

    def test_low_moat_no_buy(self):
        analysis = {
            "margin_of_safety": 0.40,
            "moat_score": 5,
            "management_score": 7,
            "sector": "Tech",
        }
        portfolio_state = {
            "cash_available": 50_000,
            "portfolio_value": 100_000,
            "positions": [],
            "sector_exposure": {},
        }
        result = evaluate_buy("X", analysis, portfolio_state)
        assert result["signal"] == "NO_BUY"
        assert any("moat" in r.lower() for r in result["reasons_fail"])

    def test_no_cash_no_buy(self):
        analysis = {
            "margin_of_safety": 0.40,
            "moat_score": 8,
            "management_score": 7,
            "sector": "Tech",
        }
        portfolio_state = {
            "cash_available": 0,
            "portfolio_value": 100_000,
            "positions": [],
            "sector_exposure": {},
        }
        result = evaluate_buy("X", analysis, portfolio_state)
        assert result["signal"] == "NO_BUY"
        assert any("cash" in r.lower() for r in result["reasons_fail"])

    def test_sector_at_limit_no_buy(self):
        analysis = {
            "margin_of_safety": 0.40,
            "moat_score": 8,
            "management_score": 7,
            "sector": "Tech",
        }
        portfolio_state = {
            "cash_available": 50_000,
            "portfolio_value": 100_000,
            "positions": [],
            "sector_exposure": {"Tech": 0.35},
        }
        result = evaluate_buy("X", analysis, portfolio_state)
        assert result["signal"] == "NO_BUY"
        assert any("Sector" in r for r in result["reasons_fail"])


class TestEvaluateSellThesisBroken:
    """Thesis broken (moat < 5 for 2 quarters) → SELL."""

    @pytest.fixture
    def moat_history_broken(self):
        now = datetime.now(UTC)
        return [
            {"date": (now - timedelta(days=30)).isoformat(), "moat_score": 4},
            {"date": (now - timedelta(days=60)).isoformat(), "moat_score": 3},
        ]

    def test_thesis_broken_returns_sell(self, moat_history_broken):
        position = {
            "purchase_date": (datetime.now(UTC) - timedelta(days=400)).isoformat(),
            "shares": 100,
        }
        analysis = {"intrinsic_value_per_share": 150, "current_price": 100}
        result = evaluate_sell("X", position, analysis, {}, moat_history=moat_history_broken)
        assert result["signal"] == "SELL"
        assert any("Thesis broken" in r for r in result["reasons"])


class TestEvaluateSellPriceDeclineHold:
    """Price decline alone → HOLD (never panic sell)."""

    def test_price_decline_no_sell(self):
        position = {
            "purchase_date": (datetime.now(UTC) - timedelta(days=400)).isoformat(),
            "shares": 100,
        }
        analysis = {"intrinsic_value_per_share": 120, "current_price": 80}  # Down from purchase
        result = evaluate_sell("X", position, analysis, {}, moat_history=None)
        assert result["signal"] == "HOLD"
        assert any("No sell trigger" in r or "HOLD" in r for r in result["reasons"])

    def test_no_moat_history_no_thesis_break_hold(self):
        position = {"purchase_date": (datetime.now(UTC) - timedelta(days=400)).isoformat()}
        analysis = {"intrinsic_value_per_share": 100, "current_price": 60}
        result = evaluate_sell("X", position, analysis, {}, moat_history=[])
        assert result["signal"] == "HOLD"


class TestCashReserveInBuy:
    """Bug #5: evaluate_buy must reject when cash < 10% of portfolio value."""

    def test_cash_below_10_pct_no_buy(self):
        """Cash at 5% of portfolio should fail the buy evaluation."""
        analysis = {
            "margin_of_safety": 0.40,
            "moat_score": 8,
            "management_score": 7,
            "sector": "Technology",
        }
        portfolio_state = {
            "cash_available": 5_000,  # 5% of 100K — below 10% minimum
            "portfolio_value": 100_000,
            "positions": [],
            "sector_exposure": {},
        }
        result = evaluate_buy("AAPL", analysis, portfolio_state)
        assert result["signal"] == "NO_BUY"
        assert any("cash reserve" in r.lower() or "10%" in r for r in result["reasons_fail"])

    def test_cash_above_10_pct_passes_cash_check(self):
        """Cash at 15% should pass the cash reserve check."""
        analysis = {
            "margin_of_safety": 0.40,
            "moat_score": 8,
            "management_score": 7,
            "sector": "Technology",
        }
        portfolio_state = {
            "cash_available": 15_000,  # 15% — above minimum
            "portfolio_value": 100_000,
            "positions": [],
            "sector_exposure": {},
        }
        result = evaluate_buy("AAPL", analysis, portfolio_state)
        assert result["signal"] == "BUY"
        assert not any("cash reserve" in r.lower() for r in result["reasons_fail"])


class TestEvaluateSellExtremeOvervaluation:
    """Extreme overvaluation (>150% IV) → SELL."""

    def test_overvalued_returns_sell(self):
        position = {"purchase_date": (datetime.now(UTC) - timedelta(days=400)).isoformat()}
        analysis = {"intrinsic_value_per_share": 100, "current_price": 160}
        result = evaluate_sell("X", position, analysis, {})
        assert result["signal"] == "SELL"
        assert any("overvaluation" in r.lower() for r in result["reasons"])
