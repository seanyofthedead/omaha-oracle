"""
Shared SEC EDGAR utilities.

Consolidates the ticker-to-CIK lookup that was previously duplicated in
``ingestion.sec_edgar.handler`` and ``ingestion.insider_transactions.handler``.
"""

from __future__ import annotations

import time
from typing import Any

from shared.http_client import TIMEOUT, get_session

SEC_FILES = "https://www.sec.gov/files"
RATE_LIMIT_SLEEP = 0.15  # 150ms between requests (~6.7 req/s, under 10 req/s)


def _cik_pad(cik: int | str) -> str:
    """Zero-pad CIK to 10 digits for SEC API."""
    return str(cik).zfill(10)


def _fetch_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    """Fetch JSON from a URL with rate limiting."""
    time.sleep(RATE_LIMIT_SLEEP)
    resp = get_session().get(url, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def get_ticker_to_cik(user_agent: str) -> dict[str, str]:
    """Build ticker -> CIK map from SEC company_tickers.json.

    Parameters
    ----------
    user_agent:
        The User-Agent string required by SEC EDGAR fair-use policy.

    Returns
    -------
    dict mapping uppercase ticker symbols to zero-padded 10-digit CIK strings.
    """
    data = _fetch_json(f"{SEC_FILES}/company_tickers.json", {"User-Agent": user_agent})
    out: dict[str, str] = {}
    for entry in data.values():
        if isinstance(entry, dict):
            ticker = entry.get("ticker")
            cik = entry.get("cik_str") or entry.get("cik")
            if ticker and cik is not None:
                out[str(ticker).upper()] = _cik_pad(cik)
    return out
