#!/usr/bin/env python3
"""Quality Gate Search — find the first company passing all Omaha Oracle gates.

Gates:  moat_score >= 7,  management_score >= 6,  margin_of_safety > 0.30

Usage:
    python scripts/quality_gate_search.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

# ---------------------------------------------------------------------------
# Load .env BEFORE importing project modules (they read env at import time)
# ---------------------------------------------------------------------------
from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ROOT / ".env")

# Override AWS env for moto — must happen before any boto3 import in our code
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ.pop("AWS_PROFILE", None)
os.environ["ENVIRONMENT"] = "dev"
os.environ["AWS_REGION"] = "us-east-1"
os.environ["MONTHLY_LLM_BUDGET_USD"] = "200.0"
os.environ["MONTHLY_LLM_BUDGET_CENTS"] = "0"

import boto3  # noqa: E402
import requests  # noqa: E402
import yfinance as yf  # noqa: E402
from moto import mock_aws  # noqa: E402

# ---------------------------------------------------------------------------
# Quality gate thresholds
# ---------------------------------------------------------------------------
MOAT_MIN = 7
MGMT_MIN = 6
MOS_MIN = 0.30  # 30%
MAX_EVALUATIONS = 30

# SEC EDGAR constants
SEC_BASE = "https://data.sec.gov"
SEC_FILES = "https://www.sec.gov/files"
SEC_RATE_LIMIT = 0.15
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "OmahaOracle research@example.com")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class Candidate:
    ticker: str
    company_name: str
    sector: str
    industry: str
    rationale: str


@dataclass
class EvalResult:
    ticker: str
    company_name: str
    moat_score: int = 0
    management_score: int = 0
    margin_of_safety: float = 0.0
    intrinsic_value: float = 0.0
    current_price: float = 0.0
    moat_type: str = ""
    passed: bool = False
    error: str = ""
    failed_gates: list[str] = field(default_factory=list)
    full_result: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# SEC EDGAR helpers
# ---------------------------------------------------------------------------

def _sec_headers() -> dict[str, str]:
    return {"User-Agent": SEC_USER_AGENT, "Accept": "application/json"}


def _get_cik(ticker: str) -> str | None:
    """Look up the CIK for a ticker from SEC's company_tickers.json."""
    url = f"{SEC_FILES}/company_tickers.json"
    time.sleep(SEC_RATE_LIMIT)
    resp = requests.get(url, headers=_sec_headers(), timeout=15)
    resp.raise_for_status()
    data = resp.json()
    ticker_upper = ticker.upper()
    for entry in data.values():
        if entry.get("ticker", "").upper() == ticker_upper:
            return str(entry["cik_str"]).zfill(10)
    return None


