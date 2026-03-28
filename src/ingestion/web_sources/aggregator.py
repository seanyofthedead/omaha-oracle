"""Candidate aggregation — dedup, multi-signal scoring, quality filtering.

Collects ``WebCandidate`` records from all sources, deduplicates by ticker,
scores by signal convergence, and filters out low-quality tickers.
"""

from __future__ import annotations

from collections import defaultdict

from shared.logger import get_logger

from .models import AggregatedCandidate, WebCandidate

_log = get_logger(__name__)

# Quality filters
_MIN_MARKET_CAP = 100_000_000  # $100M — broader than pipeline's $1B gate
_SIGNAL_DIVERSITY_BONUS = 0.10  # per unique signal type (capped)
_MULTI_SOURCE_BONUS = 0.15  # per additional source (capped)
_MAX_DIVERSITY_BONUS = 0.40
_MAX_SOURCE_BONUS = 0.45

# OTC / penny stock patterns (tickers ending in common OTC suffixes)
_OTC_SUFFIXES = {"F", "Y", "Q"}  # Foreign ADR, ADR, Bankruptcy


class CandidateAggregator:
    """Deduplicates candidates by ticker and scores by signal convergence."""

    def aggregate(self, candidates: list[WebCandidate]) -> list[AggregatedCandidate]:
        """Group candidates by ticker, merge signals, compute score."""
        by_ticker: dict[str, list[WebCandidate]] = defaultdict(list)
        for c in candidates:
            by_ticker[c.ticker].append(c)

        aggregated: list[AggregatedCandidate] = []
        for ticker, group in by_ticker.items():
            sources = list({c.source for c in group})
            signal_types = list({c.signal_type for c in group})

            # Best-known enrichment: take first non-None across all records
            sector = next((c.sector for c in group if c.sector), None)
            market_cap = next((c.market_cap for c in group if c.market_cap is not None), None)
            pe_ratio = next((c.pe_ratio for c in group if c.pe_ratio is not None), None)
            price = next((c.price for c in group if c.price is not None), None)
            first_seen = min(c.discovered_at for c in group)

            composite = self._score(group, sources, signal_types)

            aggregated.append(
                AggregatedCandidate(
                    ticker=ticker,
                    sources=sources,
                    signal_types=signal_types,
                    source_count=len(sources),
                    composite_score=composite,
                    first_seen=first_seen,
                    candidates=group,
                    sector=sector,
                    market_cap=market_cap,
                    pe_ratio=pe_ratio,
                    price=price,
                )
            )

        return aggregated

    def filter_quality(self, candidates: list[AggregatedCandidate]) -> list[AggregatedCandidate]:
        """Remove penny stocks, OTC, and tickers unlikely to pass the pipeline."""
        filtered: list[AggregatedCandidate] = []
        for c in candidates:
            if self._is_otc(c.ticker):
                continue
            if c.market_cap is not None and c.market_cap < _MIN_MARKET_CAP:
                continue
            if c.price is not None and c.price < 1.0:
                continue
            filtered.append(c)

        removed = len(candidates) - len(filtered)
        if removed:
            _log.info(
                "Quality filter removed candidates",
                extra={"removed": removed, "remaining": len(filtered)},
            )
        return filtered

    def rank(self, candidates: list[AggregatedCandidate]) -> list[AggregatedCandidate]:
        """Rank by composite score descending, then ticker for stability."""
        return sorted(candidates, key=lambda c: (-c.composite_score, c.ticker))

    def process(self, candidates: list[WebCandidate]) -> list[AggregatedCandidate]:
        """Full pipeline: aggregate → filter → rank."""
        aggregated = self.aggregate(candidates)
        filtered = self.filter_quality(aggregated)
        ranked = self.rank(filtered)
        _log.info(
            "Aggregation complete",
            extra={
                "raw_candidates": len(candidates),
                "unique_tickers": len(aggregated),
                "after_filter": len(filtered),
            },
        )
        return ranked

    # ------------------------------------------------------------------ #
    # Scoring                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _score(
        group: list[WebCandidate],
        sources: list[str],
        signal_types: list[str],
    ) -> float:
        """Compute multi-source convergence score.

        Formula:
          base = avg(confidence) across all candidates
          diversity_bonus = min(0.10 * (unique_signal_types - 1), 0.40)
          source_bonus = min(0.15 * (unique_sources - 1), 0.45)
          score = clamp(base + diversity_bonus + source_bonus, 0.0, 1.0)
        """
        base = sum(c.confidence for c in group) / len(group)
        diversity_bonus = min(
            _SIGNAL_DIVERSITY_BONUS * (len(signal_types) - 1), _MAX_DIVERSITY_BONUS
        )
        source_bonus = min(_MULTI_SOURCE_BONUS * (len(sources) - 1), _MAX_SOURCE_BONUS)
        return min(1.0, max(0.0, base + diversity_bonus + source_bonus))

    @staticmethod
    def _is_otc(ticker: str) -> bool:
        """Heuristic OTC detection — tickers ending in F, Y, Q with 5+ chars."""
        if len(ticker) >= 5 and ticker[-1] in _OTC_SUFFIXES:
            return True
        return False
