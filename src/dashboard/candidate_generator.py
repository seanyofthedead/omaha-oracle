"""Candidate generation and pre-screening for company search.

Implements a 3-tier screening funnel:
  Tier 1: Yahoo Finance EquityQuery bulk filter (P/E, P/B, D/E, MCap, ROE, FCF)
  Tier 2: Composite scoring & ranking by gate proximity
  Tier 3: Full pipeline (quant screen, moat, management, IV, thesis) — handled by search_runner
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import yfinance as yf
from yfinance import EquityQuery

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
# Module-level cache for SEC universe (used for CIK lookups)
# ---------------------------------------------------------------------------

_universe_cache: dict[str, str] | None = None

MIN_MARKET_CAP = 1_000_000_000  # $1B

# ---------------------------------------------------------------------------
# Tier 1: EquityQuery bulk filter
# ---------------------------------------------------------------------------

# Yahoo screener field names mapped to quality gate criteria
_FIELD_PE = "peratio.lasttwelvemonths"
_FIELD_PB = "pricebookratio.quarterly"
_FIELD_DE = "ltdebtequity.lasttwelvemonths"
_FIELD_MCAP = "intradaymarketcap"
_FIELD_ROE = "returnonequity.lasttwelvemonths"
_FIELD_FCF = "leveredfreecashflow.lasttwelvemonths"

# Scoring thresholds (defaults aligned with quant screen)
_DEFAULT_PE_MAX = 15.0
_DEFAULT_PB_MAX = 1.5
_DEFAULT_DE_MAX = 0.5
_DEFAULT_ROE_MIN = 12.0  # percent
_DEFAULT_ROE_CEILING = 30.0  # percent — for score normalization
_DEFAULT_FCF_YIELD_CEILING = 0.10  # 10% FCF yield = max score

# Composite score weights
SCORE_WEIGHTS: dict[str, float] = {
    "pe": 0.20,
    "pb": 0.20,
    "de": 0.15,
    "roe": 0.25,
    "fcf_yield": 0.20,
}


def build_screener_query() -> EquityQuery:
    """Build a yfinance EquityQuery matching Omaha Oracle quality gate proxies."""
    return EquityQuery(
        "and",
        [
            EquityQuery("is-in", ["exchange", "NMS", "NYQ"]),
            EquityQuery("gt", [_FIELD_MCAP, MIN_MARKET_CAP]),
            EquityQuery("btwn", [_FIELD_PE, 0, _DEFAULT_PE_MAX]),
            EquityQuery("btwn", [_FIELD_PB, 0, _DEFAULT_PB_MAX]),
            EquityQuery("lt", [_FIELD_DE, _DEFAULT_DE_MAX]),
            EquityQuery("gt", [_FIELD_ROE, _DEFAULT_ROE_MIN]),
            EquityQuery("gt", [_FIELD_FCF, 0]),
        ],
    )


def fetch_screener_candidates(max_results: int = 500) -> list[dict[str, Any]]:
    """Execute the screener query with pagination, returning candidate dicts.

    Each dict contains at minimum: ``symbol`` plus any screener-provided fields.
    Returns up to *max_results* candidates sorted by P/E ascending (cheapest first).
    """
    query = build_screener_query()
    candidates: list[dict[str, Any]] = []
    page_size = min(250, max_results)  # Yahoo caps at 250 per call
    offset = 0

    while len(candidates) < max_results:
        try:
            resp = yf.screen(
                query,
                offset=offset,
                size=page_size,
                sortField=_FIELD_PE,
                sortAsc=True,
            )
        except Exception:
            _log.warning("Screener API call failed", extra={"offset": offset})
            break

        quotes = resp.get("quotes", []) if isinstance(resp, dict) else []
        if not quotes:
            break

        candidates.extend(quotes)
        offset += page_size

        if len(quotes) < page_size:
            break  # No more pages

    return candidates[:max_results]


# ---------------------------------------------------------------------------
# Tier 2: Composite scoring & ranking
# ---------------------------------------------------------------------------


def _score_pe(pe: float) -> float:
    """Lower P/E is better. 0 at threshold (15), 1 at P/E near 0."""
    if pe <= 0 or pe >= _DEFAULT_PE_MAX:
        return 0.0
    return 1.0 - (pe / _DEFAULT_PE_MAX)


def _score_pb(pb: float) -> float:
    """Lower P/B is better. 0 at threshold (1.5), 1 at P/B near 0."""
    if pb <= 0 or pb >= _DEFAULT_PB_MAX:
        return 0.0
    return 1.0 - (pb / _DEFAULT_PB_MAX)


def _score_de(de: float) -> float:
    """Lower D/E is better. 0 at threshold (0.5), 1 at D/E=0."""
    if de < 0 or de >= _DEFAULT_DE_MAX:
        return 0.0
    return 1.0 - (de / _DEFAULT_DE_MAX)


def _score_roe(roe: float) -> float:
    """Higher ROE is better. 0 at threshold (12%), 1 at 30%+."""
    if roe <= _DEFAULT_ROE_MIN:
        return 0.0
    return min(1.0, (roe - _DEFAULT_ROE_MIN) / (_DEFAULT_ROE_CEILING - _DEFAULT_ROE_MIN))


def _score_fcf_yield(fcf: float, mcap: float) -> float:
    """Higher FCF yield is better. 0 at zero, 1 at 10%+ yield."""
    if mcap <= 0 or fcf <= 0:
        return 0.0
    yield_ = fcf / mcap
    return min(1.0, yield_ / _DEFAULT_FCF_YIELD_CEILING)


def score_candidate(candidate: dict[str, Any]) -> float:
    """Compute a composite gate-proximity score (0.0–1.0) for a screener candidate.

    Missing fields are scored as 0 and their weight is redistributed.
    """
    scores: dict[str, float | None] = {}

    pe = candidate.get("trailingPE") or candidate.get("peRatio")
    scores["pe"] = _score_pe(float(pe)) if pe is not None else None

    pb = candidate.get("priceToBook") or candidate.get("priceBookRatio")
    scores["pb"] = _score_pb(float(pb)) if pb is not None else None

    de = candidate.get("debtToEquity") or candidate.get("ltDebtToEquity")
    # Yahoo sometimes reports D/E as a percentage (e.g., 45 = 45%), normalize
    if de is not None:
        de_val = float(de)
        if de_val > 5:  # Likely percentage form
            de_val = de_val / 100.0
        scores["de"] = _score_de(de_val)
    else:
        scores["de"] = None

    roe = candidate.get("returnOnEquity") or candidate.get("roe")
    if roe is not None:
        roe_val = float(roe)
        # Normalize: if already a fraction (e.g. 0.15), convert to percent
        if -1 < roe_val < 1:
            roe_val = roe_val * 100.0
        scores["roe"] = _score_roe(roe_val)
    else:
        scores["roe"] = None

    fcf = candidate.get("leveredFreeCashflow") or candidate.get("freeCashflow")
    mcap = candidate.get("marketCap") or candidate.get("intradayMarketCap")
    if fcf is not None and mcap is not None:
        scores["fcf_yield"] = _score_fcf_yield(float(fcf), float(mcap))
    else:
        scores["fcf_yield"] = None

    # Weighted sum, redistributing weight from missing fields
    total_weight = 0.0
    weighted_sum = 0.0
    for key, weight in SCORE_WEIGHTS.items():
        val = scores.get(key)
        if val is not None:
            total_weight += weight
            weighted_sum += weight * val

    if total_weight == 0:
        return 0.0
    return weighted_sum / total_weight


def rank_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score and sort candidates by composite gate-proximity score (best first).

    Adds ``_composite_score`` key to each candidate dict.
    """
    for c in candidates:
        c["_composite_score"] = score_candidate(c)
    return sorted(candidates, key=lambda c: c["_composite_score"], reverse=True)


