"""
Unit tests for thesis generator handler.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

_THESIS_TEXT = "## The Business\nApple makes iPhones.\n## The Moat\nStrong switching costs.\n"

_LLM_THESIS_RESPONSE = {
    "content": _THESIS_TEXT,
    "model": "claude-opus-4-20250514",
    "input_tokens": 1500,
    "output_tokens": 800,
    "cost_usd": 0.08,
}

_QUALIFYING_EVENT = {
    "ticker": "AAPL",
    "company_name": "Apple Inc.",
    "quant_passed": True,
    "moat_score": 8,
    "management_score": 7,
    "margin_of_safety": 0.35,
}


class TestThesisGeneratorHandler:
    """Tests for thesis generator handler."""

    def test_qualifying_company_writes_thesis_to_s3(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_USD", "100.0")

        with (
            patch("analysis.thesis_generator.handler.LLMClient") as mock_llm_cls,
            patch("analysis.thesis_generator.handler.store_analysis_result") as mock_store,
            patch("analysis.thesis_generator.handler.S3Client") as mock_s3_cls,
        ):
            mock_llm = MagicMock()
            mock_llm.invoke.return_value = _LLM_THESIS_RESPONSE
            mock_llm_cls.return_value = mock_llm

            mock_s3 = MagicMock()
            mock_s3_cls.return_value = mock_s3

            from analysis.thesis_generator.handler import handler

            result = handler(_QUALIFYING_EVENT, None)

        assert result["thesis_generated"] is True
        assert "theses/AAPL/" in result["thesis_s3_key"]
        mock_s3.write_markdown.assert_called_once()
        mock_store.assert_called_once()

    def test_low_moat_score_skips_thesis(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        event = {**_QUALIFYING_EVENT, "moat_score": 4}  # Below MOAT_MIN=7

        with patch("analysis.thesis_generator.handler.LLMClient") as mock_llm_cls:
            from analysis.thesis_generator.handler import handler

            result = handler(event, None)

        assert result["thesis_generated"] is False
        assert "moat_score" in result["skipped_reason"]
        mock_llm_cls.return_value.invoke.assert_not_called()

    def test_low_margin_of_safety_skips_thesis(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        event = {**_QUALIFYING_EVENT, "margin_of_safety": 0.20}  # Below MOS_MIN=0.30

        with patch("analysis.thesis_generator.handler.LLMClient") as mock_llm_cls:
            from analysis.thesis_generator.handler import handler

            result = handler(event, None)

        assert result["thesis_generated"] is False
        assert "margin_of_safety" in result["skipped_reason"]
        mock_llm_cls.return_value.invoke.assert_not_called()
