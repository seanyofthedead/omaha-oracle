"""
Regression tests for three HIGH-severity self-improvement loop bugs:

1. Lessons never injected into moat/management prompts
2. prediction_miss lessons invisible (not in _stage_to_lesson_types allowlist)
3. management_assessment vs management_quality screen_type mismatch
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from shared.lessons_client import LessonsClient, _stage_to_lesson_types
from tests.fixtures.mock_data import make_lesson


# ------------------------------------------------------------------ #
# Helper                                                               #
# ------------------------------------------------------------------ #


def _seed_lesson(table, lesson: dict) -> None:
    """Put lesson into DynamoDB, converting floats to Decimal."""

    def _to_decimal(obj):
        if isinstance(obj, dict):
            return {k: _to_decimal(v) for k, v in obj.items()}
        if isinstance(obj, float):
            return Decimal(str(obj))
        return obj

    table.put_item(Item=_to_decimal(lesson))


# ------------------------------------------------------------------ #
# Bug 1: Moat handler must call LessonsClient.get_relevant_lessons     #
# ------------------------------------------------------------------ #


class TestMoatHandlerInjectsLessons:
    """Moat analysis handler should import and call LessonsClient."""

    def test_moat_handler_calls_get_relevant_lessons(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_USD", "100.0")

        with (
            patch("analysis.moat_analysis.handler.LLMClient") as mock_llm_cls,
            patch("analysis.moat_analysis.handler.store_analysis_result"),
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
            mock_llm.invoke.return_value = {
                "content": {
                    "moat_type": "narrow",
                    "moat_sources": ["switching costs"],
                    "moat_score": 6,
                    "moat_trend": "stable",
                    "pricing_power": 5,
                    "customer_captivity": 6,
                    "reasoning": "Moderate moat.",
                    "risks_to_moat": [],
                    "confidence": 0.7,
                },
                "cost_usd": 0.01,
            }
            mock_llm_cls.return_value = mock_llm

            mock_s3 = MagicMock()
            mock_s3.get_filing_context.return_value = ("No context.", False)
            mock_s3_cls.return_value = mock_s3

            mock_lessons = MagicMock()
            mock_lessons.get_relevant_lessons.return_value = "LESSON: Be skeptical of moats."
            mock_lessons_cls.return_value = mock_lessons

            from analysis.moat_analysis.handler import handler

            handler(
                {"ticker": "AAPL", "metrics": {"sector": "Technology", "industry": "Consumer Electronics"}},
                None,
            )

            # LessonsClient must have been called with correct args
            mock_lessons.get_relevant_lessons.assert_called_once()
            call_kwargs = mock_lessons.get_relevant_lessons.call_args
            # Should pass ticker, sector, industry, and stage="moat_analysis"
            args = call_kwargs[1] if call_kwargs[1] else {}
            positional = call_kwargs[0] if call_kwargs[0] else ()
            # The call should include the ticker and stage
            all_args = list(positional) + list(args.values())
            assert "AAPL" in all_args or any("AAPL" in str(a) for a in all_args)

            # The lesson text should appear in the system_prompt passed to LLM
            llm_call_kwargs = mock_llm.invoke.call_args
            system_prompt_used = llm_call_kwargs[1].get("system_prompt", "") if llm_call_kwargs[1] else ""
            if not system_prompt_used:
                # Check positional args
                system_prompt_used = str(llm_call_kwargs)
            assert "LESSON: Be skeptical of moats." in system_prompt_used or \
                   "Be skeptical" in str(llm_call_kwargs)


# ------------------------------------------------------------------ #
# Bug 1b: Management handler must call LessonsClient.get_relevant_lessons
# ------------------------------------------------------------------ #


class TestManagementHandlerInjectsLessons:
    """Management quality handler should import and call LessonsClient."""

    def test_management_handler_calls_get_relevant_lessons(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_USD", "100.0")

        with (
            patch("analysis.management_quality.handler.LLMClient") as mock_llm_cls,
            patch("analysis.management_quality.handler.store_analysis_result"),
            patch("analysis.management_quality.handler.CostTracker") as mock_tracker_cls,
            patch("analysis.management_quality.handler.S3Client") as mock_s3_cls,
            patch("analysis.management_quality.handler.LessonsClient") as mock_lessons_cls,
        ):
            mock_tracker = MagicMock()
            mock_tracker.check_budget.return_value = {
                "exhausted": False,
                "spent_usd": 5.0,
                "budget_usd": 100.0,
            }
            mock_tracker_cls.return_value = mock_tracker

            mock_llm = MagicMock()
            mock_llm.invoke.return_value = {
                "content": {
                    "owner_operator_mindset": 7,
                    "capital_allocation_skill": 8,
                    "candor_transparency": 7,
                    "management_score": 7,
                    "red_flags": [],
                    "green_flags": ["good capital allocation"],
                    "reasoning": "Decent management.",
                    "confidence": 0.75,
                },
                "cost_usd": 0.008,
            }
            mock_llm_cls.return_value = mock_llm

            mock_s3 = MagicMock()
            mock_s3.get_filing_context.return_value = ("No context.", False)
            mock_s3_cls.return_value = mock_s3

            mock_lessons = MagicMock()
            mock_lessons.get_relevant_lessons.return_value = "LESSON: Watch for empire building."
            mock_lessons_cls.return_value = mock_lessons

            from analysis.management_quality.handler import handler

            handler(
                {"ticker": "MSFT", "moat_score": 7, "metrics": {"sector": "Technology"}},
                None,
            )

            # LessonsClient must have been called
            mock_lessons.get_relevant_lessons.assert_called_once()

            # The lesson text should appear in the system_prompt passed to LLM
            llm_call_kwargs = mock_llm.invoke.call_args
            system_prompt_used = llm_call_kwargs[1].get("system_prompt", "") if llm_call_kwargs[1] else ""
            if not system_prompt_used:
                system_prompt_used = str(llm_call_kwargs)
            assert "LESSON: Watch for empire building." in system_prompt_used or \
                   "Watch for empire building" in str(llm_call_kwargs)


# ------------------------------------------------------------------ #
# Bug 2: prediction_miss lessons must be retrievable                   #
# ------------------------------------------------------------------ #


class TestPredictionMissLessonsRetrievable:
    """prediction_miss lessons should be included in _stage_to_lesson_types."""

    def test_prediction_miss_in_moat_analysis_stage_types(self):
        types = _stage_to_lesson_types("moat_analysis")
        assert "prediction_miss" in types

    def test_prediction_miss_in_management_quality_stage_types(self):
        types = _stage_to_lesson_types("management_quality")
        assert "prediction_miss" in types

    def test_prediction_miss_in_intrinsic_value_stage_types(self):
        types = _stage_to_lesson_types("intrinsic_value")
        assert "prediction_miss" in types

    def test_prediction_miss_in_thesis_generator_stage_types(self):
        types = _stage_to_lesson_types("thesis_generator")
        assert "prediction_miss" in types

    def test_prediction_miss_lesson_retrieved_via_get_relevant_lessons(
        self, lessons_client: LessonsClient, lessons_table
    ):
        """A stored prediction_miss lesson should appear in results for moat_analysis."""
        lesson = make_lesson(
            "prediction_miss",
            "PRED_AAPL_abc123",
            ticker="AAPL",
            sector="Technology",
            severity="high",
            prompt_injection_text=(
                "CAUTION: Past revenue_growth predictions for Technology stocks "
                "have been inaccurate."
            ),
        )
        _seed_lesson(lessons_table, lesson)

        result = lessons_client.get_relevant_lessons(
            ticker="AAPL",
            sector="Technology",
            industry="Consumer Electronics",
            analysis_stage="moat_analysis",
        )
        assert "CAUTION" in result
        assert "revenue_growth" in result


# ------------------------------------------------------------------ #
# Bug 3: management_quality handler must use screen_type="management_quality"
# ------------------------------------------------------------------ #


class TestManagementScreenType:
    """Management handler must store screen_type='management_quality', not 'management_assessment'."""

    def test_management_handler_stores_management_quality_screen_type(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_USD", "100.0")

        with (
            patch("analysis.management_quality.handler.LLMClient") as mock_llm_cls,
            patch("analysis.management_quality.handler.store_analysis_result") as mock_store,
            patch("analysis.management_quality.handler.CostTracker") as mock_tracker_cls,
            patch("analysis.management_quality.handler.S3Client") as mock_s3_cls,
            patch("analysis.management_quality.handler.LessonsClient") as mock_lessons_cls,
        ):
            mock_tracker = MagicMock()
            mock_tracker.check_budget.return_value = {
                "exhausted": False,
                "spent_usd": 5.0,
                "budget_usd": 100.0,
            }
            mock_tracker_cls.return_value = mock_tracker

            mock_llm = MagicMock()
            mock_llm.invoke.return_value = {
                "content": {
                    "owner_operator_mindset": 7,
                    "capital_allocation_skill": 8,
                    "candor_transparency": 7,
                    "management_score": 7,
                    "red_flags": [],
                    "green_flags": [],
                    "reasoning": "OK.",
                    "confidence": 0.7,
                },
                "cost_usd": 0.01,
            }
            mock_llm_cls.return_value = mock_llm

            mock_s3 = MagicMock()
            mock_s3.get_filing_context.return_value = ("No context.", False)
            mock_s3_cls.return_value = mock_s3

            mock_lessons = MagicMock()
            mock_lessons.get_relevant_lessons.return_value = ""
            mock_lessons_cls.return_value = mock_lessons

            from analysis.management_quality.handler import handler

            handler({"ticker": "AAPL", "moat_score": 7}, None)

        # All store_analysis_result calls must use "management_quality", not "management_assessment"
        for call in mock_store.call_args_list:
            screen_type_arg = call[0][2]  # 3rd positional arg is screen_type
            assert screen_type_arg == "management_quality", (
                f"Expected screen_type='management_quality', got '{screen_type_arg}'"
            )

    def test_budget_exhausted_stores_management_quality_screen_type(self, monkeypatch):
        """Even the budget-exhausted path must use the correct screen_type."""
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        with (
            patch("analysis.management_quality.handler.LLMClient"),
            patch("analysis.management_quality.handler.store_analysis_result") as mock_store,
            patch("analysis.management_quality.handler.CostTracker") as mock_tracker_cls,
            patch("analysis.management_quality.handler.S3Client"),
        ):
            mock_tracker = MagicMock()
            mock_tracker.check_budget.return_value = {
                "exhausted": True,
                "spent_usd": 110.0,
                "budget_usd": 100.0,
            }
            mock_tracker_cls.return_value = mock_tracker

            from analysis.management_quality.handler import handler

            handler({"ticker": "AAPL"}, None)

        for call in mock_store.call_args_list:
            screen_type_arg = call[0][2]
            assert screen_type_arg == "management_quality", (
                f"Expected screen_type='management_quality', got '{screen_type_arg}'"
            )
