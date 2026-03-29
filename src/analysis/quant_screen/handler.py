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

        # Debug: which criteria failed (for both pass and fail)
        pe = result.get("pe", 0)
        pb = result.get("pb", 0)
        de = result.get("debt_equity", 0)
        roic = result.get("roic_10y_avg", 0)
        pfcf = result.get("positive_fcf_years", 0)
        pio = result.get("piotroski_score", 0)
        pe_max = float(thresholds.get("max_pe", 15))
        pb_max = float(thresholds.get("max_pb", 1.5))
        de_max = float(thresholds.get("max_debt_equity", 0.5))
        roic_min = float(thresholds.get("min_roic_avg", 0.12))
        fcf_min = int(float(thresholds.get("min_positive_fcf_years", 8)))
        piot_min = int(float(thresholds.get("min_piotroski", 6)))

        failed: list[str] = []
        if pe > 0 and pe >= pe_max:
            failed.append("pe")
        if pb > 0 and pb >= pb_max:
            failed.append("pb")
        if de >= de_max:
            failed.append("debt_equity")
        if roic < roic_min:
            failed.append("roic")
        if pfcf < fcf_min:
            failed.append("positive_fcf_years")
        if pio < piot_min:
            failed.append("piotroski")

        _log.debug(
            "Quant screen result",
            extra={
                "ticker": ticker,
                "pe": round(pe, 2),
                "pb": round(pb, 2),
                "debt_equity": round(de, 3),
                "roic_10y_avg_pct": round(roic * 100, 1),
                "piotroski": pio,
                "positive_fcf_years": pfcf,
                "failed_criteria": failed,
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
