"""
Lambda handler for intrinsic value estimation.

Pure math — no LLM. Three-scenario DCF + EPV + asset floor.
"""

from __future__ import annotations

from typing import Any

from shared.config import get_config
from shared.converters import normalize_ticker, safe_float
from shared.dynamo_client import DynamoClient, store_analysis_result
from shared.logger import get_logger

_log = get_logger(__name__)

DISCOUNT_RATE = 0.10
TERMINAL_GROWTH = 0.03
WACC = 0.10
DCF_WEIGHT = 0.60
EPV_WEIGHT = 0.30
FLOOR_WEIGHT = 0.10
DEFAULT_MOS_THRESHOLD = 0.30

# Bear / Base / Bull: (growth_rate, probability)
SCENARIOS = [
    (0.02, 0.25),  # Bear
    (0.06, 0.50),  # Base
    (0.10, 0.25),  # Bull
]
YEARS = 10


def _extract_inputs(metrics: dict[str, Any]) -> dict[str, float]:
    """Extract required values from metrics with flexible key names."""

    def get(*keys: str) -> float:
        for k in keys:
            v = metrics.get(k)
            if v is not None:
                return safe_float(v)
        return 0.0

    owner_earnings = get("owner_earnings")
    if owner_earnings == 0:
        ni = get("net_income", "netIncome")
        dep = get("depreciation")
        capex = get("capex")
        owner_earnings = ni + dep - 0.7 * capex

    ca = get("current_assets", "currentAssets")
    tl = get("total_liabilities", "totalLiabilities")
    nnwc = get("net_net_working_capital", "netNetWorkingCapital")
    if nnwc == 0:
        nnwc = ca - tl

    shares = get("shares_outstanding", "sharesOutstanding")
    if shares <= 0:
        mcap = get("market_cap", "marketCap")
        price = get("current_price", "currentPrice")
        if mcap > 0 and price > 0:
            shares = mcap / price

    price = get("current_price", "currentPrice")

    return {
        "owner_earnings": owner_earnings,
        "nnwc": nnwc,
        "shares": shares,
        "price": price,
    }


def _dcf_pv(fcf0: float, growth: float) -> float:
    """Present value of 10-year DCF + terminal value."""
    if fcf0 <= 0:
        return 0.0
    r = DISCOUNT_RATE
    g_term = TERMINAL_GROWTH
    pv_explicit = 0.0
    for t in range(1, YEARS + 1):
        fcf_t = fcf0 * ((1 + growth) ** t)
        pv_explicit += fcf_t / ((1 + r) ** t)
    fcf_10 = fcf0 * ((1 + growth) ** YEARS)
    terminal_value = fcf_10 * (1 + g_term) / (r - g_term)
    pv_terminal = terminal_value / ((1 + r) ** YEARS)
    return pv_explicit + pv_terminal


def _load_mos_threshold(config_client: DynamoClient) -> float:
    """Load margin-of-safety buy threshold from config."""
    item = config_client.get_item({"config_key": "intrinsic_value"})
    if not item or "value" not in item:
        return DEFAULT_MOS_THRESHOLD
    val = item.get("value")
    if isinstance(val, dict) and "margin_of_safety_threshold" in val:
        return safe_float(val["margin_of_safety_threshold"]) or DEFAULT_MOS_THRESHOLD
    return DEFAULT_MOS_THRESHOLD


