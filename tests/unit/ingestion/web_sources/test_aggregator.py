"""Tests for candidate aggregation, scoring, and quality filtering."""

from __future__ import annotations

from ingestion.web_sources.aggregator import CandidateAggregator
from ingestion.web_sources.models import WebCandidate


def _candidate(
    ticker: str = "AAPL",
    source: str = "src1",
    signal_type: str = "value_screen",
    confidence: float = 0.5,
    market_cap: float | None = None,
    price: float | None = None,
) -> WebCandidate:
    return WebCandidate(
        ticker=ticker,
        source=source,
        signal_type=signal_type,
        confidence=confidence,
        market_cap=market_cap,
        price=price,
    )


class TestAggregate:
    def test_single_candidate(self):
        agg = CandidateAggregator()
        result = agg.aggregate([_candidate()])
        assert len(result) == 1
        assert result[0].ticker == "AAPL"
        assert result[0].source_count == 1

    def test_dedup_same_ticker(self):
        agg = CandidateAggregator()
        result = agg.aggregate(
            [
                _candidate(source="src1"),
                _candidate(source="src2"),
            ]
        )
        assert len(result) == 1
        assert result[0].source_count == 2
        assert set(result[0].sources) == {"src1", "src2"}

    def test_different_tickers_kept_separate(self):
        agg = CandidateAggregator()
        result = agg.aggregate(
            [
                _candidate(ticker="AAPL"),
                _candidate(ticker="GOOG"),
            ]
        )
        assert len(result) == 2

    def test_signal_types_merged(self):
        agg = CandidateAggregator()
        result = agg.aggregate(
            [
                _candidate(signal_type="value_screen"),
                _candidate(signal_type="insider_buy", source="src2"),
            ]
        )
        assert len(result) == 1
        assert set(result[0].signal_types) == {"value_screen", "insider_buy"}


class TestScoring:
    def test_single_source_score_equals_confidence(self):
        agg = CandidateAggregator()
        result = agg.aggregate([_candidate(confidence=0.6)])
        assert abs(result[0].composite_score - 0.6) < 0.01

    def test_multi_source_bonus(self):
        agg = CandidateAggregator()
        single = agg.aggregate([_candidate(confidence=0.5)])
        multi = agg.aggregate(
            [
                _candidate(confidence=0.5, source="src1"),
                _candidate(confidence=0.5, source="src2"),
            ]
        )
        assert multi[0].composite_score > single[0].composite_score

    def test_signal_diversity_bonus(self):
        agg = CandidateAggregator()
        same = agg.aggregate(
            [
                _candidate(source="s1", signal_type="value_screen"),
                _candidate(source="s2", signal_type="value_screen"),
            ]
        )
        diverse = agg.aggregate(
            [
                _candidate(source="s1", signal_type="value_screen"),
                _candidate(source="s2", signal_type="insider_buy"),
            ]
        )
        assert diverse[0].composite_score > same[0].composite_score

    def test_score_capped_at_1(self):
        agg = CandidateAggregator()
        # 6 sources with high confidence should still cap at 1.0
        candidates = [
            _candidate(confidence=0.9, source=f"s{i}", signal_type=f"t{i}") for i in range(6)
        ]
        result = agg.aggregate(candidates)
        assert result[0].composite_score <= 1.0


class TestQualityFilter:
    def test_filters_penny_stocks(self):
        agg = CandidateAggregator()
        candidates = agg.aggregate([_candidate(price=0.50)])
        filtered = agg.filter_quality(candidates)
        assert len(filtered) == 0

    def test_filters_low_market_cap(self):
        agg = CandidateAggregator()
        candidates = agg.aggregate([_candidate(market_cap=50_000_000)])
        filtered = agg.filter_quality(candidates)
        assert len(filtered) == 0

    def test_filters_otc_tickers(self):
        agg = CandidateAggregator()
        candidates = agg.aggregate([_candidate(ticker="TOYOF")])
        filtered = agg.filter_quality(candidates)
        assert len(filtered) == 0

    def test_keeps_valid_candidates(self):
        agg = CandidateAggregator()
        candidates = agg.aggregate(
            [
                _candidate(market_cap=5e9, price=50.0),
            ]
        )
        filtered = agg.filter_quality(candidates)
        assert len(filtered) == 1

    def test_keeps_unknown_market_cap(self):
        """Candidates without market_cap data should not be filtered."""
        agg = CandidateAggregator()
        candidates = agg.aggregate([_candidate()])
        filtered = agg.filter_quality(candidates)
        assert len(filtered) == 1


class TestRank:
    def test_rank_descending(self):
        agg = CandidateAggregator()
        candidates = agg.aggregate(
            [
                _candidate(ticker="LOW", confidence=0.3),
                _candidate(ticker="HIGH", confidence=0.9),
            ]
        )
        ranked = agg.rank(candidates)
        assert ranked[0].ticker == "HIGH"
        assert ranked[1].ticker == "LOW"

    def test_rank_stable_on_tie(self):
        """Same score → alphabetical by ticker."""
        agg = CandidateAggregator()
        candidates = agg.aggregate(
            [
                _candidate(ticker="ZZZ", confidence=0.5),
                _candidate(ticker="AAA", confidence=0.5),
            ]
        )
        ranked = agg.rank(candidates)
        assert ranked[0].ticker == "AAA"
        assert ranked[1].ticker == "ZZZ"


class TestProcess:
    def test_full_pipeline(self):
        agg = CandidateAggregator()
        result = agg.process(
            [
                _candidate(ticker="GOOD", confidence=0.7, market_cap=5e9, price=50),
                _candidate(ticker="GOOD", confidence=0.8, source="s2", signal_type="insider_buy"),
                _candidate(ticker="PENNY", confidence=0.9, price=0.10),
                _candidate(ticker="ABCDE", confidence=0.9),  # 5-char, no OTC suffix → kept
            ]
        )
        tickers = [c.ticker for c in result]
        assert "GOOD" in tickers
        assert "PENNY" not in tickers