# ---------------------------------------------------------------------------
# SmartCandidateGenerator (replaces CandidateGenerator)
# ---------------------------------------------------------------------------


class SmartCandidateGenerator:
    """Generates ranked ticker candidates using the Yahoo Finance screener.

    On first call to ``generate_batch``, runs Tier 1 (screener) and Tier 2 (scoring)
    to build a pre-ranked candidate list. Subsequent calls return the next batch
    from that list.
    """

    def __init__(
        self,
        evaluated: set[str] | None = None,
        max_screener_results: int = 500,
    ) -> None:
        self._evaluated = evaluated or set()
        self._max_screener_results = max_screener_results
        self._ranked: list[dict[str, Any]] | None = None
        self._index = 0
        self.screener_count = 0  # Number of Tier 1 results (for progress display)

    def _initialize(self) -> None:
        """Run Tier 1 + Tier 2 to build the ranked candidate list."""
        _log.info("Running Yahoo Finance screener (Tier 1)")
        raw = fetch_screener_candidates(self._max_screener_results)
        self.screener_count = len(raw)
        _log.info("Screener returned %d candidates", self.screener_count)

        # If screener returns nothing, fall back to predefined screener
        if not raw:
            _log.warning("Screener returned 0 results, trying undervalued_large_caps fallback")
            try:
                resp = yf.screen("undervalued_large_caps", count=250)
                raw = resp.get("quotes", []) if isinstance(resp, dict) else []
                self.screener_count = len(raw)
            except Exception:
                _log.warning("Fallback screener also failed")
                raw = []

        # Filter out already-evaluated tickers
        filtered = [c for c in raw if c.get("symbol") and c["symbol"] not in self._evaluated]

        _log.info("Ranking %d candidates (Tier 2)", len(filtered))
        self._ranked = rank_candidates(filtered)

    def generate_batch(self, batch_size: int = 10) -> list[str]:
        """Return the next batch of ticker symbols from the ranked list."""
        if self._ranked is None:
            self._initialize()

        assert self._ranked is not None
        start = self._index
        end = start + batch_size
        batch = [c["symbol"] for c in self._ranked[start:end] if c.get("symbol")]
        self._index = end
        return batch

    def get_cik(self, ticker: str) -> str | None:
        """Look up the CIK for a ticker from the SEC universe."""
        universe = load_sec_universe()
        return universe.get(ticker)


# ---------------------------------------------------------------------------
# Kept unchanged: SEC universe, pre-screening, ingestion
# ---------------------------------------------------------------------------


def load_sec_universe(user_agent: str | None = None) -> dict[str, str]:
    """Load the full SEC ticker-to-CIK universe, caching after first call."""
    global _universe_cache  # noqa: PLW0603
    if _universe_cache is None:
        if user_agent is None:
            user_agent = get_config().get_sec_user_agent()
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