def _resolve_metrics(event: dict[str, Any], cfg: Any) -> dict[str, Any]:
    """
    Build metrics dict from event, with fallback to companies table when
    current_price or market_cap is missing/zero.
    """
    metrics = dict(event.get("metrics") or {})
    # Merge top-level overrides (e.g. current_price passed directly)
    if event.get("current_price") is not None:
        metrics["current_price"] = event["current_price"]
    if event.get("market_cap") is not None:
        metrics["market_cap"] = event["market_cap"]

    ticker = normalize_ticker(event)
    price = safe_float(metrics.get("current_price") or metrics.get("currentPrice"))
    mcap = safe_float(metrics.get("market_cap") or metrics.get("marketCap"))

    if (price <= 0 or mcap <= 0) and ticker:
        companies = DynamoClient(cfg.table_companies)
        company = companies.get_item({"ticker": ticker})
        if company:
            if price <= 0:
                price = safe_float(company.get("currentPrice") or company.get("regularMarketPrice"))
                metrics["current_price"] = price
            if mcap <= 0:
                mcap = safe_float(company.get("marketCap"))
                metrics["market_cap"] = mcap

    return metrics


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    Input: {"ticker", "metrics", "company_name", "moat_score", "management_score"}
    Output: input dict + intrinsic_value_per_share, margin_of_safety, buy_signal, scenarios, etc.
    """
    cfg = get_config()
    ticker = normalize_ticker(event)
    metrics = _resolve_metrics(event, cfg)

    if not ticker:
        return {"error": "ticker required", "intrinsic_value_per_share": 0, "buy_signal": False}

    inputs = _extract_inputs(metrics)
    oe = inputs["owner_earnings"]
    nnwc = inputs["nnwc"]
    shares = inputs["shares"]
    price = inputs["price"]

    if shares <= 0:
        _log.warning("No shares_outstanding — cannot compute per-share", extra={"ticker": ticker})
        result = _build_result(event, 0.0, 0.0, False, {}, price)
        result["metrics"] = metrics
        result["error"] = "no_shares_outstanding"
        store_analysis_result(
            cfg.table_analysis,
            ticker,
            "intrinsic_value",
            result,
            result.get("buy_signal", False),
        )
        return result

    # DCF scenarios
    dcf_bear = _dcf_pv(oe, 0.02)
    dcf_base = _dcf_pv(oe, 0.06)
    dcf_bull = _dcf_pv(oe, 0.10)
    dcf_weighted = 0.25 * dcf_bear + 0.50 * dcf_base + 0.25 * dcf_bull
    dcf_per_share = dcf_weighted / shares

    # EPV
    epv = oe / WACC if WACC else 0
    epv_per_share = epv / shares if shares else 0

    # Asset floor
    floor_per_share = nnwc / shares if shares else 0
    floor_per_share = max(0.0, floor_per_share)

    # Composite
    composite = (
        DCF_WEIGHT * dcf_per_share + EPV_WEIGHT * epv_per_share + FLOOR_WEIGHT * floor_per_share
    )
    composite = max(0.0, composite)

    # Margin of safety
    if composite > 0 and price > 0:
        margin_of_safety = (composite - price) / composite
    else:
        margin_of_safety = 0.0

    config_client = DynamoClient(cfg.table_config)
    mos_threshold = _load_mos_threshold(config_client)
    buy_signal = margin_of_safety > mos_threshold

    scenarios = {
        "bear": {"growth_pct": 2, "probability": 0.25, "dcf_per_share": dcf_bear / shares},
        "base": {"growth_pct": 6, "probability": 0.50, "dcf_per_share": dcf_base / shares},
        "bull": {"growth_pct": 10, "probability": 0.25, "dcf_per_share": dcf_bull / shares},
        "weighted_dcf_per_share": dcf_per_share,
    }

    result = _build_result(
        event,
        composite,
        margin_of_safety,
        buy_signal,
        scenarios,
        price,
        dcf_per_share=dcf_per_share,
        epv_per_share=epv_per_share,
        floor_per_share=floor_per_share,
    )
    result["mos_threshold"] = mos_threshold
    result["metrics"] = metrics  # Pass through resolved metrics for downstream

    store_analysis_result(
        cfg.table_analysis,
        ticker,
        "intrinsic_value",
        result,
        result.get("buy_signal", False),
    )
    return result


def _build_result(
    event: dict[str, Any],
    intrinsic_value: float,
    margin_of_safety: float,
    buy_signal: bool,
    scenarios: dict[str, Any],
    price: float,
    **extra: Any,
) -> dict[str, Any]:
    """Build output dict from input + computed values."""
    out = dict(event)
    out["intrinsic_value_per_share"] = intrinsic_value
    out["margin_of_safety"] = margin_of_safety
    out["buy_signal"] = buy_signal
    out["scenarios"] = scenarios
    out["current_price"] = price
    out.update(extra)
    return out
