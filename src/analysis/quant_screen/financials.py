"""
Financial data aggregation helpers for the quant screen.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from shared.converters import safe_float
from shared.dynamo_client import DynamoClient

# Config table keys: max_pe, max_pb, max_debt_equity, min_roic_avg,
# min_positive_fcf_years, min_piotroski, min_margin_of_safety
DEFAULT_THRESHOLDS: dict[str, float | int] = {
    "max_pe": 15.0,
    "max_pb": 1.5,
    "max_debt_equity": 0.5,
    "min_roic_avg": 0.12,
    "min_positive_fcf_years": 8,
    "min_piotroski": 6,
    "min_margin_of_safety": 0.30,
}

# Alias map: old config keys -> canonical keys (for backwards compatibility)
_CONFIG_KEY_ALIASES: dict[str, str] = {
    "pe_max": "max_pe",
    "pb_max": "max_pb",
    "debt_equity_max": "max_debt_equity",
    "roic_10y_min_pct": "min_roic_avg",
    "positive_fcf_min_years": "min_positive_fcf_years",
    "piotroski_min": "min_piotroski",
}


def _aggregate_financials_by_year(items: list[dict[str, Any]]) -> dict[int, dict[str, float]]:
    """
    Group financials by year. Items have ticker, period, metric_name, value,
    fiscal_year, period_end_date. Uses fiscal_year if present, else extracts
    year from period_end_date or period (format {date}#{metric_name}).
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
        metric = it.get("metric_name", "")
        val = safe_float(it.get("value"))
        if metric and year:
            by_year[year][metric] = val
    return dict(by_year)


def _cv(values: list[float]) -> float:
    """Coefficient of variation (std/mean). Returns 0 if mean is 0 or empty."""
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    if mean == 0:
        return 0.0
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    std = math.sqrt(variance)
    return std / abs(mean)


def _load_thresholds(config_client: DynamoClient) -> dict[str, float | int]:
    """Load screening thresholds from config table.
    Keys: max_pe, max_pb, max_debt_equity, min_roic_avg,
    min_positive_fcf_years, min_piotroski, min_margin_of_safety.
    Config values override defaults. Converts Decimal to float.
    """
    out = dict(DEFAULT_THRESHOLDS)
    item = config_client.get_item({"config_key": "screening_thresholds"})
    if not item:
        return out

    # Config may be under "value" (nested) or at top level (flat)
    config_raw = item.get("value")
    if config_raw is None:
        config_raw = {k: v for k, v in item.items() if k != "config_key"}
    if not isinstance(config_raw, dict):
        return out

    # Normalize keys (handle aliases) and collect config values
    for raw_k, v in config_raw.items():
        if v is None:
            continue
        canon_k = _CONFIG_KEY_ALIASES.get(raw_k, raw_k)
        if canon_k not in DEFAULT_THRESHOLDS:
            continue
        try:
            fv = float(v)
            if raw_k == "roic_10y_min_pct":
                fv = fv / 100.0
            if isinstance(DEFAULT_THRESHOLDS[canon_k], float):
                out[canon_k] = fv
            else:
                out[canon_k] = int(fv)
        except (TypeError, ValueError):
            pass
    return out


def _company_metrics(company: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Build metrics dict from company (camelCase) and quant result for downstream stages."""
    return {
        "current_price": safe_float(
            company.get("currentPrice") or company.get("regularMarketPrice")
        ),
        "market_cap": safe_float(company.get("marketCap")),
        "pe": safe_float(result.get("pe") or company.get("trailingPE")),
        "pb": safe_float(result.get("pb") or company.get("priceToBook")),
        "eps": safe_float(company.get("trailingEps")),
        "book_value": safe_float(company.get("bookValue")),
        "industry": company.get("industry") or "Unknown",
        "sector": company.get("sector") or "Unknown",
        "owner_earnings": safe_float(result.get("owner_earnings")),
        "net_net_working_capital": safe_float(result.get("net_net_working_capital")),
        "shares_outstanding": safe_float(result.get("shares_outstanding")),
        "roic_current": safe_float(result.get("roic_current")),
        "roic_10y_avg": safe_float(result.get("roic_10y_avg")),
        "debt_equity": safe_float(result.get("debt_equity")),
        "fcf_yield": safe_float(result.get("fcf_yield")),
    }
