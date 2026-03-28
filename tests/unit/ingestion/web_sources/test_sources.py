"""Tests for source definitions and the default registry."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ingestion.web_sources.sources import (
    BarchartVolumeLeaders,
    FinvizInsiderBuying,
    FinvizOversold,
    FinvizValueScreener,
    SEC13FFilings,
    SECEdgarFullText,
    StockAnalysisInsider,
    build_default_registry,
)


class TestDefaultRegistry:
    def test_all_12_sources_registered(self):
        registry = build_default_registry()
        assert len(registry) == 12

    def test_daily_sources(self):
        registry = build_default_registry()
        daily = registry.get_enabled(frequency="daily")
        daily_names = {s.name for s in daily}
        assert "finviz_value" in daily_names
        assert "finviz_insider" in daily_names
        assert "finviz_analyst" in daily_names
        assert "barchart_volume" in daily_names
        assert "finviz_oversold" in daily_names
        assert "sec_fulltext" in daily_names
        assert "stockanalysis_insider" in daily_names

    def test_weekly_sources(self):
        registry = build_default_registry()
        weekly = registry.get_enabled(frequency="weekly")
        weekly_names = {s.name for s in weekly}
        assert "dataroma_gurus" in weekly_names
        assert "finviz_dividend" in weekly_names
        assert "sec_13f" in weekly_names
        assert "yahoo_earnings" in weekly_names
        assert "finviz_growth" in weekly_names


class TestSourceAttributes:
    def test_finviz_value_uses_stealth_proxy(self):
        src = FinvizValueScreener()
        assert src.proxy == "stealth"
        assert src.signal_type == "value_screen"

    def test_insider_sources_have_insider_signal(self):
        assert FinvizInsiderBuying().signal_type == "insider_buy"
        assert StockAnalysisInsider().signal_type == "insider_buy"

    def test_sec_sources_have_user_agent(self):
        assert SECEdgarFullText().headers is not None
        assert "User-Agent" in SECEdgarFullText().headers
        assert SEC13FFilings().headers is not None

    def test_barchart_uses_wait_for(self):
        src = BarchartVolumeLeaders()
        assert src.wait_for == 5000

    def test_finviz_oversold_uses_technical_view(self):
        src = FinvizOversold()
        assert "v=171" in src.url

    def test_all_sources_have_extraction_prompt(self):
        registry = build_default_registry()
        for source in registry.get_all():
            assert source.extraction_prompt, f"{source.name} missing extraction_prompt"


class TestSourceScrape:
    @patch("ingestion.web_sources.base.FirecrawlClient")
    def test_schema_source_parses_json_response(self, mock_client_cls):
        """Verify SchemaWebSource.scrape converts extracted JSON to WebCandidates."""
        mock_client = MagicMock()
        mock_client.scrape_extract.return_value = {
            "json": {
                "stocks": [
                    {"ticker": "AAPL", "company_name": "Apple", "pe_ratio": 12.5},
                    {"ticker": "MSFT", "company_name": "Microsoft", "price": 350.0},
                    {"ticker": "", "company_name": "Empty"},  # Should be skipped
                ]
            }
        }

        src = FinvizValueScreener()
        candidates = src.scrape(mock_client)

        assert len(candidates) == 2
        assert candidates[0].ticker == "AAPL"
        assert candidates[0].source == "finviz_value"
        assert candidates[0].pe_ratio == 12.5
        assert candidates[1].ticker == "MSFT"
        assert candidates[1].price == 350.0

    @patch("ingestion.web_sources.base.FirecrawlClient")
    def test_handles_empty_response(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.scrape_extract.return_value = {"json": {}}

        src = FinvizValueScreener()
        candidates = src.scrape(mock_client)
        assert candidates == []

    @patch("ingestion.web_sources.base.FirecrawlClient")
    def test_handles_extract_key(self, mock_client_cls):
        """Firecrawl may return data under 'extract' instead of 'json'."""
        mock_client = MagicMock()
        mock_client.scrape_extract.return_value = {"extract": {"stocks": [{"ticker": "GOOG"}]}}

        src = FinvizValueScreener()
        candidates = src.scrape(mock_client)
        assert len(candidates) == 1
        assert candidates[0].ticker == "GOOG"

    @patch("ingestion.web_sources.base.FirecrawlClient")
    def test_invalid_ticker_skipped(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.scrape_extract.return_value = {"json": {"stocks": [{"ticker": "123INVALID!"}]}}

        src = FinvizValueScreener()
        candidates = src.scrape(mock_client)
        assert candidates == []
