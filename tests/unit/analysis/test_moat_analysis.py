"""
Unit tests for moat analysis handler.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

_LLM_MOAT_RESPONSE = {
    "content": {
        "moat_type": "wide",
        "moat_sources": ["switching costs", "intangible assets"],
        "moat_score": 8,
        "moat_trend": "stable",
        "pricing_power": 7,
        "customer_captivity": 8,
        "reasoning": "Apple has exceptional switching costs and brand power.",
        "risks_to_moat": ["commoditisation of smartphones"],
        "confidence": 0.85,
    },
    "model": "claude-sonnet-4-20250514",
    "input_tokens": 500,
    "output_tokens": 200,
    "cost_usd": 0.01,
}


class TestMoatAnalysisHandler:
    """Tests for moat analysis handler."""

    def test_happy_path_stores_result(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_USD", "100.0")

        with (
            patch("analysis.moat_analysis.handler.LLMClient") as mock_llm_cls,
            patch("analysis.moat_analysis.handler.store_analysis_result") as mock_store,
            patch("analysis.moat_analysis.handler.CostTracker") as mock_tracker_cls,
            patch("analysis.moat_analysis.handler.S3Client") as mock_s3_cls,
            patch("analysis.moat_analysis.handler.LessonsClient") as mock_lessons_cls,
        ):
            mock_tracker = MagicMock()
            mock_tracker.check_budget.return_value = {
                "exhausted": False,
                "spent_usd": 5.0,
                "budget_usd": 100.0,
            }
            mock_tracker_cls.return_value = mock_tracker

            mock_llm = MagicMock()
            mock_llm.invoke.return_value = _LLM_MOAT_RESPONSE
            mock_llm_cls.return_value = mock_llm

            mock_s3 = MagicMock()
            mock_s3.get_filing_context.return_value = ("No context available.", False)
            mock_s3_cls.return_value = mock_s3

            mock_lessons = MagicMock()
            mock_lessons.get_relevant_lessons.return_value = ""
            mock_lessons_cls.return_value = mock_lessons

            from analysis.moat_analysis.handler import handler

            result = handler({"ticker": "AAPL", "metrics": {"sector": "Technology"}}, None)

        assert result["moat_score"] == 8
        assert result["moat_type"] == "wide"
        assert result["skipped"] is False
        mock_store.assert_called_once()
        store_args = mock_store.call_args[0]
        assert store_args[1] == "AAPL"

    def test_budget_exhausted_skips_llm(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        with (
            patch("analysis.moat_analysis.handler.LLMClient") as mock_llm_cls,
            patch("analysis.moat_analysis.handler.store_analysis_result") as mock_store,
            patch("analysis.moat_analysis.handler.CostTracker") as mock_tracker_cls,
            patch("analysis.moat_analysis.handler.S3Client"),
        ):
            mock_tracker = MagicMock()
            mock_tracker.check_budget.return_value = {
                "exhausted": True,
                "spent_usd": 110.0,
                "budget_usd": 100.0,
            }
            mock_tracker_cls.return_value = mock_tracker

            from analysis.moat_analysis.handler import handler

            result = handler({"ticker": "AAPL"}, None)

        assert result["skipped"] is True
        assert result["moat_score"] == 0
        mock_llm_cls.return_value.invoke.assert_not_called()
        mock_store.assert_called_once()

    def test_missing_ticker_returns_error(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        with (
            patch("analysis.moat_analysis.handler.CostTracker"),
            patch("analysis.moat_analysis.handler.S3Client"),
        ):
            from analysis.moat_analysis.handler import handler

            result = handler({}, None)

        assert "error" in result
        assert result["moat_score"] == 0
