"""Candidate generation and pre-screening for company search."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from typing import Any

from ingestion.sec_edgar.handler import _get_ticker_to_cik
from ingestion.sec_edgar.handler import _process_ticker as _sec_process_ticker
from ingestion.yahoo_finance.handler import _fetch_prices
from ingestion.yahoo_finance.handler import _process_ticker as _yf_process_ticker
from shared.config import get_config
from shared.dynamo_client import DynamoClient
from shared.logger import get_logger
from shared.s3_client import S3Client

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Module-level cache for SEC universe
# ---------------------------------------------------------------------------

_universe_cache: dict[str, str] | None = None

MIN_MARKET_CAP = 1_000_000_000  # $1B


def load_sec_universe(user_agent: str) -> dict[str, str]:
    """Load the full SEC ticker-to-CIK universe, caching after first call."""
    global _universe_cache  # noqa: PLW0603
    if _universe_cache is None:
        _universe_cache = _get_ticker_to_cik(user_agent)
    return _universe_cache


def pre_screen_ticker(ticker: str) -> tuple[bool, dict[str, Any]]:
    """Cheaply pre-screen a ticker via yfinance before pipeline evaluation.

    Checks: market cap > $1B, has trailing P/E, has sector (not ETF/fund).
    Returns (passes, info_dict).
    """
    try:
        info = _fetch_prices(ticker)
    except Exception:
        _log.debug("Pre-screen fetch failed", extra={"ticker": ticker})
        return False, {}

    if not info:
        return False, {}

    market_cap = info.get("marketCap")
    if not market_cap or market_cap < MIN_MARKET_CAP:
        return False, info

    if info.get("trailingPE") is None:
        return False, info

    if "sector" not in info:
        return False, info

    return True, info


class CandidateGenerator:
    """Generates batches of ticker candidates from the SEC universe."""

    def __init__(
        self,
        evaluated: set[str] | None = None,
        seed: int | None = None,
    ) -> None:
        self._evaluated = evaluated or set()
        self._rng = random.Random(seed)
        self._shuffled: list[str] | None = None
        self._index = 0

    def generate_batch(self, batch_size: int = 10) -> list[str]:
        """Return the next batch of unevaluated tickers."""
        if self._shuffled is None:
            universe = load_sec_universe("omaha-oracle/1.0 dashboard-search")
            tickers = [t for t in universe if t not in self._evaluated]
            self._rng.shuffle(tickers)
            self._shuffled = tickers

        start = self._index
        end = start + batch_size
        batch = self._shuffled[start:end]
        self._index = end
        return batch

    def get_cik(self, ticker: str) -> str | None:
        """Look up the CIK for a ticker from the cached universe."""
        universe = load_sec_universe("omaha-oracle/1.0 dashboard-search")
        return universe.get(ticker)


def ingest_ticker_data(ticker: str, cik: str) -> bool:
    """Ingest Yahoo Finance + SEC EDGAR data for a ticker.

    Returns True if at least Yahoo Finance data was ingested successfully.
    """
    cfg = get_config()
    companies = DynamoClient(cfg.table_companies)
    s3 = S3Client()
    updated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")

    try:
        _yf_process_ticker(ticker, companies, s3, include_history=True, updated_at=updated_at)
    except Exception:
        _log.warning("Yahoo Finance ingestion failed", extra={"ticker": ticker})
        return False

    try:
        dynamo = DynamoClient(cfg.table_financials)
        _sec_process_ticker(ticker, cik, cfg, s3, dynamo, date_str)
    except Exception:
        _log.warning("SEC EDGAR ingestion failed (continuing)", extra={"ticker": ticker})

    return True
