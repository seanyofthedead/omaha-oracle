"""
Lambda handler for Graham-Dodd quantitative screening.

Pure math — no LLM calls. Screens all companies in the companies table
against configurable thresholds, stores results in the analysis table.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from boto3.dynamodb.conditions import Key

from shared.config import get_config
from shared.dynamo_client import DynamoClient
from shared.logger import get_logger

_log = get_logger(__name__)

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
WACC_APPROX = 0.10
GRAHAM_MULTIPLIER = 22.5
YEARS = 10


def _safe_float(val: Any) -> float:
    if val is None:
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _to_float(val: Any) -> float:
    """Convert Decimal or other numeric types to float for JSON serialization."""
    return _safe_float(val)


def _company_metrics(company: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Build metrics dict from company (camelCase) and quant result for downstream stages."""
    return {
        "current_price": _to_float(company.get("currentPrice") or company.get("regularMarketPrice")),
        "market_cap": _to_float(company.get("marketCap")),
        "pe": _to_float(result.get("pe") or company.get("trailingPE")),
        "pb": _to_float(result.get("pb") or company.get("priceToBook")),
        "eps": _to_float(company.get("trailingEps")),
        "book_value": _to_float(company.get("bookValue")),
        "industry": company.get("industry") or "Unknown",
        "sector": company.get("sector") or "Unknown",
        "owner_earnings": _to_float(result.get("owner_earnings")),
        "net_net_working_capital": _to_float(result.get("net_net_working_capital")),
        "shares_outstanding": _to_float(result.get("shares_outstanding")),
        "roic_current": _to_float(result.get("roic_current")),
        "roic_10y_avg": _to_float(result.get("roic_10y_avg")),
        "debt_equity": _to_float(result.get("debt_equity")),
        "fcf_yield": _to_float(result.get("fcf_yield")),
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
        val = _safe_float(it.get("value"))
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


def _piotroski_score(by_year: dict[int, dict[str, float]], years_sorted: list[int]) -> int:
    """
    Piotroski F-Score (0–9). Uses available data; scores 0 for missing components.
    """
    if len(years_sorted) < 2:
        return 0
    score = 0

    def get(y: int, m: str) -> float:
        return by_year.get(y, {}).get(m, 0.0)

    # Current year = most recent
    curr_y = years_sorted[-1]
    prev_y = years_sorted[-2]

    # 1. ROA > 0 (Net Income / Total Assets)
    ni = get(curr_y, "net_income")
    ta = get(curr_y, "total_assets")
    roa_curr = ni / ta if ta else 0
    if roa_curr > 0:
        score += 1

    # 2. Operating Cash Flow > 0
    ocf = get(curr_y, "operating_cash_flow")
    if ocf > 0:
        score += 1

    # 3. Change in ROA > 0
    ta_prev = get(prev_y, "total_assets")
    roa_prev = get(prev_y, "net_income") / ta_prev if ta_prev else 0
    if roa_curr > roa_prev:
        score += 1

    # 4. Cash flow from ops > Net Income
    if ocf > ni:
        score += 1

    # 5. Change in leverage < 0 (decrease in LTD/Assets)
    ltd_curr = get(curr_y, "long_term_debt")
    ltd_prev = get(prev_y, "long_term_debt")
    lev_curr = ltd_curr / ta if ta else 0
    lev_prev = ltd_prev / ta_prev if ta_prev else 0
    if lev_curr < lev_prev:
        score += 1

    # 6. Change in current ratio > 0
    ca_curr = get(curr_y, "current_assets")
    cl_curr = get(curr_y, "current_liabilities")
    ca_prev = get(prev_y, "current_assets")
    cl_prev = get(prev_y, "current_liabilities")
    cr_curr = ca_curr / cl_curr if cl_curr else 0
    cr_prev = ca_prev / cl_prev if cl_prev else 0
    if cr_curr > cr_prev:
        score += 1

    # 7. Change in shares outstanding <= 0 (no dilution)
    sh_curr = get(curr_y, "shares_outstanding")
    sh_prev = get(prev_y, "shares_outstanding")
    if sh_curr <= sh_prev and sh_prev > 0:
        score += 1

    # 8. Change in gross margin > 0 (we don't have gross margin; use operating margin proxy)
    rev_curr = get(curr_y, "revenue")
    rev_prev = get(prev_y, "revenue")
    dep_curr = get(curr_y, "depreciation") + get(curr_y, "capex")
    dep_prev = get(prev_y, "depreciation") + get(prev_y, "capex")
    om_curr = (rev_curr - dep_curr) / rev_curr if rev_curr else 0
    om_prev = (rev_prev - dep_prev) / rev_prev if rev_prev else 0
    if om_curr > om_prev:
        score += 1

    # 9. Change in asset turnover > 0
    at_curr = rev_curr / ta if ta else 0
    at_prev = rev_prev / ta_prev if ta_prev else 0
    if at_curr > at_prev:
        score += 1

    return score


def _screen_company(
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
        print(f"[quant_screen] {ticker}: no financials -> FAIL")
        return {"ticker": ticker, "pass": False, "reason": "no_financials"}, False

    curr_y = years_sorted[0]
    fy = by_year.get(curr_y, {})

    # Companies table uses camelCase from yfinance
    pe = _safe_float(company.get("trailingPE"))
    pb = _safe_float(company.get("priceToBook"))
    eps = _safe_float(company.get("trailingEps"))
    bvps = _safe_float(company.get("bookValue"))
    mcap = _safe_float(company.get("marketCap"))

    ni = fy.get("net_income", 0.0)
    dep = fy.get("depreciation", 0.0)
    capex = fy.get("capex", 0.0)
    ocf = fy.get("operating_cash_flow", 0.0)
    equity = fy.get("stockholders_equity", 0.0)
    ltd = fy.get("long_term_debt", 0.0)
    ca = fy.get("current_assets", 0.0)
    tl = fy.get("total_liabilities", 0.0)

    owner_earnings = ni + dep - 0.7 * capex
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
        by_year[y].get("operating_cash_flow", 0) - by_year[y].get("capex", 0)
        for y in years_sorted
    ]
    positive_fcf_years = sum(1 for v in fcf_vals if v > 0)

    piotroski = _piotroski_score(by_year, years_sorted)

    shares = fy.get("shares_outstanding", 0) or 0
    shares = _safe_float(shares)

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

    passed = pe_ok and pb_ok and de_ok and roic_ok and fcf_ok and piot_ok

    # Debug: print to Lambda logs (threshold vs actual, pass/fail)
    print(f"[quant_screen] {ticker}:")
    print(f"  P/E: actual={pe:.2f} vs max={pe_max} -> {'PASS' if pe_ok else 'FAIL'}")
    print(f"  P/B: actual={pb:.2f} vs max={pb_max} -> {'PASS' if pb_ok else 'FAIL'}")
    print(f"  debt/equity: actual={debt_equity:.3f} vs max={de_max} -> {'PASS' if de_ok else 'FAIL'}")
    print(f"  ROIC 10y avg: actual={roic_10y_avg*100:.1f}% vs min={roic_min*100}% -> {'PASS' if roic_ok else 'FAIL'}")
    print(f"  positive FCF years: actual={positive_fcf_years} vs min={fcf_min} -> {'PASS' if fcf_ok else 'FAIL'}")
    print(f"  Piotroski: actual={piotroski} vs min={piot_min} -> {'PASS' if piot_ok else 'FAIL'}")
    print(f"  OVERALL: {'PASS' if passed else 'FAIL'}")

    result["pass"] = passed
    return result, passed


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
    print(f"[quant_screen] Loaded thresholds: {thresholds}")
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    sk = f"{date_str}#quant_screen"

    companies = companies_client.scan_all()

    passing: list[dict[str, Any]] = []

    for comp in companies:
        ticker = comp.get("ticker")
        if not ticker:
            continue

        result, passed = _screen_company(
            ticker, comp, financials_client, thresholds
        )
        if not passed and "reason" in result and result["reason"] == "no_financials":
            _log.debug("No financials for ticker", extra={"ticker": ticker})
            continue
        if passed:
            company_name = (
                comp.get("longName") or comp.get("shortName") or str(ticker)
            )
            metrics = _company_metrics(comp, result)
            passing.append({
                "ticker": ticker,
                "company_name": company_name,
                "metrics": metrics,
                "quant_result": result,
            })

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

        analysis_item = {
            "ticker": ticker,
            "analysis_date": sk,
            "screen_type": "quant_screen",
            "result": result,
            "passed": passed,
        }
        analysis_client.put_item(analysis_item)

    return {
        "passing_tickers": passing,
        "total_screened": len(companies),
        "total_passed": len(passing),
    }
