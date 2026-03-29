"""
Lambda handler for Graham-Dodd quantitative screening.

Pure math — no LLM calls. Screens all companies in the companies table
against configurable thresholds, stores results in the analysis table.
"""

from __future__ import annotations

from typing import Any

from shared.config import get_config
from shared.converters import today_str
from shared.dynamo_client import DynamoClient
from shared.logger import get_logger

from .financials import (
    _company_metrics,
    _load_thresholds,
)
from .screener import screen_company

_log = get_logger(__name__)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    Input: {} (screens all companies in companies table)
    Output: {"passing_tickers": [...], "total_screened": N, "total_passed": N}
    """
    cfg = get_config()
    companies_client = DynamoClient(cfg.table_companies)
    financials_client = DynamoClient(cfg.table_financials)
    config_client = DynamoClient(cfg.table_config)
    analysis_client = DynamoClient(cfg.table_analysis)

    thresholds = _load_thresholds(config_client)
    _log.debug("Loaded thresholds", extra={"thresholds": thresholds})
    sk = f"{today_str()}#quant_screen"

    companies = companies_client.scan_all()

    passing: list[dict[str, Any]] = []
    analysis_items: list[dict[str, Any]] = []

    for comp in companies:
        ticker = comp.get("ticker")
        if not ticker:
            continue

        result, passed = screen_company(ticker, comp, financials_client, thresholds)
        if not passed and "reason" in result and result["reason"] == "no_financials":
            _log.debug("No financials for ticker", extra={"ticker": ticker})
            continue
        if passed:
            company_name = comp.get("longName") or comp.get("shortName") or str(ticker)
            metrics = _company_metrics(comp, result)
            passing.append(
                {
                    "ticker": ticker,
                    "company_name": company_name,
                    "metrics": metrics,
                    "quant_result": result,
                }
            )

        # failed_criteria is now computed inside screen_company
        _log.debug(
            "Quant screen result",
            extra={
                "ticker": ticker,
                "failed_criteria": result.get("failed_criteria", []),
                "passed": passed,
            },
        )

        analysis_items.append(
            {
                "ticker": ticker,
                "analysis_date": sk,
                "screen_type": "quant_screen",
                "result": result,
                "passed": passed,
            }
        )

    if analysis_items:
        analysis_client.batch_write(analysis_items)

    return {
        "passing_tickers": passing,
        "total_screened": len(companies),
        "total_passed": len(passing),
    }
