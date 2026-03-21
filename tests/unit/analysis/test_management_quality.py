"""
Unit tests for management quality handler.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

_LLM_MGMT_RESPONSE = {
    "content": {
        "owner_operator_mindset": 8,
        "capital_allocation_skill": 7,
        "candor_transparency": 8,
        "management_score": 8,
        "red_flags": [],
        "green_flags": ["strong buyback discipline", "long-tenured CEO"],
        "reasoning": "Management demonstrates owner-operator mindset.",
        "confidence": 0.8,
    },
    "model": "claude-sonnet-4-20250514",
    "input_tokens": 400,
    "output_tokens": 150,
    "cost_usd": 0.008,
}


class TestManagementQualityHandler:
    """Tests for management quality handler."""

    def test_happy_path_returns_management_score(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_USD", "100.0")

        with (
            patch("analysis.management_quality.handler.LLMClient") as mock_llm_cls,
            patch("analysis.management_quality.handler.store_analysis_result") as mock_store,
            patch("analysis.management_quality.handler.CostTracker") as mock_tracker_cls,
            patch("analysis.management_quality.handler.S3Client") as mock_s3_cls,
        ):
            mock_tracker = MagicMock()
            mock_tracker.check_budget.return_value = {
                "exhausted": False,
                "spent_usd": 5.0,
                "budget_usd": 100.0,
            }
            mock_tracker_cls.return_value = mock_tracker

            mock_llm = MagicMock()
            mock_llm.invoke.return_value = _LLM_MGMT_RESPONSE
            mock_llm_cls.return_value = mock_llm

            mock_s3 = MagicMock()
            mock_s3.get_filing_context.return_value = ("No context.", False)
            mock_s3_cls.return_value = mock_s3

            from analysis.management_quality.handler import handler

            result = handler({"ticker": "AAPL", "moat_score": 8}, None)

        assert result["management_score"] == 8
        assert result["skipped"] is False
        mock_store.assert_called_once()

    def test_budget_exhausted_skips_llm(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        with (
            patch("analysis.management_quality.handler.LLMClient") as mock_llm_cls,
            patch("analysis.management_quality.handler.store_analysis_result") as mock_store,
            patch("analysis.management_quality.handler.CostTracker") as mock_tracker_cls,
            patch("analysis.management_quality.handler.S3Client"),
        ):
            mock_tracker = MagicMock()
            mock_tracker.check_budget.return_value = {
                "exhausted": True,
                "spent_usd": 105.0,
                "budget_usd": 100.0,
            }
            mock_tracker_cls.return_value = mock_tracker

            from analysis.management_quality.handler import handler

            result = handler({"ticker": "AAPL"}, None)

        assert result["skipped"] is True
        assert result["management_score"] == 0
        mock_llm_cls.return_value.invoke.assert_not_called()
        mock_store.assert_called_once()