def _fetch_company_facts(cik: str) -> dict[str, Any]:
    """Fetch company facts from SEC XBRL API."""
    url = f"{SEC_BASE}/api/xbrl/companyfacts/CIK{cik}.json"
    time.sleep(SEC_RATE_LIMIT)
    resp = requests.get(url, headers=_sec_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def _extract_annual_value(facts: dict, taxonomy: str, concept: str) -> float:
    """Extract the most recent annual (10-K) value for a concept."""
    try:
        units = facts["facts"][taxonomy][concept]["units"]
        # Try USD first, then shares
        for unit_key in ["USD", "shares"]:
            if unit_key not in units:
                continue
            entries = units[unit_key]
            # Filter for annual filings (10-K or FY)
            annual = [
                e for e in entries
                if e.get("form") in ("10-K", "10-K/A")
                and e.get("val") is not None
            ]
            if not annual:
                continue
            # Sort by end date descending
            annual.sort(key=lambda e: e.get("end", ""), reverse=True)
            return float(annual[0]["val"])
    except (KeyError, IndexError):
        pass
    return 0.0


def _get_recent_filing_text(cik: str, ticker: str) -> str:
    """Fetch the most recent 10-K filing description from SEC."""
    url = f"{SEC_BASE}/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-K&dateb=&owner=include&count=1&search_text=&action=getcompany&output=atom"
    # Use the submissions API instead
    url = f"{SEC_BASE}/submissions/CIK{cik}.json"
    time.sleep(SEC_RATE_LIMIT)
    resp = requests.get(url, headers=_sec_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()

    # Build filing context from recent filings
    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accns = recent.get("accessionNumber", [])
    descriptions = recent.get("primaryDocDescription", [])

    lines = [f"Company: {data.get('name', ticker)} (CIK: {cik})"]
    lines.append(f"SIC: {data.get('sic', 'N/A')} - {data.get('sicDescription', 'N/A')}")
    lines.append(f"State: {data.get('stateOfIncorporation', 'N/A')}")
    lines.append(f"\nRecent SEC filings:")

    count = 0
    for i in range(min(20, len(forms))):
        f = forms[i] if i < len(forms) else ""
        d = dates[i] if i < len(dates) else ""
        desc = descriptions[i] if i < len(descriptions) else ""
        lines.append(f"  - {f} ({d}) {desc}")
        count += 1

    return "\n".join(lines)


def fetch_financial_data(ticker: str) -> tuple[dict[str, Any], str]:
    """Fetch financial metrics and filing context for a ticker.

    Returns (metrics_dict, filing_context_str).
    """
    print(f"    Fetching SEC EDGAR data for {ticker}...")
    cik = _get_cik(ticker)
    if not cik:
        raise ValueError(f"Could not find CIK for {ticker}")

    facts = _fetch_company_facts(cik)
    filing_context = _get_recent_filing_text(cik, ticker)

    # Extract key financial metrics
    concept_map = {
        "revenue": [("us-gaap", "Revenues"), ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax")],
        "net_income": [("us-gaap", "NetIncomeLoss")],
        "total_assets": [("us-gaap", "Assets")],
        "total_liabilities": [("us-gaap", "Liabilities")],
        "stockholders_equity": [("us-gaap", "StockholdersEquity")],
        "operating_cash_flow": [("us-gaap", "NetCashProvidedByUsedInOperatingActivities")],
        "capex": [("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment")],
        "depreciation": [("us-gaap", "DepreciationDepletionAndAmortization")],
        "shares_outstanding": [
            ("us-gaap", "CommonStockSharesOutstanding"),
            ("dei", "EntityCommonStockSharesOutstanding"),
        ],
        "current_assets": [("us-gaap", "AssetsCurrent")],
        "current_liabilities": [("us-gaap", "LiabilitiesCurrent")],
        "long_term_debt": [("us-gaap", "LongTermDebt"), ("us-gaap", "LongTermDebtNoncurrent")],
    }

    metrics: dict[str, Any] = {}
    for metric_name, concepts in concept_map.items():
        for taxonomy, concept in concepts:
            val = _extract_annual_value(facts, taxonomy, concept)
            if val != 0:
                metrics[metric_name] = val
                break

    # Get current price via yfinance
    print(f"    Fetching current price for {ticker}...")
    try:
        info = yf.Ticker(ticker).info
        price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose", 0)
        metrics["currentPrice"] = float(price)
        metrics["marketCap"] = float(info.get("marketCap", 0))
        metrics["sector"] = info.get("sector", "Unknown")
        metrics["industry"] = info.get("industry", "Unknown")
        metrics["trailingPE"] = info.get("trailingPE", 0)
        metrics["priceToBook"] = info.get("priceToBook", 0)
        metrics["trailingEps"] = info.get("trailingEps", 0)
        metrics["bookValue"] = info.get("bookValue", 0)
    except Exception as exc:
        print(f"    WARNING: yfinance failed for {ticker}: {exc}")

    # Compute owner earnings if not directly available
    ni = metrics.get("net_income", 0)
    dep = metrics.get("depreciation", 0)
    capex = metrics.get("capex", 0)
    if ni and capex:
        metrics["owner_earnings"] = ni + dep - 0.7 * capex

    # Compute net-net working capital
    ca = metrics.get("current_assets", 0)
    tl = metrics.get("total_liabilities", 0)
    if ca and tl:
        metrics["net_net_working_capital"] = ca - tl

    return metrics, filing_context


# ---------------------------------------------------------------------------
# AWS mock setup
# ---------------------------------------------------------------------------

TABLE_COST_TRACKING = "omaha-oracle-dev-cost-tracking"
TABLE_ANALYSIS = "omaha-oracle-dev-analysis"
TABLE_CONFIG = "omaha-oracle-dev-config"
TABLE_COMPANIES = "omaha-oracle-dev-companies"
S3_BUCKET = "omaha-oracle-dev-data"


def create_mock_tables():
    """Create all DynamoDB tables and S3 bucket needed by the pipeline."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")

    # Cost tracking table
    ddb.create_table(
        TableName=TABLE_COST_TRACKING,
        KeySchema=[
            {"AttributeName": "month_key", "KeyType": "HASH"},
            {"AttributeName": "timestamp", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "month_key", "AttributeType": "S"},
            {"AttributeName": "timestamp", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    # Analysis table
    ddb.create_table(
        TableName=TABLE_ANALYSIS,
        KeySchema=[
            {"AttributeName": "ticker", "KeyType": "HASH"},
            {"AttributeName": "analysis_date", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "ticker", "AttributeType": "S"},
            {"AttributeName": "analysis_date", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    # Config table
    ddb.create_table(
        TableName=TABLE_CONFIG,
        KeySchema=[{"AttributeName": "config_key", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "config_key", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )

    # Companies table
    ddb.create_table(
        TableName=TABLE_COMPANIES,
        KeySchema=[{"AttributeName": "ticker", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "ticker", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )

    # S3 bucket
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=S3_BUCKET)


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def run_pipeline(candidate: Candidate) -> EvalResult:
    """Run the full Omaha Oracle pipeline for one candidate."""
    result = EvalResult(ticker=candidate.ticker, company_name=candidate.company_name)

    try:
        metrics, filing_context = fetch_financial_data(candidate.ticker)
    except Exception as exc:
        result.error = f"Data fetch failed: {exc}"
        print(f"    ERROR: {result.error}")
        return result

    # Override sector/industry from candidate if yfinance didn't provide
    if metrics.get("sector", "Unknown") == "Unknown":
        metrics["sector"] = candidate.sector
    if metrics.get("industry", "Unknown") == "Unknown":
        metrics["industry"] = candidate.industry

    result.current_price = metrics.get("currentPrice", 0)

    # Clear config cache so fresh Settings are created inside mock
    from shared.config import _ssm_get, get_config
    _ssm_get.cache_clear()
    get_config.cache_clear()

    from dashboard.upload_validator import UploadMetadata
    from dashboard.analysis_runner import (
        build_analysis_event,
        run_upload_analysis,
    )

    metadata = UploadMetadata(
        ticker=candidate.ticker,
        company_name=candidate.company_name,
        filing_type="10-K",
        fiscal_year=2025,
    )

    event = build_analysis_event(
        metadata=metadata,
        upload_s3_key=f"uploads/{candidate.ticker}/2025/10-K/search.html",
        extra_metrics=metrics,
    )

    filing_text = (
        f"Uploaded filing: 10-K for {candidate.company_name} "
        f"({candidate.ticker}), FY2025\n\n{filing_context}\n\n"
        f"Financial metrics:\n{json.dumps(metrics, indent=2, default=str)}"
    )

    def progress(stage_name: str, stage_num: int) -> None:
        print(f"    Stage {stage_num}/4: {stage_name.replace('_', ' ').title()}")

    try:
        pipeline_result = run_upload_analysis(
            event=event,
            filing_context=filing_text,
            progress_callback=progress,
        )
    except Exception as exc:
        result.error = f"Pipeline failed: {exc}"
        print(f"    ERROR: {result.error}")
        traceback.print_exc()
        return result

    # Extract scores
    result.moat_score = int(pipeline_result.get("moat_score", 0))
    result.management_score = int(pipeline_result.get("management_score", 0))
    result.margin_of_safety = float(pipeline_result.get("margin_of_safety", 0))
    result.intrinsic_value = float(pipeline_result.get("intrinsic_value_per_share", 0))
    result.current_price = float(pipeline_result.get("current_price", 0))
    result.moat_type = pipeline_result.get("moat_type", "")
    result.full_result = pipeline_result

    # Check gates
    failed = []
    if result.moat_score < MOAT_MIN:
        failed.append(f"moat={result.moat_score}<{MOAT_MIN}")
    if result.management_score < MGMT_MIN:
        failed.append(f"mgmt={result.management_score}<{MGMT_MIN}")
    if result.margin_of_safety <= MOS_MIN:
        failed.append(f"MoS={result.margin_of_safety:.1%}<={MOS_MIN:.0%}")

    result.failed_gates = failed
    result.passed = len(failed) == 0

    return result


# ---------------------------------------------------------------------------
# Candidate lists
# ---------------------------------------------------------------------------

INITIAL_CANDIDATES = [
    Candidate("AAPL", "Apple Inc.", "Technology", "Consumer Electronics",
              "Massive ecosystem lock-in, brand power, services moat"),
    Candidate("GOOG", "Alphabet Inc.", "Technology", "Internet Content & Information",
              "Search monopoly, YouTube, cloud — wide moat candidate"),
    Candidate("JNJ", "Johnson & Johnson", "Healthcare", "Drug Manufacturers",
              "Diversified healthcare giant, 60+ year dividend growth"),
    Candidate("BRK-B", "Berkshire Hathaway", "Financials", "Insurance — Diversified",
              "Buffett's own conglomerate, float-funded compounding"),
    Candidate("V", "Visa Inc.", "Financials", "Credit Services",
              "Dominant payment network, near-zero marginal cost"),
    Candidate("COST", "Costco Wholesale", "Consumer Defensive", "Discount Stores",
              "Membership moat, extreme customer loyalty, scale advantages"),
    Candidate("PG", "Procter & Gamble", "Consumer Defensive", "Household & Personal Products",
              "Brand portfolio, pricing power, 67-year dividend king"),
    Candidate("MCO", "Moody's Corporation", "Financials", "Financial Data & Stock Exchanges",
              "Duopoly in credit ratings, high switching costs"),
    Candidate("MSFT", "Microsoft Corporation", "Technology", "Software — Infrastructure",
              "Enterprise lock-in, Azure cloud, Office ecosystem"),
    Candidate("KO", "The Coca-Cola Company", "Consumer Defensive", "Beverages — Non-Alcoholic",
              "Global brand, distribution moat, 60+ year dividend growth"),
    Candidate("WMT", "Walmart Inc.", "Consumer Defensive", "Discount Stores",
              "Scale advantages, supply chain moat, growing e-commerce"),
    Candidate("MRK", "Merck & Co.", "Healthcare", "Drug Manufacturers — General",
              "Strong pipeline, Keytruda franchise, R&D moat"),
    Candidate("UNH", "UnitedHealth Group", "Healthcare", "Healthcare Plans",
              "Vertically integrated health ecosystem, Optum data moat"),
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 72)
    print("  OMAHA ORACLE — Quality Gate Search")
    print("=" * 72)
    print()
    print("Pipeline Discovery")
    print("-" * 40)
    print("  Stages : moat (Sonnet) -> mgmt (Sonnet) -> IV (math) -> thesis (Opus)")
    print("  Scores : moat 1-10, mgmt 1-10, MoS 0.0-1.0 (decimal)")
    print(f"  Gates  : moat >= {MOAT_MIN}, mgmt >= {MGMT_MIN}, MoS > {MOS_MIN:.0%}")
    print("  Data   : SEC EDGAR XBRL + yfinance prices")
    print("  AWS    : moto-mocked DynamoDB & S3")
    print()

    candidates = list(INITIAL_CANDIDATES)
    results: list[EvalResult] = []
    winner: EvalResult | None = None

    print("Search Progress")
    print("-" * 40)
    print(f"{'#':<3} {'Company':<25} {'Moat':<6} {'Mgmt':<6} {'MoS':<8} {'Result'}")
    print("-" * 72)

    with mock_aws():
        create_mock_tables()

        for i, candidate in enumerate(candidates):
            if i >= MAX_EVALUATIONS:
                print(f"\nMax evaluations ({MAX_EVALUATIONS}) reached. Stopping.")
                break

            print(f"\n[{i+1}/{len(candidates)}] Evaluating {candidate.company_name} ({candidate.ticker})...")

            result = run_pipeline(candidate)
            results.append(result)

            # Print result row
            if result.error:
                status = f"ERROR: {result.error[:30]}"
            elif result.passed:
                status = "PASS"
            else:
                status = f"FAIL ({', '.join(result.failed_gates)})"

            moat_str = f"{result.moat_score}/10" if result.moat_score else "—"
            mgmt_str = f"{result.management_score}/10" if result.management_score else "—"
            mos_str = f"{result.margin_of_safety:.1%}" if result.margin_of_safety else "—"

            print(f"\n{i+1:<3} {candidate.company_name:<25} {moat_str:<6} {mgmt_str:<6} {mos_str:<8} {status}")

            if result.passed:
                winner = result
                print(f"\n*** WINNER FOUND: {result.company_name} ({result.ticker}) ***")
                break

    # ---------------------------------------------------------------------------
    # Final report
    # ---------------------------------------------------------------------------
    print()
    print("=" * 72)
    print("  FINAL REPORT")
    print("=" * 72)

    if winner:
        print()
        print(f"  Winner: {winner.company_name} ({winner.ticker})")
        print(f"  Moat Score:        {winner.moat_score}/10 ({winner.moat_type})")
        print(f"  Management Score:  {winner.management_score}/10")
        print(f"  Intrinsic Value:   ${winner.intrinsic_value:,.2f}/share")
        print(f"  Current Price:     ${winner.current_price:,.2f}/share")
        print(f"  Margin of Safety:  {winner.margin_of_safety:.1%}")
        print(f"  Buy Signal:        {'YES' if winner.full_result.get('buy_signal') else 'NO'}")

        if winner.full_result.get("thesis_generated"):
            print(f"  Thesis Generated:  Yes (S3: {winner.full_result.get('thesis_s3_key', 'N/A')})")
        else:
            reason = winner.full_result.get("skipped_reason", "N/A")
            print(f"  Thesis Generated:  No ({reason})")

        scenarios = winner.full_result.get("scenarios", {})
        if scenarios:
            print()
            print("  Valuation Scenarios:")
            for name in ("bear", "base", "bull"):
                s = scenarios.get(name, {})
                if s:
                    print(f"    {name.title()}: ${s.get('dcf_per_share', 0):,.2f}/share "
                          f"({s.get('growth_pct', 0)}% growth, {s.get('probability', 0):.0%} prob)")
    else:
        print()
        print("  No company passed all quality gates.")
        print()
        # Find closest misses
        scored = [r for r in results if not r.error]
        if scored:
            scored.sort(key=lambda r: (r.moat_score + r.management_score + r.margin_of_safety * 10), reverse=True)
            print("  Closest misses:")
            for r in scored[:3]:
                print(f"    {r.company_name}: moat={r.moat_score}, mgmt={r.management_score}, "
                      f"MoS={r.margin_of_safety:.1%} — failed: {', '.join(r.failed_gates)}")

    # Summary table
    print()
    print(f"  Companies evaluated: {len(results)}")
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed and not r.error)
    errors = sum(1 for r in results if r.error)
    print(f"  Passed: {passed}  |  Failed: {failed}  |  Errors: {errors}")
    print()
    print(f"  {'Company':<25} {'Moat':<6} {'Mgmt':<6} {'MoS':<8} {'IV':<12} {'Price':<10} {'Result'}")
    print("  " + "-" * 70)
    for r in results:
        if r.error:
            status = "ERROR"
        elif r.passed:
            status = "PASS"
        else:
            status = "FAIL"
        moat_str = f"{r.moat_score}/10" if r.moat_score else "—"
        mgmt_str = f"{r.management_score}/10" if r.management_score else "—"
        mos_str = f"{r.margin_of_safety:.1%}" if r.margin_of_safety else "—"
        iv_str = f"${r.intrinsic_value:,.2f}" if r.intrinsic_value else "—"
        price_str = f"${r.current_price:,.2f}" if r.current_price else "—"
        print(f"  {r.company_name:<25} {moat_str:<6} {mgmt_str:<6} {mos_str:<8} {iv_str:<12} {price_str:<10} {status}")

    print()
    print("=" * 72)


if __name__ == "__main__":
    main()
