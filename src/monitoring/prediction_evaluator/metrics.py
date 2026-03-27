"""
Metric lookup for prediction evaluation.

Maps prediction metric names to actual values from the companies and financials
DynamoDB tables (already populated by Yahoo Finance and SEC EDGAR ingestion).
"""

from __future__ import annotations

from typing import Any

from shared.dynamo_client import DynamoClient
from shared.logger import get_logger

_log = get_logger(__name__)

# Canonical metric names and their analysis stage mapping
ALLOWED_METRICS = frozenset({
    "revenue",
    "earnings_per_share",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "book_value_per_share",
    "debt_to_equity",
    "free_cash_flow",
    "return_on_equity",
    "stock_price",
})

METRIC_TO_STAGE: dict[str, str] = {
    "revenue": "intrinsic_value",
    "earnings_per_share": "intrinsic_value",
    "free_cash_flow": "intrinsic_value",
    "stock_price": "intrinsic_value",
    "gross_margin": "moat_analysis",
    "operating_margin": "moat_analysis",
    "net_margin": "moat_analysis",
    "return_on_equity": "moat_analysis",
    "book_value_per_share": "quant_screen",
    "debt_to_equity": "quant_screen",
}

# Yahoo Finance companies table field mappings
_YF_METRIC_MAP: dict[str, str] = {
    "stock_price": "currentPrice",
    "earnings_per_share": "trailingEps",
    "book_value_per_share": "bookValue",
}


def _fetch_from_companies(
    companies_client: DynamoClient,
    ticker: str,
    metric: str,
) -> float | None:
    """Fetch a metric from the companies table (Yahoo Finance data)."""
    item = companies_client.get_item({"ticker": ticker})
    if not item:
        return None

    yf_key = _YF_METRIC_MAP.get(metric)
    if yf_key:
        val = item.get(yf_key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                return None
    return None


def _fetch_from_financials(
    financials_client: DynamoClient,
    ticker: str,
    metric: str,
) -> float | None:
    """Fetch a derived metric from the financials table (SEC EDGAR data).

    Financials store raw values (revenue, net_income, total_assets, etc.).
    Margin metrics are computed from these.
    """
    from boto3.dynamodb.conditions import Key

    items = financials_client.query(
        Key("ticker").eq(ticker),
        scan_forward=False,
        limit=1,
    )
    if not items:
        return None
    latest = items[0]
    metrics = latest.get("metrics") or latest
    if not isinstance(metrics, dict):
        return None

    try:
        if metric == "revenue":
            val = metrics.get("revenue")
            return float(val) if val is not None else None
        if metric == "free_cash_flow":
            op_cf = metrics.get("operating_cash_flow")
            capex = metrics.get("capex")
            if op_cf is not None and capex is not None:
                return float(op_cf) - float(capex)
            return None
        if metric == "gross_margin":
            revenue = metrics.get("revenue")
            # Gross margin not directly stored; try cost_of_revenue
            cogs = metrics.get("cost_of_revenue")
            if revenue and cogs:
                return (float(revenue) - float(cogs)) / float(revenue)
            return None
        if metric == "operating_margin":
            revenue = metrics.get("revenue")
            op_income = metrics.get("operating_income")
            if revenue and op_income:
                return float(op_income) / float(revenue)
            return None
        if metric == "net_margin":
            revenue = metrics.get("revenue")
            net_income = metrics.get("net_income")
            if revenue and net_income:
                return float(net_income) / float(revenue)
            return None
        if metric == "return_on_equity":
            net_income = metrics.get("net_income")
            equity = metrics.get("stockholders_equity")
            if net_income and equity and float(equity) != 0:
                return float(net_income) / float(equity)
            return None
        if metric == "debt_to_equity":
            total_liabilities = metrics.get("total_liabilities")
            equity = metrics.get("stockholders_equity")
            if total_liabilities and equity and float(equity) != 0:
                return float(total_liabilities) / float(equity)
            return None
        if metric == "book_value_per_share":
            equity = metrics.get("stockholders_equity")
            shares = metrics.get("shares_outstanding")
            if equity and shares and float(shares) != 0:
                return float(equity) / float(shares)
            return None
        if metric == "earnings_per_share":
            net_income = metrics.get("net_income")
            shares = metrics.get("shares_outstanding")
            if net_income and shares and float(shares) != 0:
                return float(net_income) / float(shares)
            return None
    except (TypeError, ValueError, ZeroDivisionError):
        return None

    return None


def fetch_actual(
    metric: str,
    ticker: str,
    data_source: str,
    companies_client: DynamoClient,
    financials_client: DynamoClient,
) -> float | None:
    """Fetch the actual value for a prediction metric.

    Returns None if the metric cannot be resolved.
    """
    if metric not in ALLOWED_METRICS:
        _log.warning("Unknown metric", extra={"metric": metric, "ticker": ticker})
        return None

    if data_source == "yahoo_finance":
        val = _fetch_from_companies(companies_client, ticker, metric)
        if val is not None:
            return val
        # Fall through to financials as backup
        return _fetch_from_financials(financials_client, ticker, metric)

    if data_source == "sec_edgar":
        val = _fetch_from_financials(financials_client, ticker, metric)
        if val is not None:
            return val
        # Fall through to companies as backup
        return _fetch_from_companies(companies_client, ticker, metric)

    # Unknown data source — try both
    val = _fetch_from_companies(companies_client, ticker, metric)
    if val is not None:
        return val
    return _fetch_from_financials(financials_client, ticker, metric)
