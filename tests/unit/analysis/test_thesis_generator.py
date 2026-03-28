"""
Unit tests for thesis generator handler.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from shared.llm_client import BudgetExhaustedError

_THESIS_TEXT = "## The Business\nApple makes iPhones.\n## The Moat\nStrong switching costs.\n"

_LLM_THESIS_RESPONSE = {
    "content": _THESIS_TEXT,
    "model": "claude-opus-4-20250514",
    "input_tokens": 1500,
    "output_tokens": 800,
    "cost_usd": 0.08,
}

_VALID_PREDICTIONS = {
    "predictions": [
        {
            "description": "Revenue exceeds $400B by Q4 2026",
            "metric": "revenue",
            "operator": ">",
            "threshold": 400_000_000_000,
            "data_source": "yahoo_finance",
            "deadline": "2026-12-31",
        },
        {
            "description": "Gross margin stays above 45%",
            "metric": "gross_margin",
            "operator": ">",
            "threshold": 0.45,
            "data_source": "sec_edgar",
            "deadline": "2027-06-30",
        },
        {
            "description": "EPS exceeds $7.50",
            "metric": "earnings_per_share",
            "operator": ">",
            "threshold": 7.50,
            "data_source": "yahoo_finance",
            "deadline": "2027-03-31",
        },
    ]
}

_LLM_PREDICTION_RESPONSE = {
    "content": _VALID_PREDICTIONS,
    "model": "claude-haiku-4-5-20251001",
    "input_tokens": 500,
    "output_tokens": 200,
    "cost_usd": 0.01,
}

_QUALIFYING_EVENT = {
    "ticker": "AAPL",
    "company_name": "Apple Inc.",
    "quant_passed": True,
    "moat_score": 8,
    "management_score": 7,
    "margin_of_safety": 0.35,
    "sector": "Technology",
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

    def test_predictions_extracted_on_qualifying_thesis(self, monkeypatch):
        """Happy path: thesis + prediction extraction produces 3 predictions."""
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_USD", "100.0")

        with (
            patch("analysis.thesis_generator.handler.LLMClient") as mock_llm_cls,
            patch("analysis.thesis_generator.handler.store_analysis_result"),
            patch("analysis.thesis_generator.handler.S3Client") as mock_s3_cls,
        ):
            mock_llm = MagicMock()
            # First call: thesis generation, second call: prediction extraction
            mock_llm.invoke.side_effect = [_LLM_THESIS_RESPONSE, _LLM_PREDICTION_RESPONSE]
            mock_llm_cls.return_value = mock_llm
            mock_s3_cls.return_value = MagicMock()

            from analysis.thesis_generator.handler import handler

            result = handler(_QUALIFYING_EVENT, None)

        assert result["thesis_generated"] is True
        assert len(result["predictions"]) == 3
        assert result["predictions"][0]["metric"] == "revenue"
        assert result["predictions"][0]["analysis_stage"] == "intrinsic_value"
        assert result["predictions"][1]["analysis_stage"] == "moat_analysis"
        assert mock_llm.invoke.call_count == 2
        # Second call should be bulk tier with require_json=True
        second_call = mock_llm.invoke.call_args_list[1]
        assert second_call.kwargs.get("tier") == "bulk" or second_call[1].get("tier") == "bulk"

    def test_budget_exhaustion_on_prediction_still_stores_thesis(self, monkeypatch):
        """Budget exhaustion on prediction extraction should not block thesis storage."""
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
            mock_llm.invoke.side_effect = [
                _LLM_THESIS_RESPONSE,
                BudgetExhaustedError("Budget exceeded"),
            ]
            mock_llm_cls.return_value = mock_llm
            mock_s3_cls.return_value = MagicMock()

            from analysis.thesis_generator.handler import handler

            result = handler(_QUALIFYING_EVENT, None)

        assert result["thesis_generated"] is True
        assert result["predictions"] == []
        mock_store.assert_called_once()

    def test_invalid_predictions_filtered_out(self, monkeypatch):
        """Predictions with invalid metrics or missing fields are filtered."""
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_USD", "100.0")

        bad_predictions = {
            "content": {
                "predictions": [
                    {
                        "metric": "invalid_metric",
                        "operator": ">",
                        "threshold": 100,
                        "deadline": "2027-01-01",
                    },
                    {
                        "metric": "revenue",
                        "operator": ">",
                        "threshold": 400e9,
                        "deadline": "2027-01-01",
                        "description": "Good",
                    },
                    {
                        "metric": "gross_margin",
                        "operator": "INVALID",
                        "threshold": 0.45,
                        "deadline": "2027-01-01",
                    },
                ]
            },
            "model": "haiku",
            "input_tokens": 100,
            "output_tokens": 100,
            "cost_usd": 0.01,
        }

        with (
            patch("analysis.thesis_generator.handler.LLMClient") as mock_llm_cls,
            patch("analysis.thesis_generator.handler.store_analysis_result"),
            patch("analysis.thesis_generator.handler.S3Client") as mock_s3_cls,
        ):
            mock_llm = MagicMock()
            mock_llm.invoke.side_effect = [_LLM_THESIS_RESPONSE, bad_predictions]
            mock_llm_cls.return_value = mock_llm
            mock_s3_cls.return_value = MagicMock()

            from analysis.thesis_generator.handler import handler

            result = handler(_QUALIFYING_EVENT, None)

        assert result["thesis_generated"] is True
        # Only 1 of 3 predictions is valid (revenue one)
        assert len(result["predictions"]) == 1
        assert result["predictions"][0]["metric"] == "revenue"

    def test_skipped_thesis_has_no_prediction_call(self, monkeypatch):
        """When thesis is skipped (gate not met), no prediction extraction call is made."""
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        event = {**_QUALIFYING_EVENT, "moat_score": 3}

        with patch("analysis.thesis_generator.handler.LLMClient") as mock_llm_cls:
            from analysis.thesis_generator.handler import handler

            result = handler(event, None)

        assert result["thesis_generated"] is False
        assert "predictions" not in result
        mock_llm_cls.return_value.invoke.assert_not_called()
