"""
Daily Lambda: LLM + AWS spend vs $67/month budget.

Gets LLM spend from CostTracker, AWS spend from Cost Explorer (Project=omaha-oracle),
sends SNS alert if utilization > 80%.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import boto3

from shared.config import get_config
from shared.cost_tracker import CostTracker
from shared.logger import get_logger

_log = get_logger(__name__)

MONTHLY_BUDGET_USD = 67.0
ALERT_THRESHOLD_PCT = 80.0

# Cost Explorer is only available in us-east-1
CE_REGION = "us-east-1"


def _get_llm_spend(month_key: str) -> float:
    """Get LLM spend for the month from CostTracker."""
    tracker = CostTracker()
    spent = tracker.get_monthly_spend(month_key)
    return float(spent)


def _get_aws_spend(month_key: str) -> float:
    """Get AWS spend for the month from Cost Explorer (Project=omaha-oracle)."""
    year, month = month_key.split("-")
    start = f"{year}-{month}-01"
    # End is exclusive: first day of next month
    month_int = int(month)
    if month_int == 12:
        end = f"{int(year) + 1}-01-01"
    else:
        end = f"{year}-{month_int + 1:02d}-01"

    client = boto3.client("ce", region_name=CE_REGION)
    try:
        response = client.get_cost_and_usage(
            TimePeriod={"Start": start, "End": end},
            Granularity="MONTHLY",
            Metrics=["BlendedCost"],
            Filter={
                "Tags": {
                    "Key": "Project",
                    "Values": ["omaha-oracle"],
                    "MatchOptions": ["EQUALS"],
                }
            },
        )
    except Exception as exc:
        _log.warning("Cost Explorer failed — assuming $0 AWS spend", extra={"error": str(exc)})
        return 0.0

    total = Decimal("0")
    for result in response.get("ResultsByTime", []):
        # With Filter, Total is at result level (no GroupBy)
        amount = result.get("Total", {}).get("BlendedCost", {}).get("Amount", "0")
        total += Decimal(str(amount))

    return float(total)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    Returns
    -------
    dict
        date, llm_spend, aws_spend, total_spend, monthly_budget,
        utilization_pct, alert_triggered
    """
    cfg = get_config()
    now = datetime.now(UTC)
    date_str = now.strftime("%Y-%m-%d")
    month_key = now.strftime("%Y-%m")

    llm_spend = _get_llm_spend(month_key)
    aws_spend = _get_aws_spend(month_key)
    total_spend = llm_spend + aws_spend

    utilization_pct = (
        (total_spend / MONTHLY_BUDGET_USD * 100) if MONTHLY_BUDGET_USD > 0 else 0.0
    )
    alert_triggered = utilization_pct > ALERT_THRESHOLD_PCT

    if alert_triggered:
        topic_arn = cfg.sns_topic_arn
        if topic_arn:
            try:
                sns = boto3.client("sns", region_name=cfg.aws_region)
                msg = (
                    f"Omaha Oracle cost alert: {utilization_pct:.1f}% of ${MONTHLY_BUDGET_USD:.0f} "
                    f"budget used (LLM: ${llm_spend:.2f}, AWS: ${aws_spend:.2f}). "
                    f"Date: {date_str}"
                )
                sns.publish(TopicArn=topic_arn, Subject="Omaha Oracle — Budget Alert", Message=msg)
                _log.info("SNS alert sent", extra={"utilization_pct": utilization_pct})
            except Exception as exc:
                _log.error("SNS publish failed", extra={"error": str(exc)})
        else:
            _log.warning("No SNS_TOPIC_ARN — alert not sent")

    return {
        "date": date_str,
        "llm_spend": round(llm_spend, 2),
        "aws_spend": round(aws_spend, 2),
        "total_spend": round(total_spend, 2),
        "monthly_budget": MONTHLY_BUDGET_USD,
        "utilization_pct": round(utilization_pct, 2),
        "alert_triggered": alert_triggered,
    }
