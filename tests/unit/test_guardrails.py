"""
Unit tests for hard limit guardrails.

- Sector concentration breach
- Position size breach
- Prohibited asset types (options, crypto, shorts, leverage)
"""
from __future__ import annotations

import pytest

from portfolio.risk.guardrails import (
    MAX_POSITION_PCT,
    MAX_SECTOR_PCT,
    check_all_guardrails,
    validate_analysis_consistency,
)


class TestSectorConcentration:
    """Sector concentration breach."""

    def test_sector_over_limit_fails(self):
        proposed = {
            "signal": "BUY",
            "side": "buy",
            "position_size_usd": 40_000,
            "sector": "Technology",
            "asset_type": "equity",
        }
        portfolio_state = {
            "portfolio_value": 100_000,
            "cash_available": 50_000,
            "positions": [{"sector": "Technology", "market_value": 30_000}],
            "sector_exposure": {"Technology": 0.30},
        }
        budget_status = {"exhausted": False}
        result = check_all_guardrails(proposed, portfolio_state, budget_status)
        assert result["passed"] is False
        assert any("Sector" in v for v in result["violations"])

    def test_sector_under_limit_passes(self):
        proposed = {
            "signal": "BUY",
            "side": "buy",
            "position_size_usd": 10_000,
            "sector": "Healthcare",
            "asset_type": "equity",
        }
        portfolio_state = {
            "portfolio_value": 100_000,
            "cash_available": 50_000,
            "positions": [],
            "sector_exposure": {"Healthcare": 0.20},
        }
        budget_status = {"exhausted": False}
        result = check_all_guardrails(proposed, portfolio_state, budget_status)
        assert result["passed"] is True


class TestPositionSizeBreach:
    """Position size breach (max 15%)."""

    def test_position_over_15_pct_fails(self):
        proposed = {
            "signal": "BUY",
            "side": "buy",
            "position_size_usd": 20_000,
            "position_pct": 0.20,
            "sector": "Tech",
            "asset_type": "equity",
        }
        portfolio_state = {
            "portfolio_value": 100_000,
            "cash_available": 50_000,
            "positions": [],
            "sector_exposure": {},
        }
        budget_status = {"exhausted": False}
        result = check_all_guardrails(proposed, portfolio_state, budget_status)
        assert result["passed"] is False
        assert any("Position" in v or "exceeds max" in v for v in result["violations"])

    def test_position_at_limit_passes(self):
        proposed = {
            "signal": "BUY",
            "side": "buy",
            "position_size_usd": 15_000,
            "sector": "Tech",
            "asset_type": "equity",
        }
        portfolio_state = {
            "portfolio_value": 100_000,
            "cash_available": 50_000,
            "positions": [],
            "sector_exposure": {},
        }
        budget_status = {"exhausted": False}
        result = check_all_guardrails(proposed, portfolio_state, budget_status)
        assert result["passed"] is True


class TestProhibitedAssetTypes:
    """Prohibited: options, crypto, shorts, leverage."""

    def test_crypto_fails(self):
        proposed = {"signal": "BUY", "asset_type": "crypto", "position_size_usd": 1000}
        portfolio_state = {"portfolio_value": 100_000, "cash_available": 50_000, "positions": [], "sector_exposure": {}}
        result = check_all_guardrails(proposed, portfolio_state, {"exhausted": False})
        assert result["passed"] is False
        assert any("Crypto" in v for v in result["violations"])

    def test_options_fails(self):
        proposed = {"signal": "BUY", "options": True, "asset_type": "equity", "position_size_usd": 5000}
        portfolio_state = {"portfolio_value": 100_000, "cash_available": 50_000, "positions": [], "sector_exposure": {}}
        result = check_all_guardrails(proposed, portfolio_state, {"exhausted": False})
        assert result["passed"] is False
        assert any("Options" in v for v in result["violations"])

    def test_short_fails(self):
        proposed = {"signal": "BUY", "side": "short", "asset_type": "equity"}
        portfolio_state = {"portfolio_value": 100_000, "cash_available": 50_000, "positions": [], "sector_exposure": {}}
        result = check_all_guardrails(proposed, portfolio_state, {"exhausted": False})
        assert result["passed"] is False
        assert any("Short" in v for v in result["violations"])

    def test_leverage_fails(self):
        proposed = {"signal": "BUY", "leverage": 2.0, "asset_type": "equity", "position_size_usd": 5000}
        portfolio_state = {"portfolio_value": 100_000, "cash_available": 50_000, "positions": [], "sector_exposure": {}}
        result = check_all_guardrails(proposed, portfolio_state, {"exhausted": False})
        assert result["passed"] is False
        assert any("Leverage" in v for v in result["violations"])


class TestBudgetExhausted:
    """LLM budget exhausted blocks new analysis."""

    def test_exhausted_budget_fails(self):
        proposed = {"signal": "BUY", "asset_type": "equity", "position_size_usd": 5000}
        portfolio_state = {"portfolio_value": 100_000, "cash_available": 50_000, "positions": [], "sector_exposure": {}}
        result = check_all_guardrails(proposed, portfolio_state, {"exhausted": True})
        assert result["passed"] is False
        assert any("budget" in v.lower() for v in result["violations"])


class TestValidateAnalysisConsistency:
    """Circuit breaker: reject BUY if LLM contradicts quant screen."""

    def test_buy_requires_quant_passed(self):
        assert validate_analysis_consistency("BUY", quant_screen_passed=False, moat_score=8, margin_of_safety=0.40) is False

    def test_buy_requires_moat_ge_7(self):
        assert validate_analysis_consistency("BUY", quant_screen_passed=True, moat_score=5, margin_of_safety=0.40) is False

    def test_buy_requires_mos_gt_30(self):
        assert validate_analysis_consistency("BUY", quant_screen_passed=True, moat_score=8, margin_of_safety=0.25) is False

    def test_buy_all_pass_consistent(self):
        assert validate_analysis_consistency("BUY", quant_screen_passed=True, moat_score=8, margin_of_safety=0.40) is True

    def test_no_buy_always_consistent(self):
        assert validate_analysis_consistency("NO_BUY", quant_screen_passed=False, moat_score=3, margin_of_safety=0.0) is True
