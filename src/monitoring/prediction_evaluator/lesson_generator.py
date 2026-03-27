"""
Auto-generate structured lessons from falsified predictions — no LLM call required.

Conforms exactly to the lesson schema in owners_letter/pipeline.py so lessons
are picked up by LessonsClient.get_relevant_lessons() and get_confidence_adjustment().
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from shared.dynamo_client import DynamoClient
from shared.logger import get_logger

from .metrics import METRIC_TO_STAGE

_log = get_logger(__name__)

# Minimum predictions evaluated per (stage, sector) before writing calibration lessons
MIN_SAMPLE_SIZE = 10

# Confidence adjustment bounds
CALIBRATION_FACTOR_MIN = 0.5
CALIBRATION_FACTOR_MAX = 1.3

# Default lesson expiry in quarters
DEFAULT_EXPIRY_QUARTERS = 4


def _severity_from_miss(threshold: float, actual: float) -> str:
    """Derive lesson severity from the magnitude of the prediction miss."""
    if threshold == 0:
        return "moderate"
    miss_pct = abs(actual - threshold) / abs(threshold)
    if miss_pct >= 0.50:
        return "high"
    if miss_pct >= 0.20:
        return "moderate"
    return "minor"


def _current_quarter_label() -> str:
    now = datetime.now(UTC)
    q = (now.month - 1) // 3 + 1
    return f"Q{q}_{now.year}"


def generate_lessons_from_results(
    evaluation_results: list[dict[str, Any]],
    lessons_client: DynamoClient,
) -> list[dict[str, Any]]:
    """Generate and store micro-lessons for FALSIFIED predictions.

    Also tracks prediction accuracy per (analysis_stage, sector) and writes
    confidence_calibration lessons when sample size reaches MIN_SAMPLE_SIZE.

    Returns list of lesson items that were stored.
    """
    now = datetime.now(UTC)
    created = now.isoformat()
    q_label = _current_quarter_label()

    # Track accuracy per (stage, sector) across all results (confirmed + falsified)
    accuracy_tracker: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"total": 0, "correct": 0}
    )

    lessons_stored: list[dict[str, Any]] = []

    for result in evaluation_results:
        status = result["status"]
        if status == "UNRESOLVABLE":
            continue

        pred = result.get("prediction", {})
        metric = pred.get("metric", "")
        stage = pred.get("analysis_stage") or METRIC_TO_STAGE.get(metric, "intrinsic_value")
        sector = (result.get("sector") or "ALL").strip()
        sector_key = sector.lower() if sector != "ALL" else "all"

        key = (stage, sector_key)
        accuracy_tracker[key]["total"] += 1
        if status == "CONFIRMED":
            accuracy_tracker[key]["correct"] += 1

        if status != "FALSIFIED":
            continue

        ticker = result.get("ticker", "")
        actual = result.get("actual_value")
        threshold = float(pred.get("threshold", 0))
        operator_str = pred.get("operator", ">")
        deadline = pred.get("deadline", "")
        description_text = pred.get("description", "")

        severity = _severity_from_miss(threshold, actual) if actual is not None else "moderate"
        expiry_date = now + timedelta(days=DEFAULT_EXPIRY_QUARTERS * 91)

        lesson_id = f"PRED_{ticker}_{uuid.uuid4().hex[:8]}"

        actual_str = f"{actual}" if actual is not None else "unavailable"

        lesson = {
            "lesson_type": "prediction_miss",
            "lesson_id": lesson_id,
            "severity": severity,
            "description": (
                f"Predicted {metric} {operator_str} {threshold} by {deadline} "
                f"for {ticker}. Actual: {actual_str}."
            ),
            "actionable_rule": (
                f"When analyzing {sector} companies, reduce confidence in "
                f"{metric} predictions. Original prediction: {description_text}"
            ),
            "prompt_injection_text": (
                f"CAUTION: Past {metric} predictions for {sector} stocks have been "
                f"inaccurate. The system predicted {metric} {operator_str} {threshold} "
                f"for {ticker} but actual was {actual_str}. Weight {metric}-based "
                f"assumptions conservatively."
            ),
            "ticker": ticker,
            "sector": sector,
            "quarter": q_label,
            "created_at": created,
            "expires_at": expiry_date.isoformat(),
            "expiry_quarters": DEFAULT_EXPIRY_QUARTERS,
            "active": True,
            "active_flag": "1",
        }

        try:
            lessons_client.put_item(lesson)
            lessons_stored.append(lesson)
        except Exception:
            _log.exception(
                "Failed to write prediction lesson",
                extra={"lesson_id": lesson_id, "ticker": ticker},
            )

    # Write confidence_calibration lessons for (stage, sector) pairs with enough data
    for (stage, sector_key), counts in accuracy_tracker.items():
        if counts["total"] < MIN_SAMPLE_SIZE:
            continue

        accuracy = counts["correct"] / counts["total"]
        adjustment_factor = max(CALIBRATION_FACTOR_MIN, min(CALIBRATION_FACTOR_MAX, accuracy))

        sector_display = sector_key.title() if sector_key != "all" else "ALL"
        cal_lesson_id = f"PRED_CAL_{stage}_{sector_key}_{q_label}"

        cal_lesson = {
            "lesson_type": "confidence_calibration",
            "lesson_id": cal_lesson_id,
            "severity": "minor",
            "description": (
                f"Prediction accuracy for {stage}/{sector_display}: "
                f"{accuracy:.0%} ({counts['correct']}/{counts['total']})"
            ),
            "actionable_rule": (
                f"Adjust confidence for {stage} in {sector_display} sector "
                f"based on {accuracy:.0%} prediction accuracy."
            ),
            "prompt_injection_text": "",
            "ticker": "",
            "sector": sector_display,
            "quarter": q_label,
            "created_at": created,
            "expires_at": (now + timedelta(days=DEFAULT_EXPIRY_QUARTERS * 91)).isoformat(),
            "expiry_quarters": DEFAULT_EXPIRY_QUARTERS,
            "active": True,
            "active_flag": "1",
            "confidence_calibration": {
                "analysis_stage": stage,
                "sector": sector_display,
                "bias_direction": "over" if accuracy < 0.5 else "under",
                "adjustment_factor": adjustment_factor,
            },
        }

        try:
            lessons_client.put_item(cal_lesson)
            lessons_stored.append(cal_lesson)
            _log.info(
                "Confidence calibration lesson written",
                extra={
                    "stage": stage,
                    "sector": sector_display,
                    "accuracy": accuracy,
                    "adjustment_factor": adjustment_factor,
                    "sample_size": counts["total"],
                },
            )
        except Exception:
            _log.exception(
                "Failed to write calibration lesson",
                extra={"lesson_id": cal_lesson_id},
            )

    return lessons_stored
