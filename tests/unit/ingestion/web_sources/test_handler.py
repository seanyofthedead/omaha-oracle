"""Tests for the web scraping orchestrator Lambda handler."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ingestion.web_sources.handler import handler
from ingestion.web_sources.models import WebCandidate

TABLE_NAME = "omaha-oracle-dev-web-candidates"


@pytest.fixture()
def web_env(aws_env, monkeypatch):
    """Set up env for handler tests."""
    monkeypatch.setenv("TABLE_WEB_CANDIDATES", TABLE_NAME)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-fc-key")


@pytest.fixture()
def web_table(web_env, _moto_session):
    """Create DynamoDB table for handler tests."""
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
    yield
    table.delete()


def _fake_candidates(source_name: str) -> list[WebCandidate]:
    return [
        WebCandidate(
            ticker="AAPL",
            source=source_name,
            signal_type="value_screen",
            confidence=0.7,
        ),
        WebCandidate(
            ticker="GOOG",
            source=source_name,
            signal_type="value_screen",
            confidence=0.6,
        ),
    ]


class TestHandler:
    @patch("ingestion.web_sources.handler.build_default_registry")
    @patch("ingestion.web_sources.handler.FirecrawlClient")
    def test_handler_daily_run(self, mock_fc_cls, mock_registry_fn, web_table):
        # Set up a mock registry with one source
        mock_source = MagicMock()
        mock_source.name = "test_source"
        mock_source.enabled = True
        mock_source.frequency = "daily"
        mock_source.scrape.return_value = _fake_candidates("test_source")

        mock_registry = MagicMock()
        mock_registry.get_enabled.return_value = [mock_source]
        mock_registry_fn.return_value = mock_registry

        mock_client = MagicMock()
        mock_client.total_credits_used = 1
        mock_fc_cls.return_value = mock_client

        result = handler({"frequency": "daily"}, None)

        assert result["status"] == "ok"
        assert result["raw_candidates"] == 2
        assert result["unique_tickers"] == 2
        assert result["sources_scraped"] == 1
        assert result["sources_failed"] == 0

    @patch("ingestion.web_sources.handler.build_default_registry")
    @patch("ingestion.web_sources.handler.FirecrawlClient")
    def test_handler_partial_failure(self, mock_fc_cls, mock_registry_fn, web_table):
        ok_source = MagicMock()
        ok_source.name = "ok_source"
        ok_source.enabled = True
        ok_source.frequency = "daily"
        ok_source.scrape.return_value = _fake_candidates("ok_source")

        fail_source = MagicMock()
        fail_source.name = "fail_source"
        fail_source.enabled = True
        fail_source.frequency = "daily"
        fail_source.scrape.side_effect = RuntimeError("boom")

        mock_registry = MagicMock()
        mock_registry.get_enabled.return_value = [ok_source, fail_source]
        mock_registry_fn.return_value = mock_registry

        mock_client = MagicMock()
        mock_client.total_credits_used = 1
        mock_fc_cls.return_value = mock_client

        result = handler({"frequency": "daily"}, None)

        assert result["status"] == "partial"
        assert result["sources_failed"] == 1
        assert result["raw_candidates"] == 2  # Only from ok_source

    @patch("ingestion.web_sources.handler.build_default_registry")
    @patch("ingestion.web_sources.handler.FirecrawlClient")
    def test_handler_no_sources(self, mock_fc_cls, mock_registry_fn, web_table):
        mock_registry = MagicMock()
        mock_registry.get_enabled.return_value = []
        mock_registry_fn.return_value = mock_registry

        result = handler({"frequency": "daily"}, None)
        assert result["status"] == "ok"
        assert result["processed"] == 0

    @patch("ingestion.web_sources.handler.build_default_registry")
    @patch("ingestion.web_sources.handler.FirecrawlClient")
    def test_manual_action_runs_all(self, mock_fc_cls, mock_registry_fn, web_table):
        mock_registry = MagicMock()
        mock_registry.get_enabled.return_value = []
        mock_registry_fn.return_value = mock_registry

        mock_client = MagicMock()
        mock_client.total_credits_used = 0
        mock_fc_cls.return_value = mock_client

        handler({"action": "manual"}, None)

        # When action=manual, frequency should be None (all sources)
        mock_registry.get_enabled.assert_called_with(frequency=None)
