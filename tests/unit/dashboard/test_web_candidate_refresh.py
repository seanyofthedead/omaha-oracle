"""Tests for live Firecrawl scraping integrated into the search pipeline.

When 'Include web-sourced candidates' is checked, the search should trigger
a live Firecrawl scrape to populate the web-candidates DynamoDB table,
then read those candidates into the scoring pool.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ingestion.web_sources.models import WebCandidate


TABLE_NAME = "omaha-oracle-dev-web-candidates"


@pytest.fixture()
def firecrawl_env(monkeypatch):
    """Set Firecrawl API key for tests."""
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-fc-key")


@pytest.fixture()
def web_candidates_table(aws_env, firecrawl_env, _moto_session):
    """Create the web-candidates DynamoDB table in moto."""
    table = _moto_session.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {"AttributeName": "ticker", "KeyType": "HASH"},
            {"AttributeName": "source_key", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "ticker", "AttributeType": "S"},
            {"AttributeName": "source_key", "AttributeType": "S"},
            {"AttributeName": "status", "AttributeType": "S"},
            {"AttributeName": "composite_score", "AttributeType": "N"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "status-score-index",
                "KeySchema": [
                    {"AttributeName": "status", "KeyType": "HASH"},
                    {"AttributeName": "composite_score", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    yield table
    table.delete()


def _fake_web_candidates(source: str = "finviz_value") -> list[WebCandidate]:
    return [
        WebCandidate(ticker="BRK.B", source=source, signal_type="value_screen", confidence=0.7),
        WebCandidate(ticker="JNJ", source=source, signal_type="value_screen", confidence=0.6),
        WebCandidate(ticker="PG", source=source, signal_type="insider_buy", confidence=0.8),
    ]


class TestRefreshWebCandidates:
    """Test the refresh_web_candidates function that triggers live scraping."""

    @patch("ingestion.web_sources.sources.build_default_registry")
    @patch("shared.firecrawl_client.FirecrawlApp")
    def test_scrapes_sources_and_stores_candidates(
        self, mock_fc_app_cls, mock_registry_fn, web_candidates_table
    ):
        """Scraping should call enabled sources and store results in DynamoDB."""
        from dashboard.candidate_generator import refresh_web_candidates

        mock_source = MagicMock()
        mock_source.name = "test_source"
        mock_source.enabled = True
        mock_source.frequency = "daily"
        mock_source.scrape.return_value = _fake_web_candidates("test_source")

        mock_registry = MagicMock()
        mock_registry.get_enabled.return_value = [mock_source]
        mock_registry_fn.return_value = mock_registry

        stored = refresh_web_candidates()

        assert stored == 3
        mock_source.scrape.assert_called_once()

    @patch("ingestion.web_sources.sources.build_default_registry")
    @patch("shared.firecrawl_client.FirecrawlApp")
    def test_returns_zero_when_no_sources(
        self, mock_fc_app_cls, mock_registry_fn, web_candidates_table
    ):
        """Should return 0 when no enabled sources exist."""
        from dashboard.candidate_generator import refresh_web_candidates

        mock_registry = MagicMock()
        mock_registry.get_enabled.return_value = []
        mock_registry_fn.return_value = mock_registry

        stored = refresh_web_candidates()
        assert stored == 0

    @patch("ingestion.web_sources.sources.build_default_registry")
    @patch("shared.firecrawl_client.FirecrawlApp")
    def test_handles_source_failure_gracefully(
        self, mock_fc_app_cls, mock_registry_fn, web_candidates_table
    ):
        """A failing source should not crash; other sources still contribute."""
        from dashboard.candidate_generator import refresh_web_candidates

        ok_source = MagicMock()
        ok_source.name = "ok_source"
        ok_source.enabled = True
        ok_source.scrape.return_value = _fake_web_candidates("ok_source")

        fail_source = MagicMock()
        fail_source.name = "fail_source"
        fail_source.enabled = True
        fail_source.scrape.side_effect = RuntimeError("scrape failed")

        mock_registry = MagicMock()
        mock_registry.get_enabled.return_value = [ok_source, fail_source]
        mock_registry_fn.return_value = mock_registry

        stored = refresh_web_candidates()
        assert stored == 3  # Only ok_source candidates stored

    @patch("ingestion.web_sources.sources.build_default_registry")
    @patch("shared.firecrawl_client.FirecrawlApp")
    def test_candidates_readable_after_refresh(
        self, mock_fc_app_cls, mock_registry_fn, web_candidates_table
    ):
        """After refresh, _fetch_web_candidates should return the stored data."""
        from dashboard.candidate_generator import _fetch_web_candidates, refresh_web_candidates

        mock_source = MagicMock()
        mock_source.name = "test_source"
        mock_source.enabled = True
        mock_source.scrape.return_value = _fake_web_candidates("test_source")

        mock_registry = MagicMock()
        mock_registry.get_enabled.return_value = [mock_source]
        mock_registry_fn.return_value = mock_registry

        refresh_web_candidates()

        web_dicts = _fetch_web_candidates()
        tickers = {d["symbol"] for d in web_dicts}
        assert "BRK.B" in tickers
        assert "JNJ" in tickers
        assert "PG" in tickers


class TestSmartCandidateGeneratorWebIntegration:
    """Test that SmartCandidateGenerator calls refresh when web sources enabled."""

    @patch("dashboard.candidate_generator.refresh_web_candidates")
    @patch("dashboard.candidate_generator.fetch_screener_candidates")
    def test_calls_refresh_when_web_sources_enabled(
        self, mock_screener, mock_refresh
    ):
        """Generator should call refresh_web_candidates during initialization."""
        from dashboard.candidate_generator import SmartCandidateGenerator

        mock_screener.return_value = [{"symbol": "AAPL"}]
        mock_refresh.return_value = 5

        gen = SmartCandidateGenerator(include_web_sources=True)
        gen.generate_batch(10)

        mock_refresh.assert_called_once()

    @patch("dashboard.candidate_generator.refresh_web_candidates")
    @patch("dashboard.candidate_generator.fetch_screener_candidates")
    def test_skips_refresh_when_web_sources_disabled(
        self, mock_screener, mock_refresh
    ):
        """Generator should NOT call refresh when web sources are disabled."""
        from dashboard.candidate_generator import SmartCandidateGenerator

        mock_screener.return_value = [{"symbol": "AAPL"}]

        gen = SmartCandidateGenerator(include_web_sources=False)
        gen.generate_batch(10)

        mock_refresh.assert_not_called()
