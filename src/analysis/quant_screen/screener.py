"""
Core screening logic for a single company.
"""

from __future__ import annotations

import math
from typing import Any

from boto3.dynamodb.conditions import Key

from shared.converters import compute_owner_earnings, safe_float
from shared.dynamo_client import DynamoClient
from shared.logger import get_logger

from .financials import _aggregate_financials_by_year, _cv
from .piotroski import piotroski_score

_log = get_logger(__name__)

WACC_APPROX = 0.10
GRAHAM_MULTIPLIER = 22.5
YEARS = 10


def screen_company(
    ticker: str,
    company: dict[str, Any],
    financials_client: DynamoClient,
    thresholds: dict[str, float | int],
) -> tuple[dict[str, Any], bool]:
    """
    Compute all metrics and determine pass/fail.
    Queries financials table for ticker, aggregates by year, then screens.
    Returns (result_dict, passed).
    """
    fin_items = financials_client.query(Key("ticker").eq(ticker))
    by_year = _aggregate_financials_by_year(fin_items)
    years_sorted = sorted(by_year.keys(), reverse=True)[:YEARS]
    if not years_sorted:
        _log.debug("no financials", extra={"ticker": ticker})
        return {"ticker": ticker, "pass": False, "reason": "no_financials"}, False

    curr_y = years_sorted[0]
    fy = by_year.get(curr_y, {})

    # Companies table uses camelCase from yfinance
    pe = safe_float(company.get("trailingPE"))
    pb = safe_float(company.get("priceToBook"))
    eps = safe_float(company.get("trailingEps"))
    bvps = safe_float(company.get("bookValue"))
    mcap = safe_float(company.get("marketCap"))

    ni = fy.get("net_income", 0.0)
    dep = fy.get("depreciation", 0.0)
    capex = fy.get("capex", 0.0)
    ocf = fy.get("operating_cash_flow", 0.0)
    equity = fy.get("stockholders_equity", 0.0)
    ltd = fy.get("long_term_debt", 0.0)
    ca = fy.get("current_assets", 0.0)
    tl = fy.get("total_liabilities", 0.0)

    owner_earnings = compute_owner_earnings(ni, dep, capex)
    epv = owner_earnings / WACC_APPROX if WACC_APPROX else 0
    nnwc = ca - tl
    graham_num = math.sqrt(GRAHAM_MULTIPLIER * eps * bvps) if eps > 0 and bvps > 0 else 0.0

    roic_denom = equity + ltd
    roic_curr = ni / roic_denom if roic_denom else 0
    roic_10y_vals = []
    for y in years_sorted:
        f = by_year.get(y, {})
        e = f.get("stockholders_equity", 0) + f.get("long_term_debt", 0)
        n = f.get("net_income", 0)
        if e:
            roic_10y_vals.append(n / e)
    roic_10y_avg = sum(roic_10y_vals) / len(roic_10y_vals) if roic_10y_vals else 0

    debt_equity = ltd / equity if equity else 0

    fcf = ocf - capex
    fcf_yield = fcf / mcap if mcap else 0

    rev_vals = [by_year[y].get("revenue", 0) for y in years_sorted]
    ni_vals = [by_year[y].get("net_income", 0) for y in years_sorted]
    rev_cv = _cv(rev_vals)
    earnings_cv = _cv(ni_vals)

    fcf_vals = [
        by_year[y].get("operating_cash_flow", 0) - by_year[y].get("capex", 0) for y in years_sorted
    ]
    positive_fcf_years = sum(1 for v in fcf_vals if v > 0)

    piotroski = piotroski_score(by_year, years_sorted)

    shares = fy.get("shares_outstanding", 0) or 0
    shares = safe_float(shares)

    result: dict[str, Any] = {
        "ticker": ticker,
        "owner_earnings": owner_earnings,
        "epv": epv,
        "net_net_working_capital": nnwc,
        "graham_number": graham_num,
        "roic_current": roic_curr,
        "roic_10y_avg": roic_10y_avg,
        "debt_equity": debt_equity,
        "fcf_yield": fcf_yield,
        "revenue_cv": rev_cv,
        "earnings_cv": earnings_cv,
        "positive_fcf_years": positive_fcf_years,
        "piotroski_score": piotroski,
        "pe": pe,
        "pb": pb,
        "shares_outstanding": shares,
    }

    # Convert thresholds to float/int (DynamoDB may return Decimal)
    pe_max = float(thresholds.get("max_pe", 15))
    pb_max = float(thresholds.get("max_pb", 1.5))
    de_max = float(thresholds.get("max_debt_equity", 0.5))
    roic_min = float(thresholds.get("min_roic_avg", 0.12))
    fcf_min = int(float(thresholds.get("min_positive_fcf_years", 8)))
    piot_min = int(float(thresholds.get("min_piotroski", 6)))

    # Evaluate each criterion (ensure float for comparison)
    pe_ok = not (pe > 0 and pe >= pe_max)
    pb_ok = not (pb > 0 and pb >= pb_max)
    de_ok = float(debt_equity) < de_max
    roic_ok = float(roic_10y_avg) >= roic_min
    fcf_ok = positive_fcf_years >= fcf_min
    piot_ok = piotroski >= piot_min

    # Build failed_criteria list so callers don't need to re-derive thresholds
    failed_criteria: list[str] = []
    if not pe_ok:
        failed_criteria.append("pe")
    if not pb_ok:
        failed_criteria.append("pb")
    if not de_ok:
        failed_criteria.append("debt_equity")
    if not roic_ok:
        failed_criteria.append("roic")
    if not fcf_ok:
        failed_criteria.append("positive_fcf_years")
    if not piot_ok:
        failed_criteria.append("piotroski")

    passed = len(failed_criteria) == 0

    _log.debug(
        "quant_screen result",
        extra={
            "ticker": ticker,
            "pe": round(pe, 2),
            "pe_ok": pe_ok,
            "pb": round(pb, 2),
            "pb_ok": pb_ok,
            "debt_equity": round(float(debt_equity), 3),
            "de_ok": de_ok,
            "roic_10y_avg_pct": round(float(roic_10y_avg) * 100, 1),
            "roic_ok": roic_ok,
            "positive_fcf_years": positive_fcf_years,
            "fcf_ok": fcf_ok,
            "piotroski": piotroski,
            "piot_ok": piot_ok,
            "passed": passed,
            "failed_criteria": failed_criteria,
        },
    )

    result["pass"] = passed
    result["failed_criteria"] = failed_criteria
    return result, passed
