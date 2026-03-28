"""
Core evaluation engine for falsifiable predictions.

Scans the decisions table for matured pending predictions, fetches actual
metric values, classifies outcomes, and updates prediction status.
"""

from __future__ import annotations

import operator as op_module
from datetime import UTC, datetime
from typing import Any

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from shared.converters import safe_float
from shared.dynamo_client import DynamoClient
from shared.logger import get_logger

from .metrics import fetch_actual

_log = get_logger(__name__)

# Operator string to comparator function
_OPERATORS: dict[str, Any] = {
    ">": op_module.gt,
    "<": op_module.lt,
    ">=": op_module.ge,
    "<=": op_module.le,
    "==": op_module.eq,
}


def _evaluate_single(
    pred: dict[str, Any],
    actual: float,
) -> str:
    """Compare actual value against prediction threshold. Returns CONFIRMED or FALSIFIED."""
    threshold = safe_float(pred.get("threshold", 0))
    operator_str = pred.get("operator", ">")
    comparator = _OPERATORS.get(operator_str, op_module.gt)
    return "CONFIRMED" if comparator(actual, threshold) else "FALSIFIED"


def _update_prediction_status(
    decisions_client: DynamoClient,
    decision: dict[str, Any],
    pred_index: int,
    status: str,
    actual_value: float | None,
) -> None:
    """Update a single prediction's status and actual_value in the decisions table."""
    try:
        decisions_client.update_item(
            key={
                "decision_id": decision["decision_id"],
                "timestamp": decision["timestamp"],
            },
            update_expression=(
                f"SET payload.predictions[{pred_index}].#st = :status, "
                f"payload.predictions[{pred_index}].actual_value = :actual, "
                f"payload.predictions[{pred_index}].evaluated_at = :now"
            ),
            expression_attribute_names={"#st": "status"},
            expression_attribute_values={
                ":status": status,
                ":actual": actual_value,
                ":now": datetime.now(UTC).isoformat(),
            },
        )
    except Exception:
        _log.exception(
            "Failed to update prediction status",
            extra={
                "decision_id": decision.get("decision_id"),
                "pred_index": pred_index,
            },
        )


def evaluate_matured_predictions(
    decisions_client: DynamoClient,
    companies_client: DynamoClient,
    financials_client: DynamoClient,
) -> list[dict[str, Any]]:
    """Scan decisions for matured pending predictions, evaluate them.

    Returns a list of evaluation result dicts:
        {ticker, decision_id, prediction, status, actual_value}
    """
    now_iso = datetime.now(UTC).isoformat()
    now_date = datetime.now(UTC).strftime("%Y-%m-%d")

    # Query all DECISION records via GSI
    try:
        items = decisions_client.query(
            Key("record_type").eq("DECISION"),
            index_name="record_type-timestamp-index",
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ValidationException":
            _log.warning("GSI not available, falling back to scan")
            items = decisions_client.scan_all()
            items = [i for i in items if i.get("record_type") == "DECISION"]
        else:
            raise

    results: list[dict[str, Any]] = []

    for decision in items:
        payload = decision.get("payload") or {}
        predictions = payload.get("predictions")
        if not predictions or not isinstance(predictions, list):
            continue

        ticker = (decision.get("ticker") or "").strip().upper()
        if not ticker:
            continue

        for i, pred in enumerate(predictions):
            if not isinstance(pred, dict):
                continue
            if pred.get("status") != "pending":
                continue

            deadline = pred.get("deadline", "")
            if not deadline or deadline > now_date:
                continue

            metric = pred.get("metric", "")
            data_source = pred.get("data_source", "yahoo_finance")

            actual = fetch_actual(
                metric, ticker, data_source,
                companies_client, financials_client,
                as_of_date=deadline,
            )

            if actual is None:
                status = "UNRESOLVABLE"
                _log.info(
                    "Prediction unresolvable — metric unavailable",
                    extra={"ticker": ticker, "metric": metric},
                )
            else:
                status = _evaluate_single(pred, actual)

            _update_prediction_status(decisions_client, decision, i, status, actual)

            results.append({
                "ticker": ticker,
                "sector": payload.get("sector") or decision.get("sector") or "",
                "decision_id": decision.get("decision_id", ""),
                "prediction": pred,
                "status": status,
                "actual_value": actual,
            })

    _log.info(
        "Prediction evaluation complete",
        extra={
            "total_evaluated": len(results),
            "confirmed": sum(1 for r in results if r["status"] == "CONFIRMED"),
            "falsified": sum(1 for r in results if r["status"] == "FALSIFIED"),
            "unresolvable": sum(1 for r in results if r["status"] == "UNRESOLVABLE"),
        },
    )
    return results
