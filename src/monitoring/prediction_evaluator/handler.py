"""
Lambda handler for weekly prediction evaluation.

Scheduled via EventBridge (Wednesday 07:00 UTC). Scans for matured predictions,
evaluates against actual metrics, generates micro-lessons for failures, and
updates confidence calibration.
"""

from __future__ import annotations

from typing import Any

from shared.config import get_config
from shared.dynamo_client import DynamoClient
from shared.logger import get_logger

from .evaluator import evaluate_matured_predictions
from .lesson_generator import generate_lessons_from_results

_log = get_logger(__name__)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda entry point for weekly prediction evaluation."""
    cfg = get_config()

    decisions_client = DynamoClient(cfg.table_decisions)
    companies_client = DynamoClient(cfg.table_companies)
    financials_client = DynamoClient(cfg.table_financials)
    lessons_client = DynamoClient(cfg.table_lessons)

    _log.info("Starting prediction evaluation")

    evaluation_results = evaluate_matured_predictions(
        decisions_client, companies_client, financials_client
    )

    confirmed = sum(1 for r in evaluation_results if r["status"] == "CONFIRMED")
    falsified = sum(1 for r in evaluation_results if r["status"] == "FALSIFIED")
    unresolvable = sum(1 for r in evaluation_results if r["status"] == "UNRESOLVABLE")

    lessons_stored = generate_lessons_from_results(evaluation_results, lessons_client)

    summary = {
        "evaluated_count": len(evaluation_results),
        "confirmed": confirmed,
        "falsified": falsified,
        "unresolvable": unresolvable,
        "lessons_created": len(lessons_stored),
    }

    _log.info("Prediction evaluation complete", extra=summary)
    return summary
