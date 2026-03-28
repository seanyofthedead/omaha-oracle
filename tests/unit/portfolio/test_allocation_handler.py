"""
Unit tests for allocation handler — prediction threading.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


_BUY_ANALYSIS = {
    "ticker": "AAPL",
    "moat_score": 8,
    "management_score": 7,
    "margin_of_safety": 0.35,
    "predictions": [
        {
            "description": "Revenue exceeds $400B",
            "metric": "revenue",
            "operator": ">",
            "threshold": 400_000_000_000,
            "data_source": "yahoo_finance",
            "deadline": "2027-01-01",
            "analysis_stage": "intrinsic_value",
        },
        {
            "description": "Gross margin stays above 45%",
            "metric": "gross_margin",
            "operator": ">",
            "threshold": 0.45,
            "data_source": "sec_edgar",
            "deadline": "2027-06-30",
            "analysis_stage": "moat_analysis",
        },
    ],
}

_PORTFOLIO_STATE = {
    "portfolio_value": 100_000.0,
    "cash_available": 30_000.0,
    "positions": [],
}


class TestAllocationPredictionThreading:
    """Tests for prediction data flowing through allocation handler to decisions table."""

    def test_buy_decision_includes_predictions_with_status(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        logged_items = []

        with (
            patch("portfolio.allocation.handler._check_trading_enabled"),
            patch("portfolio.allocation.handler.DynamoClient") as mock_dc_cls,
            patch("portfolio.allocation.handler.load_portfolio_state", return_value=_PORTFOLIO_STATE),
            patch("portfolio.allocation.handler.load_latest_analysis", return_value=_BUY_ANALYSIS),
            patch(
                "portfolio.allocation.handler.evaluate_buy",
                return_value={
                    "signal": "BUY",
                    "reasons_pass": ["All stages passed"],
                    "reasons_fail": [],
                },
            ),
            patch(
                "portfolio.allocation.handler.calculate_position_size",
                return_value={
                    "can_buy": True,
                    "position_size_usd": 5000,
                    "position_pct": 0.05,
                },
            ),
        ):
            mock_dc = MagicMock()
            mock_dc.put_item.side_effect = lambda item: logged_items.append(item)
            mock_dc_cls.return_value = mock_dc

            from portfolio.allocation.handler import handler

            result = handler({"tickers": ["AAPL"]}, None)

        assert len(result["buy_decisions"]) == 1

        # Find the logged BUY decision
        buy_logs = [i for i in logged_items if i.get("decision_type") == "BUY"]
        assert len(buy_logs) == 1

        payload = buy_logs[0]["payload"]
        assert "predictions" in payload
        assert len(payload["predictions"]) == 2
        assert payload["predictions"][0]["status"] == "pending"
        assert payload["predictions"][0]["metric"] == "revenue"
        assert payload["predictions"][1]["status"] == "pending"
        # Each prediction should have a unique ID
        assert payload["predictions"][0]["id"].startswith("pred_AAPL_")
        assert payload["predictions"][0]["id"] != payload["predictions"][1]["id"]

    def test_no_buy_decision_has_no_predictions(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        logged_items = []

        with (
            patch("portfolio.allocation.handler._check_trading_enabled"),
            patch("portfolio.allocation.handler.DynamoClient") as mock_dc_cls,
            patch("portfolio.allocation.handler.load_portfolio_state", return_value=_PORTFOLIO_STATE),
            patch("portfolio.allocation.handler.load_latest_analysis", return_value=_BUY_ANALYSIS),
            patch(
                "portfolio.allocation.handler.evaluate_buy",
                return_value={
                    "signal": "NO_BUY",
                    "reasons_pass": [],
                    "reasons_fail": ["Moat score too low"],
                },
            ),
        ):
            mock_dc = MagicMock()
            mock_dc.put_item.side_effect = lambda item: logged_items.append(item)
            mock_dc_cls.return_value = mock_dc

            from portfolio.allocation.handler import handler

            result = handler({"tickers": ["AAPL"]}, None)

        buy_logs = [i for i in logged_items if i.get("decision_type") == "BUY"]
        assert len(buy_logs) == 1
        # NO_BUY should not have predictions
        assert "predictions" not in buy_logs[0]["payload"]

    def test_buy_with_empty_predictions_stores_empty_list(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        analysis_no_preds = {**_BUY_ANALYSIS, "predictions": []}
        logged_items = []

        with (
            patch("portfolio.allocation.handler._check_trading_enabled"),
            patch("portfolio.allocation.handler.DynamoClient") as mock_dc_cls,
            patch("portfolio.allocation.handler.load_portfolio_state", return_value=_PORTFOLIO_STATE),
            patch("portfolio.allocation.handler.load_latest_analysis", return_value=analysis_no_preds),
            patch(
                "portfolio.allocation.handler.evaluate_buy",
                return_value={
                    "signal": "BUY",
                    "reasons_pass": ["All stages passed"],
                    "reasons_fail": [],
                },
            ),
            patch(
                "portfolio.allocation.handler.calculate_position_size",
                return_value={
                    "can_buy": True,
                    "position_size_usd": 5000,
                    "position_pct": 0.05,
                },
            ),
        ):
            mock_dc = MagicMock()
            mock_dc.put_item.side_effect = lambda item: logged_items.append(item)
            mock_dc_cls.return_value = mock_dc

            from portfolio.allocation.handler import handler

            result = handler({"tickers": ["AAPL"]}, None)

        buy_logs = [i for i in logged_items if i.get("decision_type") == "BUY"]
        payload = buy_logs[0]["payload"]
        assert payload["predictions"] == []
