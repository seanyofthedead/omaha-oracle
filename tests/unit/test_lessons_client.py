"""
Unit tests for LessonsClient — feedback loop.

- Relevance scoring: ticker match > sector match > stage match > general
- Expired lessons filtered out
- Confidence adjustment geometric mean calculation
- Empty result when no lessons exist
- Prompt injection formatting includes header and lesson text
- Max lessons limit respected
"""
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from shared.lessons_client import (
    ADJUSTMENT_MAX,
    ADJUSTMENT_MIN,
    HEADER,
    LessonsClient,
)

from tests.fixtures.mock_data import (
    LESSON_CONFIDENCE_08,
    LESSON_CONFIDENCE_125,
    LESSON_EXPIRED,
    LESSON_SECTOR_TECH,
    LESSON_STAGE_MOAT,
    LESSON_TICKER_AAPL,
    make_lesson,
)


def _seed_lesson(table, lesson: dict) -> None:
    """Put lesson into DynamoDB, converting floats to Decimal."""
    def _to_decimal(obj):
        if isinstance(obj, dict):
            return {k: _to_decimal(v) for k, v in obj.items()}
        if isinstance(obj, float):
            return Decimal(str(obj))
        return obj

    table.put_item(Item=_to_decimal(lesson))


class TestRelevanceScoring:
    """Relevance: ticker > sector > stage > general."""

    def test_ticker_match_highest_score(self, lessons_client: LessonsClient, lessons_table):
        _seed_lesson(lessons_table, LESSON_TICKER_AAPL)
        _seed_lesson(lessons_table, LESSON_SECTOR_TECH)
        result = lessons_client.get_relevant_lessons(
            ticker="AAPL",
            sector="Technology",
            industry="Consumer Electronics",
            analysis_stage="moat_analysis",
            max_lessons=5,
        )
        assert "Apple" in result or "AAPL" in result or "ecosystem" in result
        assert result.startswith(HEADER)

    def test_sector_match_included(self, lessons_client: LessonsClient, lessons_table):
        _seed_lesson(lessons_table, LESSON_SECTOR_TECH)
        result = lessons_client.get_relevant_lessons(
            ticker="MSFT",
            sector="Technology",
            industry="Software",
            analysis_stage="moat_analysis",
        )
        assert "recurring revenue" in result or "Tech" in result

    def test_stage_match_included(self, lessons_client: LessonsClient, lessons_table):
        _seed_lesson(lessons_table, LESSON_STAGE_MOAT)
        result = lessons_client.get_relevant_lessons(
            ticker="WMT",
            sector="Consumer Defensive",
            industry="Retail",
            analysis_stage="moat_analysis",
        )
        assert "moat bias" in result.lower() or "retail" in result.lower()


class TestExpiredLessonsFiltered:
    """Expired lessons filtered out."""

    def test_expired_not_returned(self, lessons_client: LessonsClient, lessons_table):
        _seed_lesson(lessons_table, LESSON_EXPIRED)
        result = lessons_client.get_relevant_lessons(
            ticker="AAPL",
            sector="Technology",
            industry="Consumer Electronics",
            analysis_stage="moat_analysis",
        )
        assert result == ""


class TestConfidenceAdjustment:
    """Geometric mean of adjustment_factor, clamped to [0.5, 1.5]."""

    def test_geometric_mean_two_factors(self, lessons_client: LessonsClient, lessons_table):
        _seed_lesson(lessons_table, LESSON_CONFIDENCE_08)
        _seed_lesson(lessons_table, LESSON_CONFIDENCE_125)
        adj = lessons_client.get_confidence_adjustment("moat_analysis", "Technology")
        # geo mean of 0.8 and 1.25 = exp((ln(0.8) + ln(1.25))/2) = exp((ln1)/2) = 1.0
        expected = math.exp((math.log(0.8) + math.log(1.25)) / 2)
        assert adj == pytest.approx(expected, rel=0.01)
        assert ADJUSTMENT_MIN <= adj <= ADJUSTMENT_MAX

    def test_no_lessons_returns_one(self, lessons_client: LessonsClient):
        adj = lessons_client.get_confidence_adjustment("moat_analysis", "Technology")
        assert adj == 1.0

    def test_clamped_to_bounds(self, lessons_client: LessonsClient, lessons_table):
        extreme_low = make_lesson(
            "confidence_calibration", "L-low",
            confidence_calibration={"analysis_stage": "moat_analysis", "sector": "Tech", "adjustment_factor": Decimal("0.1")},
        )
        _seed_lesson(lessons_table, extreme_low)
        adj = lessons_client.get_confidence_adjustment("moat_analysis", "Tech")
        assert adj >= ADJUSTMENT_MIN


class TestEmptyResult:
    """Empty result when no lessons exist."""

    def test_no_lessons_returns_empty_string(self, lessons_client: LessonsClient):
        result = lessons_client.get_relevant_lessons(
            ticker="XYZ",
            sector="Unknown",
            industry="Unknown",
            analysis_stage="moat_analysis",
        )
        assert result == ""


class TestPromptInjectionFormat:
    """Prompt injection includes header and lesson text."""

    def test_includes_header(self, lessons_client: LessonsClient, lessons_table):
        _seed_lesson(lessons_table, LESSON_TICKER_AAPL)
        result = lessons_client.get_relevant_lessons(
            ticker="AAPL",
            sector="Technology",
            industry="Consumer Electronics",
            analysis_stage="moat_analysis",
        )
        assert result.startswith(HEADER)
        assert "Apple" in result or "ecosystem" in result
        assert "Q1_2025" in result or "MODERATE" in result


class TestMaxLessonsLimit:
    """Max lessons limit respected."""

    def test_max_lessons_respected(self, lessons_client: LessonsClient, lessons_table):
        for i in range(10):
            lesson = make_lesson("moat_bias", f"L{i}", sector="Technology", prompt_injection_text=f"Lesson {i}")
            _seed_lesson(lessons_table, lesson)
        result = lessons_client.get_relevant_lessons(
            ticker="MSFT",
            sector="Technology",
            industry="Software",
            analysis_stage="moat_analysis",
            max_lessons=3,
        )
        lines = [l for l in result.split("\n") if l.strip().startswith("-")]
        assert len(lines) <= 3
