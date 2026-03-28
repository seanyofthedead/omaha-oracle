"""Tests for web candidate data models."""

from __future__ import annotations

import pytest

from ingestion.web_sources.models import AggregatedCandidate, WebCandidate


class TestWebCandidate:
    def test_valid_ticker(self):
        c = WebCandidate(ticker="AAPL", source="test", signal_type="value_screen")
        assert c.ticker == "AAPL"

    def test_ticker_normalised(self):
        c = WebCandidate(ticker="  aapl  ", source="test", signal_type="value_screen")
        assert c.ticker == "AAPL"

    def test_dot_ticker(self):
        c = WebCandidate(ticker="BRK.B", source="test", signal_type="value_screen")
        assert c.ticker == "BRK.B"

    def test_invalid_ticker_raises(self):
        with pytest.raises(ValueError, match="Invalid ticker"):
            WebCandidate(ticker="123BAD!", source="test", signal_type="value_screen")

    def test_empty_ticker_raises(self):
        with pytest.raises(ValueError):
            WebCandidate(ticker="", source="test", signal_type="value_screen")

    def test_confidence_clamped(self):
        with pytest.raises(ValueError):
            WebCandidate(ticker="AAPL", source="test", signal_type="x", confidence=1.5)

    def test_confidence_negative_rejected(self):
        with pytest.raises(ValueError):
            WebCandidate(ticker="AAPL", source="test", signal_type="x", confidence=-0.1)

    def test_defaults(self):
        c = WebCandidate(ticker="MSFT", source="finviz", signal_type="value_screen")
        assert c.confidence == 0.5
        assert c.sector is None
        assert c.market_cap is None
        assert c.extra == {}
        assert c.discovered_at is not None

    def test_to_screener_dict(self):
        c = WebCandidate(
            ticker="GOOG",
            source="finviz_value",
            signal_type="value_screen",
            confidence=0.8,
            market_cap=2e12,
            pe_ratio=12.5,
            price=150.0,
            sector="Technology",
        )
        d = c.to_screener_dict()
        assert d["symbol"] == "GOOG"
        assert d["marketCap"] == 2e12
        assert d["trailingPE"] == 12.5
        assert d["currentPrice"] == 150.0
        assert d["sector"] == "Technology"
        assert d["_source"] == "finviz_value"
        assert d["_web_confidence"] == 0.8

    def test_to_screener_dict_minimal(self):
        c = WebCandidate(ticker="X", source="test", signal_type="x")
        d = c.to_screener_dict()
        assert d["symbol"] == "X"
        assert "marketCap" not in d
        assert "trailingPE" not in d


class TestAggregatedCandidate:
    def test_to_screener_dict(self):
        ac = AggregatedCandidate(
            ticker="AAPL",
            sources=["finviz_value", "sec_fulltext"],
            signal_types=["value_screen", "filing_mention"],
            source_count=2,
            composite_score=0.85,
            market_cap=3e12,
        )
        d = ac.to_screener_dict()
        assert d["symbol"] == "AAPL"
        assert d["_composite_score"] == 0.85
        assert d["_source_count"] == 2
        assert d["marketCap"] == 3e12

    def test_defaults(self):
        ac = AggregatedCandidate(
            ticker="T",
            sources=["test"],
            signal_types=["test"],
        )
        assert ac.source_count == 0
        assert ac.composite_score == 0.0
        assert ac.candidates == []
