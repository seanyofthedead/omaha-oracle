"""
Lambda handler for SEC EDGAR data ingestion.

Event types:
  - {"action": "full_refresh"}       — refresh all watchlist companies
  - {"action": "scan_new_filings"}   — check for new 8-K/insider filings (daily)
  - {"action": "single", "ticker": "AAPL"} — refresh one company
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from shared.config import get_config
from shared.converters import check_failure_threshold, today_str
from shared.dynamo_client import DynamoClient, get_watchlist_tickers
from shared.http_client import TIMEOUT, get_session
from shared.logger import get_logger
from shared.s3_client import S3Client

_log = get_logger(__name__)

# ------------------------------------------------------------------ #
# Constants                                                           #
# ------------------------------------------------------------------ #

SEC_BASE = "https://data.sec.gov"
SEC_FILES = "https://www.sec.gov/files"
RATE_LIMIT_SLEEP = 0.15  # 150ms between requests (~6.7 req/s, under 10 req/s)
YEARS_TO_STORE = 10

# Our metric names → SEC US-GAAP concept names (with fallbacks)
METRIC_CONCEPTS: dict[str, list[tuple[str, str]]] = {
    "revenue": [("us-gaap", "Revenues"), ("us-gaap", "Revenue")],
    "net_income": [("us-gaap", "NetIncomeLoss")],
    "total_assets": [("us-gaap", "Assets")],
    "total_liabilities": [("us-gaap", "Liabilities")],
    "stockholders_equity": [("us-gaap", "StockholdersEquity")],
    "operating_cash_flow": [
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
    ],
    "capex": [
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
    ],
    "depreciation": [
        ("us-gaap", "DepreciationDepletionAndAmortization"),
    ],
    "shares_outstanding": [
        ("us-gaap", "CommonStockSharesOutstanding"),
        ("dei", "EntityCommonStockSharesOutstanding"),
    ],
    "current_assets": [("us-gaap", "CurrentAssets")],
    "current_liabilities": [("us-gaap", "CurrentLiabilities")],
    "long_term_debt": [("us-gaap", "LongTermDebt")],
    "dividends_paid": [("us-gaap", "DividendsPaid")],
}


def _rate_limit() -> None:
    time.sleep(RATE_LIMIT_SLEEP)


def _cik_pad(cik: int | str) -> str:
    """Zero-pad CIK to 10 digits for SEC API."""
    return str(cik).zfill(10)


def _fetch_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    _rate_limit()
    resp = get_session().get(url, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def _get_ticker_to_cik(user_agent: str) -> dict[str, str]:
    """Fetch SEC company_tickers.json and build ticker → CIK map."""
    url = f"{SEC_FILES}/company_tickers.json"
    headers = {"User-Agent": user_agent}
    data = _fetch_json(url, headers)
    out: dict[str, str] = {}
    for entry in data.values():
        if isinstance(entry, dict):
            ticker = entry.get("ticker")
            cik = entry.get("cik_str") or entry.get("cik")
            if ticker and cik is not None:
                out[str(ticker).upper()] = _cik_pad(cik)
    return out


def _extract_annual_facts(
    facts: dict[str, Any], metric_name: str, unit_key: str = "USD"
) -> list[dict[str, Any]]:
    """
    Extract annual (10-K, FY) data points for a metric from companyfacts.
    Returns list of {end, val, form, fy, fp}.
    """
    results: list[dict[str, Any]] = []
    for taxonomy, concept in METRIC_CONCEPTS.get(metric_name, []):
        tax = facts.get(taxonomy, {})
        concept_data = tax.get(concept, {})
        units = concept_data.get("units", {})
        # Try USD first, then "shares" for shares_outstanding
        for uk in (unit_key, "shares", "USD"):
            if uk not in units:
                continue
            for item in units[uk]:
                if not isinstance(item, dict):
                    continue
                form = item.get("form") or ""
                fp = item.get("fp") or ""
                # Annual: 10-K or FY
                if form == "10-K" or fp == "FY":
                    end = item.get("end")
                    val = item.get("val")
                    if end is not None and val is not None:
                        results.append(
                            {
                                "end": end,
                                "val": val,
                                "form": form,
                                "fy": item.get("fy"),
                                "fp": fp,
                            }
                        )
            if results:
                break
        if results:
            break
    return results


def _process_ticker(
    ticker: str,
    cik: str,
    cfg: Any,
    s3: S3Client,
    dynamo: DynamoClient,
    date_str: str,
) -> int:
    """
    Fetch facts + submissions for one ticker, store raw JSON to S3,
    extract financials to DynamoDB. Returns count of financial items written.
    """
    headers = {"User-Agent": cfg.get_sec_user_agent()}
    base = f"raw/sec/{ticker}/{date_str}"

    # 1. Company facts
    facts_url = f"{SEC_BASE}/api/xbrl/companyfacts/CIK{cik}.json"
    facts = _fetch_json(facts_url, headers)
    s3.write_json(f"{base}/facts.json", facts)

    # 2. Submissions (recent filings index)
    subs_url = f"{SEC_BASE}/submissions/CIK{cik}.json"
    submissions = _fetch_json(subs_url, headers)
    s3.write_json(f"{base}/filings.json", submissions)

    # 3. Extract annual financials (10 years)
    _facts_inner = facts.get("facts") or {}
    us_gaap = _facts_inner.get("us-gaap", {})
    dei = _facts_inner.get("dei", {})
    all_facts = {"us-gaap": us_gaap, "dei": dei}

    items: list[dict[str, Any]] = []
    cutoff_year = datetime.now(UTC).year - YEARS_TO_STORE

    for metric_name in METRIC_CONCEPTS:
        unit_key = "shares" if metric_name == "shares_outstanding" else "USD"
        points = _extract_annual_facts(all_facts, metric_name, unit_key)
        for p in points:
            end = p.get("end", "")
            fy = p.get("fy")
            if fy is not None and int(fy) < cutoff_year:
                continue
            sk = f"{end}#{metric_name}"
            item: dict[str, Any] = {
                "ticker": ticker,
                "period": sk,
                "period_end_date": end,
                "metric_name": metric_name,
                "value": p["val"],
                "form": p.get("form", ""),
                "fiscal_period": p.get("fp", ""),
            }
            fy = p.get("fy")
            if fy is not None:
                item["fiscal_year"] = fy
            items.append(item)

    for item in items:
        dynamo.put_item(item)
    return len(items)


def _scan_new_filings(
    ticker: str,
    cik: str,
    cfg: Any,
    s3: S3Client,
) -> None:
    """
    Check submissions for new 8-K or insider filings.
    Stores filings.json to S3 for the given date (caller can diff later).
    """
    headers = {"User-Agent": cfg.get_sec_user_agent()}
    date_str = today_str()
    subs_url = f"{SEC_BASE}/submissions/CIK{cik}.json"
    submissions = _fetch_json(subs_url, headers)
    base = f"raw/sec/{ticker}/{date_str}"
    s3.write_json(f"{base}/filings.json", submissions)
    _log.info(
        "Stored filings for scan",
        extra={"ticker": ticker, "cik": cik, "key": f"{base}/filings.json"},
    )


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    Event schema:
      action: "full_refresh" | "scan_new_filings" | "single"
      ticker: str (required for "single")
    """
    cfg = get_config()
    s3 = S3Client()
    date_str = today_str()

    financials_client = DynamoClient(cfg.table_financials)

    action = (event.get("action") or "").strip().lower()
    ticker_arg = (event.get("ticker") or "").strip().upper()

    if action == "single" and not ticker_arg:
        return {"status": "error", "message": "ticker required for action=single"}

    ticker_to_cik = _get_ticker_to_cik(cfg.get_sec_user_agent())
    processed = 0
    errors: list[str] = []

    if action == "full_refresh":
        tickers = get_watchlist_tickers(cfg.table_watchlist)
        if not tickers:
            _log.warning("Watchlist empty — nothing to refresh")
            return {"status": "ok", "processed": 0, "message": "watchlist empty"}
        for t in tickers:
            cik = ticker_to_cik.get(t)
            if not cik:
                errors.append(f"{t}: CIK not found")
                continue
            try:
                n = _process_ticker(t, cik, cfg, s3, financials_client, date_str)
                processed += n
                _log.info("Refreshed ticker", extra={"ticker": t, "financial_items": n})
            except Exception as exc:
                errors.append(f"{t}: {exc}")
                _log.exception(
                    "Ingestion failed",
                    extra={"ticker": t, "stage": "full_refresh", "error_type": type(exc).__name__},
                )
        check_failure_threshold(errors, len(tickers), "SEC EDGAR full_refresh")

    elif action == "scan_new_filings":
        tickers = get_watchlist_tickers(cfg.table_watchlist)
        for t in tickers:
            cik = ticker_to_cik.get(t)
            if not cik:
                continue
            try:
                _scan_new_filings(t, cik, cfg, s3)
            except Exception as exc:
                errors.append(f"{t}: {exc}")
                _log.exception(
                    "Ingestion failed",
                    extra={
                        "ticker": t,
                        "stage": "scan_new_filings",
                        "error_type": type(exc).__name__,
                    },
                )
        check_failure_threshold(errors, len(tickers), "SEC EDGAR scan_new_filings")

    elif action == "single":
        cik = ticker_to_cik.get(ticker_arg)
        if not cik:
            return {"status": "error", "message": f"CIK not found for {ticker_arg}"}
        try:
            processed = _process_ticker(ticker_arg, cik, cfg, s3, financials_client, date_str)
            _log.info(
                "Single refresh complete",
                extra={"ticker": ticker_arg, "financial_items": processed},
            )
        except Exception as exc:
            _log.exception(
                "Ingestion failed",
                extra={"ticker": ticker_arg, "stage": "single", "error_type": type(exc).__name__},
            )
            return {
                "status": "error",
                "message": "Data fetch failed — see CloudWatch logs for details",
            }

    else:
        return {"status": "error", "message": f"unknown action: {action}"}

    return {
        "status": "ok",
        "action": action,
        "processed": processed,
        "errors": errors[:10],
    }
