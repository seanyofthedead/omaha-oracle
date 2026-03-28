"""
Metric lookup for prediction evaluation.

Maps prediction metric names to actual values from the companies and financials
DynamoDB tables (already populated by Yahoo Finance and SEC EDGAR ingestion).

SEC EDGAR stores one DynamoDB item per metric per fiscal period (e.g., a
separate row for revenue, net_income, etc.). This module aggregates those
rows by year before computing derived metrics like margins and ratios —
matching the pattern in ``quant_screen/financials.py``.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from boto3.dynamodb.conditions import Key

from shared.converters import safe_float
from shared.dynamo_client import DynamoClient
from shared.logger import get_logger

_log = get_logger(__name__)

# Canonical metric names and their analysis stage mapping
ALLOWED_METRICS = frozenset(
    {
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
    }
)

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


def _aggregate_financials_by_year(
    items: list[dict[str, Any]],
) -> dict[int, dict[str, float]]:
    """Group one-item-per-metric financials records into {year: {metric: value}}.

    Mirrors the pattern in ``quant_screen/financials.py``.
    """
    by_year: dict[int, dict[str, float]] = defaultdict(dict)
    for it in items:
        year: int | None = None
        fy = it.get("fiscal_year")
        if fy is not None:
            try:
                year = int(float(fy))
            except (ValueError, TypeError):
                pass
        if year is None:
            period = it.get("period_end_date") or it.get("period", "")
            if "#" in str(period):
                date_part = str(period).split("#")[0]
            else:
                date_part = str(period)
            try:
                year = int(date_part[:4]) if date_part else 0
            except (ValueError, IndexError):
                continue
        metric_name = it.get("metric_name", "")
        val = safe_float(it.get("value"))
        if metric_name and year:
            by_year[year][metric_name] = val
    return dict(by_year)


def _fetch_from_companies(
    companies_client: DynamoClient,
    ticker: str,
    metric: str,
) -> float | None:
    """Fetch a metric from the companies table (Yahoo Finance data).

    The companies table stores current snapshots only — no historical data.
    """
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


def _fetch_historical_price(ticker: str, as_of_date: str) -> float | None:
    """Fetch the stock price at a specific date using yfinance.

    Follows the same pattern as ``owners_letter/audit.py:_fetch_price``.
    """
    try:
        import yfinance as yf

        dt = datetime.strptime(as_of_date, "%Y-%m-%d").replace(tzinfo=UTC)
        t = yf.Ticker(ticker)
        end = dt + timedelta(days=1)
        df = t.history(start=dt.date(), end=end.date())
        if df.empty:
            # Try a wider range (±3 days) for weekends/holidays
            start_wide = dt - timedelta(days=3)
            df = t.history(start=start_wide.date(), end=end.date())
            if df.empty:
                return None
        return float(df["Close"].iloc[-1])
    except Exception as exc:
        _log.warning(
            "Historical price fetch failed",
            extra={"ticker": ticker, "as_of_date": as_of_date, "error": str(exc)},
        )
        return None


def _fetch_from_financials(
    financials_client: DynamoClient,
    ticker: str,
    metric: str,
    as_of_date: str | None = None,
) -> float | None:
    """Fetch a derived metric from the financials table (SEC EDGAR data).

    SEC ingestion writes one DynamoDB item per metric per fiscal period
    (e.g., ``period=2023-12-31#revenue``). This function queries ALL items
    for the ticker, aggregates them by year, then extracts or computes the
    requested metric from the most recent year on or before ``as_of_date``.
    """
    items = financials_client.query(Key("ticker").eq(ticker))
    if not items:
        return None

    by_year = _aggregate_financials_by_year(items)
    if not by_year:
        return None

    # Pick the most recent year on or before as_of_date
    if as_of_date:
        try:
            cutoff_year = int(as_of_date[:4])
        except (ValueError, IndexError):
            cutoff_year = max(by_year.keys())
        eligible_years = [y for y in by_year if y <= cutoff_year]
    else:
        eligible_years = list(by_year.keys())

    if not eligible_years:
        return None

    latest_year = max(eligible_years)
    metrics = by_year[latest_year]

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
            cogs = metrics.get("cost_of_revenue")
            if revenue and cogs:
                return (float(revenue) - float(cogs)) / float(revenue)
            # Try net_income / revenue as rough proxy if no COGS
            return None
        if metric == "operating_margin":
            revenue = metrics.get("revenue")
            # Try operating_income or operating_cash_flow as proxy
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
    as_of_date: str | None = None,
) -> float | None:
    """Fetch the actual value for a prediction metric.

    Parameters
    ----------
    as_of_date:
        ISO date (YYYY-MM-DD) at which the metric should be evaluated.
        For stock_price, fetches the historical price at that date.
        For SEC metrics, uses the most recent fiscal year on or before that date.
        If None, uses the latest available data.

    Returns None if the metric cannot be resolved.
    """
    if metric not in ALLOWED_METRICS:
        _log.warning("Unknown metric", extra={"metric": metric, "ticker": ticker})
        return None

    # For stock_price with a historical date, use yfinance directly
    if metric == "stock_price" and as_of_date:
        val = _fetch_historical_price(ticker, as_of_date)
        if val is not None:
            return val
        # Fall through to companies table (current snapshot) as last resort

    if data_source == "yahoo_finance":
        val = _fetch_from_companies(companies_client, ticker, metric)
        if val is not None:
            return val
        return _fetch_from_financials(financials_client, ticker, metric, as_of_date)

    if data_source == "sec_edgar":
        val = _fetch_from_financials(financials_client, ticker, metric, as_of_date)
        if val is not None:
            return val
        return _fetch_from_companies(companies_client, ticker, metric)

    # Unknown data source — try both
    val = _fetch_from_companies(companies_client, ticker, metric)
    if val is not None:
        return val
    return _fetch_from_financials(financials_client, ticker, metric, as_of_date)
