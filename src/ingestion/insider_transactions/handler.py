"""
Lambda handler for SEC EDGAR Form 4 (insider transaction) ingestion.

Scans Form 4 filings across the watchlist for the last 30 days.
Stores raw filing data to S3 and flags significant insider buys (> $100K).
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

from shared.config import get_config
from shared.converters import check_failure_threshold
from shared.dynamo_client import get_watchlist_tickers
from shared.http_client import TIMEOUT, get_session
from shared.logger import get_logger
from shared.s3_client import S3Client
from shared.sec_client import get_ticker_to_cik as _get_ticker_to_cik

_log = get_logger(__name__)

SEC_BASE = "https://data.sec.gov"
SEC_FILES = "https://www.sec.gov/files"
SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
RATE_LIMIT_SLEEP = 0.15  # 150ms between requests
SIGNIFICANT_BUY_THRESHOLD = 100_000  # USD
LOOKBACK_DAYS = 30

# Module-level lock ensures SEC rate limiting is global, not per-thread.
# Without this, 10 ThreadPoolExecutor threads would burst 10x against SEC.
_rate_lock = threading.Lock()
_last_request_time = 0.0


def _rate_limit() -> None:
    global _last_request_time
    with _rate_lock:
        now = time.monotonic()
        elapsed = now - _last_request_time
        if elapsed < RATE_LIMIT_SLEEP:
            time.sleep(RATE_LIMIT_SLEEP - elapsed)
        _last_request_time = time.monotonic()


def _cik_pad(cik: int | str) -> str:
    return str(cik).zfill(10)


def _fetch(url: str, headers: dict[str, str]) -> requests.Response:
    _rate_limit()
    resp = get_session().get(url, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp


def _fetch_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = _fetch(url, headers).json()
    return result


def _accn_to_path(accn: str) -> str:
    """Convert accession number to URL path segment (dashes removed)."""
    return accn.replace("-", "")


def _is_significant_buy(xml_content: str) -> bool:
    """
    Parse Form 4 XML and return True if any insider buy exceeds $100K.
    Looks for Acquired (A) transactions: value = shares * price.
    """
    if not xml_content.strip().lower().startswith("<"):
        return False

    total_acquired_value = 0.0
    # Window around each "A" to find paired shares/price (elements can be in any order)
    window = 800

    for m in re.finditer(
        r"<transactionAcquiredDisposedCode[^>]*>\s*A\s*</transactionAcquiredDisposedCode>",
        xml_content,
        re.IGNORECASE,
    ):
        start = max(0, m.start() - window)
        end = min(len(xml_content), m.end() + window)
        block = xml_content[start:end]
        shares_m = re.search(
            r"<transactionShares[^>]*>([\d,]+)</transactionShares>",
            block,
            re.IGNORECASE,
        )
        price_m = re.search(
            r"<transactionPricePerShare[^>]*>([\d.]+)</transactionPricePerShare>",
            block,
            re.IGNORECASE,
        )
        if shares_m and price_m:
            try:
                shares = float(shares_m.group(1).replace(",", ""))
                price = float(price_m.group(1))
                total_acquired_value += shares * price
            except ValueError:
                _log.warning(
                    "Failed to parse shares/price in Form 4",
                    extra={"shares_raw": shares_m.group(1), "price_raw": price_m.group(1)},
                )

    return total_acquired_value >= SIGNIFICANT_BUY_THRESHOLD


def _process_ticker(
    ticker: str,
    cik: str,
    user_agent: str,
    s3: S3Client,
    cutoff_date: datetime,
) -> tuple[int, int]:
    """
    Fetch Form 4 filings for one ticker in the last 30 days.
    Returns (filing_count, significant_buy_count).
    """
    headers = {"User-Agent": user_agent}
    url = f"{SEC_BASE}/submissions/CIK{cik}.json"
    try:
        data = _fetch_json(url, headers)
    except requests.RequestException as exc:
        _log.warning("Failed to fetch submissions", extra={"ticker": ticker, "error": str(exc)})
        return 0, 0

    recent = data.get("filings", {}).get("recent", {})
    forms = recent.get("form", []) or []
    accns = recent.get("accessionNumber", []) or []
    dates = recent.get("filingDate", []) or []
    primaries = recent.get("primaryDocument", []) or []

    stored = 0
    significant = 0

    for i, form in enumerate(forms):
        if form not in ("4", "4/A"):
            continue
        if i >= len(accns) or i >= len(dates) or i >= len(primaries):
            continue
        filing_date_str = dates[i]
        try:
            filing_date = datetime.strptime(filing_date_str, "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            continue
        if filing_date < cutoff_date:
            continue

        accn = accns[i]
        primary_doc = primaries[i] or "form4.xml"
        accn_path = _accn_to_path(accn)
        doc_url = f"{SEC_ARCHIVES}/{cik}/{accn_path}/{primary_doc}"

        try:
            resp = _fetch(doc_url, headers)
            content = resp.text
        except requests.RequestException as exc:
            _log.warning(
                "Failed to fetch filing",
                extra={"ticker": ticker, "accn": accn, "error": str(exc)},
            )
            continue

        sig_buy = _is_significant_buy(content) if content.strip().lower().startswith("<") else False
        if sig_buy:
            significant += 1

        payload: dict[str, Any] = {
            "accession_number": accn,
            "filing_date": filing_date_str,
            "form_type": form,
            "primary_document": primary_doc,
            "content": content,
            "significant_buy": sig_buy,
        }

        s3_key = f"raw/insider/{ticker}/{filing_date_str}/{accn}.json"
        s3.write_json(s3_key, payload, indent=None)
        stored += 1
        _log.info(
            "Stored Form 4",
            extra={"ticker": ticker, "accn": accn, "significant_buy": sig_buy},
        )

    return stored, significant


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    Scans Form 4 filings for all watchlist tickers in the last 30 days.
    """
    cfg = get_config()
    s3 = S3Client()
    user_agent = cfg.get_sec_user_agent()

    cutoff = datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)
    ticker_to_cik = _get_ticker_to_cik(user_agent)
    tickers = get_watchlist_tickers(cfg.table_watchlist)

    if not tickers:
        _log.warning("Watchlist empty")
        return {"status": "ok", "filings_stored": 0, "significant_buys": 0}

    total_stored = 0
    total_significant = 0
    errors: list[str] = []

    def _fetch_one(ticker: str) -> tuple[str, int, int]:
        cik = ticker_to_cik.get(ticker)
        if not cik:
            raise ValueError(f"CIK not found for {ticker}")
        stored, sig = _process_ticker(ticker, cik, user_agent, s3, cutoff)
        return ticker, stored, sig

    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_fetch_one, t): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            exc = fut.exception()
            if exc:
                errors.append(f"{t}: {exc}")
                _log.exception(
                    "Ingestion failed",
                    extra={"ticker": t, "stage": "form4_fetch", "error_type": type(exc).__name__},
                )
            else:
                _, stored, sig = fut.result()
                total_stored += stored
                total_significant += sig

    check_failure_threshold(errors, len(tickers), "Insider transactions")

    return {
        "status": "ok",
        "filings_stored": total_stored,
        "significant_buys": total_significant,
        "errors": errors[:10],
    }
