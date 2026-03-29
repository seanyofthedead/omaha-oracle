"""
Unit tests for portfolio risk handler.

Bug #4: quant_screen_passed must default to False, not True.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestQuantScreenDefaultFalse:
    """Bug #4: action.get('quant_screen_passed', True) defaults to True, defeating the circuit breaker."""

    def test_missing_quant_screen_defaults_to_false(self, monkeypatch):
        """
        When a BUY action does not include quant_screen_passed,
        the risk handler must treat it as False (fail-safe), not True.
        """
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        portfolio_state = {
            "portfolio_value": 100_000,
            "cash_available": 50_000,
            "positions": [],
            "sector_exposure": {},
        }

        with (
            patch(
                "portfolio.risk.handler.load_portfolio_state",
                return_value=portfolio_state,
            ),
            patch(
                "portfolio.risk.handler.CostTracker",
                return_value=MagicMock(check_budget=MagicMock(return_value={"exhausted": False})),
            ),
        ):
            from portfolio.risk.handler import handler

            event = {
                "buy_decisions": [
                    {
                        "ticker": "AAPL",
                        "signal": "BUY",
                        "position_size_usd": 5_000,
                        "sector": "Technology",
                        # NOTE: quant_screen_passed is MISSING — should default False
                        "moat_score": 8,
                        "margin_of_safety": 0.40,
                    }
                ]
            }
            result = handler(event, None)

        # The circuit breaker should reject this because quant_screen_passed
        # is missing and must default to False
        assert result["overall_passed"] is False
        assert any(
            "Circuit breaker" in v or "quant" in v.lower()
            for r in result["results"]
            for v in r.get("violations", [])
        )

    def test_explicit_quant_screen_true_passes(self, monkeypatch):
        """When quant_screen_passed is explicitly True, circuit breaker should pass."""
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        portfolio_state = {
            "portfolio_value": 100_000,
            "cash_available": 50_000,
            "positions": [],
            "sector_exposure": {},
        }

        with (
            patch(
                "portfolio.risk.handler.load_portfolio_state",
                return_value=portfolio_state,
            ),
            patch(
                "portfolio.risk.handler.CostTracker",
                return_value=MagicMock(check_budget=MagicMock(return_value={"exhausted": False})),
            ),
        ):
            from portfolio.risk.handler import handler

            event = {
                "buy_decisions": [
                    {
                        "ticker": "AAPL",
                        "signal": "BUY",
                        "position_size_usd": 5_000,
                        "sector": "Technology",
                        "quant_screen_passed": True,
                        "moat_score": 8,
                        "margin_of_safety": 0.40,
                    }
                ]
            }
            result = handler(event, None)

        # With explicit True, circuit breaker should pass
        assert result["overall_passed"] is True
        assert all(r.get("consistency_ok", False) for r in result["results"])
