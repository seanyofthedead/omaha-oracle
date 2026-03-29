"""Tests for the Firecrawl client wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from shared.firecrawl_client import FirecrawlClient


class TestFirecrawlClient:
    @patch("shared.firecrawl_client.FirecrawlApp")
    def test_scrape_returns_normalised_result(self, mock_app_cls):
        mock_app = MagicMock()
        mock_app.scrape.return_value = {
            "markdown": "# Hello",
            "metadata": {"title": "Test"},
        }
        mock_app_cls.return_value = mock_app

        client = FirecrawlClient(api_key="test-key", min_delay_s=0)
        result = client.scrape("https://example.com")

        assert result["url"] == "https://example.com"
        assert result["markdown"] == "# Hello"
        assert "scraped_at" in result
        mock_app.scrape.assert_called_once()

    @patch("shared.firecrawl_client.FirecrawlApp")
    def test_scrape_retries_on_failure(self, mock_app_cls):
        mock_app = MagicMock()
        mock_app.scrape.side_effect = [
            RuntimeError("timeout"),
            {"markdown": "ok"},
        ]
        mock_app_cls.return_value = mock_app

        client = FirecrawlClient(api_key="test-key", max_retries=1, min_delay_s=0)
        result = client.scrape("https://example.com")

        assert result["markdown"] == "ok"
        assert mock_app.scrape.call_count == 2

    @patch("shared.firecrawl_client.FirecrawlApp")
    def test_scrape_raises_after_max_retries(self, mock_app_cls):
        mock_app = MagicMock()
        mock_app.scrape.side_effect = RuntimeError("always fails")
        mock_app_cls.return_value = mock_app

        client = FirecrawlClient(api_key="test-key", max_retries=1, min_delay_s=0)
        import pytest

        with pytest.raises(RuntimeError, match="failed after 2 attempts"):
            client.scrape("https://example.com")

    @patch("shared.firecrawl_client.FirecrawlApp")
    def test_scrape_extract_passes_schema(self, mock_app_cls):
        mock_app = MagicMock()
        mock_app.scrape.return_value = {
            "markdown": "# Data",
            "json": {"stocks": [{"ticker": "AAPL"}]},
        }
        mock_app_cls.return_value = mock_app

        client = FirecrawlClient(api_key="test-key", min_delay_s=0)
        schema = {"type": "object", "properties": {"stocks": {"type": "array"}}}
        result = client.scrape_extract("https://example.com", schema=schema)

        assert result["json"]["stocks"][0]["ticker"] == "AAPL"
        # Verify formats include json schema
        call_args = mock_app.scrape.call_args
        formats = call_args[1].get("formats", call_args[0][1] if len(call_args[0]) > 1 else [])
        assert any(isinstance(f, dict) and f.get("type") == "json" for f in formats)

    @patch("shared.firecrawl_client.FirecrawlApp")
    def test_batch_scrape_skips_failures(self, mock_app_cls):
        mock_app = MagicMock()
        mock_app.scrape.side_effect = [
            {"markdown": "ok1"},
            RuntimeError("fail"),
            {"markdown": "ok3"},
        ]
        mock_app_cls.return_value = mock_app

        client = FirecrawlClient(api_key="test-key", max_retries=0, min_delay_s=0, max_concurrent=3)
        results = client.scrape_batch(["url1", "url2", "url3"])

        assert len(results) == 2  # One failed URL excluded

    @patch("shared.firecrawl_client.FirecrawlApp")
    def test_credits_tracked(self, mock_app_cls):
        mock_app = MagicMock()
        mock_app.scrape.return_value = {"markdown": "ok"}
        mock_app_cls.return_value = mock_app

        client = FirecrawlClient(api_key="test-key", min_delay_s=0)
        assert client.total_credits_used == 0
        client.scrape("https://example.com")
        assert client.total_credits_used == 1
        client.scrape("https://example.com/page2")
        assert client.total_credits_used == 2

    @patch("shared.firecrawl_client.FirecrawlApp")
    def test_normalise_document_object(self, mock_app_cls):
        """Handle Firecrawl returning a Document object instead of dict."""

        class FakeDoc:
            def __init__(self):
                self.markdown = "# Test"
                self.html = "<h1>Test</h1>"

        mock_app = MagicMock()
        mock_app.scrape.return_value = FakeDoc()
        mock_app_cls.return_value = mock_app

        client = FirecrawlClient(api_key="test-key", min_delay_s=0)
        result = client.scrape("https://example.com")

        assert result["url"] == "https://example.com"
        assert result["markdown"] == "# Test"
        assert "scraped_at" in result
