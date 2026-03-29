"""
Daily Lambda: LLM + AWS spend vs $67/month budget.

Gets LLM spend from CostTracker, AWS spend from Cost Explorer (Project=omaha-oracle),
sends SNS alert if utilization > 80%.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any

import boto3

from shared.config import get_config
from shared.cost_tracker import CostTracker
from shared.logger import get_logger

_log = get_logger(__name__)

ALERT_THRESHOLD_PCT = 80.0


def _monthly_budget_usd() -> float:
    """Read the monthly budget from config; fall back to 67.0 if config unavailable."""
    try:
        return get_config().monthly_llm_budget_usd
    except Exception:
        return 67.0


# Cost Explorer is only available in us-east-1
CE_REGION = "us-east-1"

# Reuse the boto3 connection pool across warm Lambda invocations
_CE_CLIENT = boto3.client("ce", region_name=CE_REGION)


@lru_cache(maxsize=1)
def _sns_client(region: str) -> Any:
    """Return a cached SNS client for the given region."""
    return boto3.client("sns", region_name=region)


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

    try:
        response = _CE_CLIENT.get_cost_and_usage(
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
        _log.error(
            "Cost Explorer unavailable — cannot compute accurate AWS spend",
            extra={"error": str(exc)},
        )
        raise

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
    monthly_budget = _monthly_budget_usd()
    now = datetime.now(UTC)
    date_str = now.strftime("%Y-%m-%d")
    month_key = now.strftime("%Y-%m")

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_llm = ex.submit(_get_llm_spend, month_key)
        f_aws = ex.submit(_get_aws_spend, month_key)
    llm_spend = f_llm.result()
    try:
        aws_spend = f_aws.result()
    except Exception as exc:
        _log.error(
            "AWS Cost Explorer failed — falling back to aws_spend=0.0",
            extra={"error": str(exc)},
        )
        aws_spend = 0.0
    total_spend = llm_spend + aws_spend

    utilization_pct = (total_spend / monthly_budget * 100) if monthly_budget > 0 else 0.0
    alert_triggered = utilization_pct > ALERT_THRESHOLD_PCT

    if alert_triggered:
        topic_arn = cfg.sns_topic_arn
        if topic_arn:
            try:
                sns = _sns_client(cfg.aws_region)
                msg = (
                    f"Omaha Oracle cost alert: {utilization_pct:.1f}% of ${monthly_budget:.0f} "
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
        "monthly_budget": monthly_budget,
        "utilization_pct": round(utilization_pct, 2),
        "alert_triggered": alert_triggered,
    }
