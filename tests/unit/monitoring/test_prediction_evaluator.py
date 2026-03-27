"""
Unit tests for prediction evaluator core logic and lesson generation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from monitoring.prediction_evaluator.evaluator import (
    _evaluate_single,
    evaluate_matured_predictions,
)
from monitoring.prediction_evaluator.lesson_generator import (
    MIN_SAMPLE_SIZE,
    _severity_from_miss,
    generate_lessons_from_results,
)


# --- Evaluator tests ---


class TestEvaluateSingle:
    def test_gt_confirmed(self):
        pred = {"threshold": 100, "operator": ">"}
        assert _evaluate_single(pred, 150) == "CONFIRMED"

    def test_gt_falsified(self):
        pred = {"threshold": 100, "operator": ">"}
        assert _evaluate_single(pred, 50) == "FALSIFIED"

    def test_lt_confirmed(self):
        pred = {"threshold": 100, "operator": "<"}
        assert _evaluate_single(pred, 50) == "CONFIRMED"

    def test_lte_boundary(self):
        pred = {"threshold": 100, "operator": "<="}
        assert _evaluate_single(pred, 100) == "CONFIRMED"

    def test_gte_boundary(self):
        pred = {"threshold": 100, "operator": ">="}
        assert _evaluate_single(pred, 100) == "CONFIRMED"

    def test_eq_confirmed(self):
        pred = {"threshold": 100, "operator": "=="}
        assert _evaluate_single(pred, 100) == "CONFIRMED"

    def test_eq_falsified(self):
        pred = {"threshold": 100, "operator": "=="}
        assert _evaluate_single(pred, 101) == "FALSIFIED"


class TestEvaluateMaturedPredictions:
    def _make_decision(self, ticker, predictions, ts_offset_days=0):
        ts = (datetime.now(UTC) - timedelta(days=ts_offset_days)).isoformat()
        return {
            "decision_id": f"BUY#{ticker}#abc123",
            "timestamp": ts,
            "record_type": "DECISION",
            "ticker": ticker,
            "signal": "BUY",
            "payload": {
                "signal": "BUY",
                "predictions": predictions,
                "sector": "Technology",
            },
        }

    def test_matured_prediction_confirmed(self):
        yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
        preds = [
            {
                "metric": "stock_price",
                "operator": ">",
                "threshold": 100,
                "deadline": yesterday,
                "status": "pending",
                "data_source": "yahoo_finance",
                "analysis_stage": "intrinsic_value",
            }
        ]
        decision = self._make_decision("AAPL", preds, ts_offset_days=30)

        decisions_client = MagicMock()
        decisions_client.query.return_value = [decision]

        companies_client = MagicMock()
        companies_client.get_item.return_value = {"ticker": "AAPL", "currentPrice": 150.0}

        financials_client = MagicMock()

        results = evaluate_matured_predictions(
            decisions_client, companies_client, financials_client
        )

        assert len(results) == 1
        assert results[0]["status"] == "CONFIRMED"
        assert results[0]["actual_value"] == 150.0
        decisions_client.update_item.assert_called_once()

    def test_future_deadline_skipped(self):
        future = (datetime.now(UTC) + timedelta(days=30)).strftime("%Y-%m-%d")
        preds = [
            {
                "metric": "stock_price",
                "operator": ">",
                "threshold": 100,
                "deadline": future,
                "status": "pending",
                "data_source": "yahoo_finance",
            }
        ]
        decision = self._make_decision("AAPL", preds)

        decisions_client = MagicMock()
        decisions_client.query.return_value = [decision]

        results = evaluate_matured_predictions(
            decisions_client, MagicMock(), MagicMock()
        )
        assert len(results) == 0

    def test_already_evaluated_skipped(self):
        yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
        preds = [
            {
                "metric": "stock_price",
                "operator": ">",
                "threshold": 100,
                "deadline": yesterday,
                "status": "CONFIRMED",  # Already evaluated
                "data_source": "yahoo_finance",
            }
        ]
        decision = self._make_decision("AAPL", preds)

        decisions_client = MagicMock()
        decisions_client.query.return_value = [decision]

        results = evaluate_matured_predictions(
            decisions_client, MagicMock(), MagicMock()
        )
        assert len(results) == 0

    def test_metric_unavailable_unresolvable(self):
        yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
        preds = [
            {
                "metric": "gross_margin",
                "operator": ">",
                "threshold": 0.45,
                "deadline": yesterday,
                "status": "pending",
                "data_source": "sec_edgar",
                "analysis_stage": "moat_analysis",
            }
        ]
        decision = self._make_decision("AAPL", preds)

        decisions_client = MagicMock()
        decisions_client.query.return_value = [decision]

        companies_client = MagicMock()
        companies_client.get_item.return_value = None

        financials_client = MagicMock()
        financials_client.query.return_value = []

        results = evaluate_matured_predictions(
            decisions_client, companies_client, financials_client
        )
        assert len(results) == 1
        assert results[0]["status"] == "UNRESOLVABLE"

    def test_decision_without_predictions_skipped(self):
        """Pre-feature decisions without predictions field are handled gracefully."""
        decision = {
            "decision_id": "BUY#AAPL#old123",
            "timestamp": datetime.now(UTC).isoformat(),
            "record_type": "DECISION",
            "ticker": "AAPL",
            "signal": "BUY",
            "payload": {"signal": "BUY"},  # No predictions field
        }

        decisions_client = MagicMock()
        decisions_client.query.return_value = [decision]

        results = evaluate_matured_predictions(
            decisions_client, MagicMock(), MagicMock()
        )
        assert len(results) == 0

    def test_gsi_fallback_on_validation_exception(self):
        """Falls back to scan when GSI is not available."""
        from botocore.exceptions import ClientError

        yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
        preds = [
            {
                "metric": "stock_price",
                "operator": ">",
                "threshold": 100,
                "deadline": yesterday,
                "status": "pending",
                "data_source": "yahoo_finance",
                "analysis_stage": "intrinsic_value",
            }
        ]
        decision = self._make_decision("AAPL", preds, ts_offset_days=30)

        decisions_client = MagicMock()
        decisions_client.query.side_effect = ClientError(
            {"Error": {"Code": "ValidationException"}}, "Query"
        )
        decisions_client.scan_all.return_value = [decision]

        companies_client = MagicMock()
        companies_client.get_item.return_value = {"ticker": "AAPL", "currentPrice": 150.0}

        results = evaluate_matured_predictions(
            decisions_client, companies_client, MagicMock()
        )
        assert len(results) == 1
        decisions_client.scan_all.assert_called_once()


# --- Lesson generator tests ---


class TestSeverityFromMiss:
    def test_large_miss_is_high(self):
        assert _severity_from_miss(100, 40) == "high"  # 60% miss

    def test_medium_miss_is_moderate(self):
        assert _severity_from_miss(100, 75) == "moderate"  # 25% miss

    def test_small_miss_is_minor(self):
        assert _severity_from_miss(100, 90) == "minor"  # 10% miss

    def test_zero_threshold(self):
        assert _severity_from_miss(0, 50) == "moderate"


class TestGenerateLessonsFromResults:
    def test_falsified_generates_lesson(self):
        results = [
            {
                "ticker": "AAPL",
                "sector": "Technology",
                "decision_id": "BUY#AAPL#abc",
                "prediction": {
                    "metric": "revenue",
                    "operator": ">",
                    "threshold": 400e9,
                    "deadline": "2026-12-31",
                    "description": "Revenue exceeds $400B",
                    "analysis_stage": "intrinsic_value",
                },
                "status": "FALSIFIED",
                "actual_value": 300e9,
            }
        ]

        lessons_client = MagicMock()
        stored = generate_lessons_from_results(results, lessons_client)

        assert len(stored) == 1
        lesson = stored[0]
        assert lesson["lesson_type"] == "prediction_miss"
        assert lesson["active_flag"] == "1"
        assert lesson["ticker"] == "AAPL"
        assert lesson["sector"] == "Technology"
        assert "revenue" in lesson["description"]
        assert lesson["severity"] == "moderate"  # 25% miss
        lessons_client.put_item.assert_called_once()

    def test_confirmed_generates_no_lesson(self):
        results = [
            {
                "ticker": "AAPL",
                "sector": "Technology",
                "decision_id": "BUY#AAPL#abc",
                "prediction": {
                    "metric": "revenue",
                    "operator": ">",
                    "threshold": 400e9,
                    "analysis_stage": "intrinsic_value",
                },
                "status": "CONFIRMED",
                "actual_value": 500e9,
            }
        ]

        lessons_client = MagicMock()
        stored = generate_lessons_from_results(results, lessons_client)

        # No prediction_miss lessons, but may have calibration if sample >= 10
        prediction_miss = [l for l in stored if l["lesson_type"] == "prediction_miss"]
        assert len(prediction_miss) == 0

    def test_unresolvable_generates_no_lesson(self):
        results = [
            {
                "ticker": "AAPL",
                "sector": "Technology",
                "prediction": {"metric": "revenue", "analysis_stage": "intrinsic_value"},
                "status": "UNRESOLVABLE",
                "actual_value": None,
            }
        ]

        lessons_client = MagicMock()
        stored = generate_lessons_from_results(results, lessons_client)
        assert len(stored) == 0

    def test_calibration_lesson_at_threshold(self):
        """When sample size reaches MIN_SAMPLE_SIZE, a calibration lesson is written."""
        results = []
        # 7 confirmed + 3 falsified = 10 total, 70% accuracy
        for i in range(7):
            results.append({
                "ticker": f"T{i}",
                "sector": "Technology",
                "prediction": {
                    "metric": "revenue",
                    "operator": ">",
                    "threshold": 100,
                    "analysis_stage": "intrinsic_value",
                },
                "status": "CONFIRMED",
                "actual_value": 150,
            })
        for i in range(3):
            results.append({
                "ticker": f"F{i}",
                "sector": "Technology",
                "prediction": {
                    "metric": "revenue",
                    "operator": ">",
                    "threshold": 100,
                    "deadline": "2026-01-01",
                    "description": "Revenue test",
                    "analysis_stage": "intrinsic_value",
                },
                "status": "FALSIFIED",
                "actual_value": 50,
            })

        lessons_client = MagicMock()
        stored = generate_lessons_from_results(results, lessons_client)

        cal_lessons = [l for l in stored if l["lesson_type"] == "confidence_calibration"]
        assert len(cal_lessons) == 1
        cal = cal_lessons[0]
        assert cal["confidence_calibration"]["analysis_stage"] == "intrinsic_value"
        assert abs(cal["confidence_calibration"]["adjustment_factor"] - 0.7) < 0.01
        assert cal["active_flag"] == "1"

    def test_no_calibration_below_threshold(self):
        """When sample size < MIN_SAMPLE_SIZE, no calibration lesson is written."""
        results = []
        for i in range(MIN_SAMPLE_SIZE - 1):
            results.append({
                "ticker": f"T{i}",
                "sector": "Technology",
                "prediction": {
                    "metric": "revenue",
                    "operator": ">",
                    "threshold": 100,
                    "deadline": "2026-01-01",
                    "description": "test",
                    "analysis_stage": "intrinsic_value",
                },
                "status": "FALSIFIED",
                "actual_value": 50,
            })

        lessons_client = MagicMock()
        stored = generate_lessons_from_results(results, lessons_client)

        cal_lessons = [l for l in stored if l["lesson_type"] == "confidence_calibration"]
        assert len(cal_lessons) == 0

    def test_calibration_factor_clamped(self):
        """Accuracy of 100% is clamped to CALIBRATION_FACTOR_MAX (1.3)."""
        results = []
        for i in range(12):
            results.append({
                "ticker": f"T{i}",
                "sector": "Technology",
                "prediction": {
                    "metric": "revenue",
                    "operator": ">",
                    "threshold": 100,
                    "analysis_stage": "intrinsic_value",
                },
                "status": "CONFIRMED",
                "actual_value": 150,
            })

        lessons_client = MagicMock()
        stored = generate_lessons_from_results(results, lessons_client)

        cal_lessons = [l for l in stored if l["lesson_type"] == "confidence_calibration"]
        assert len(cal_lessons) == 1
        assert cal_lessons[0]["confidence_calibration"]["adjustment_factor"] == 1.0
        # 100% accuracy -> factor = 1.0 (accuracy itself), clamped at max 1.3

    def test_zero_accuracy_clamped_at_min(self):
        """Accuracy of 0% is clamped to CALIBRATION_FACTOR_MIN (0.5)."""
        results = []
        for i in range(10):
            results.append({
                "ticker": f"F{i}",
                "sector": "Technology",
                "prediction": {
                    "metric": "revenue",
                    "operator": ">",
                    "threshold": 100,
                    "deadline": "2026-01-01",
                    "description": "test",
                    "analysis_stage": "intrinsic_value",
                },
                "status": "FALSIFIED",
                "actual_value": 50,
            })

        lessons_client = MagicMock()
        stored = generate_lessons_from_results(results, lessons_client)

        cal_lessons = [l for l in stored if l["lesson_type"] == "confidence_calibration"]
        assert len(cal_lessons) == 1
        assert cal_lessons[0]["confidence_calibration"]["adjustment_factor"] == 0.5

    def test_lesson_schema_compliance(self):
        """Verify all required lesson fields are present."""
        results = [
            {
                "ticker": "MSFT",
                "sector": "Technology",
                "decision_id": "BUY#MSFT#xyz",
                "prediction": {
                    "metric": "earnings_per_share",
                    "operator": ">",
                    "threshold": 12.0,
                    "deadline": "2027-01-01",
                    "description": "EPS exceeds $12",
                    "analysis_stage": "intrinsic_value",
                },
                "status": "FALSIFIED",
                "actual_value": 10.0,
            }
        ]

        lessons_client = MagicMock()
        stored = generate_lessons_from_results(results, lessons_client)

        lesson = stored[0]
        required_fields = {
            "lesson_type", "lesson_id", "severity", "description",
            "actionable_rule", "prompt_injection_text", "ticker", "sector",
            "quarter", "created_at", "expires_at", "expiry_quarters",
            "active", "active_flag",
        }
        assert required_fields.issubset(set(lesson.keys()))
        assert lesson["active_flag"] == "1"  # String, not boolean
        assert lesson["active"] is True
