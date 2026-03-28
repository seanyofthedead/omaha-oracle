"""Base class and registry for web scraping sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ingestion.web_sources.models import WebCandidate
from shared.firecrawl_client import FirecrawlClient


class BaseWebSource(ABC):
    """Contract that every web scraping source must implement.

    Subclasses define *what* to scrape and *how* to parse it.  The registry
    and orchestrator handle scheduling, error isolation, and storage.
    """

    name: str  # Unique identifier, e.g. "finviz_value"
    signal_type: str  # e.g. "value_screen", "insider_buy"
    frequency: str  # "daily" | "weekly"
    enabled: bool = True

    @abstractmethod
    def scrape(self, client: FirecrawlClient) -> list[WebCandidate]:
        """Scrape the source and return normalised candidates."""

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r} enabled={self.enabled}>"


class SchemaWebSource(BaseWebSource):
    """Config-driven source that uses Firecrawl LLM extraction.

    Subclasses only need to set class attributes and optionally override
    ``post_process`` to filter or enrich extracted records.
    """

    url: str  # Entry-point URL to scrape
    extraction_prompt: str = ""  # Prompt for Firecrawl LLM extraction
    wait_for: int | None = None  # ms to wait for JS rendering
    proxy: str | None = None  # "basic" | "stealth" | "enhanced"
    headers: dict[str, str] | None = None
    default_confidence: float = 0.5

    def extraction_schema(self) -> dict[str, Any]:
        """Return a JSON schema for Firecrawl extraction.

        Default schema extracts a list of stock records.  Override to
        customise per source.
        """
        return {
            "type": "object",
            "properties": {
                "stocks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ticker": {"type": "string"},
                            "company_name": {"type": "string"},
                            "sector": {"type": "string"},
                            "market_cap": {"type": "number"},
                            "pe_ratio": {"type": "number"},
                            "price": {"type": "number"},
                        },
                        "required": ["ticker"],
                    },
                }
            },
            "required": ["stocks"],
        }

    def post_process(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Optional hook to filter/enrich extracted records before conversion."""
        return records

    def scrape(self, client: FirecrawlClient) -> list[WebCandidate]:
        """Scrape via Firecrawl LLM extraction and convert to WebCandidates."""
        result = client.scrape_extract(
            url=self.url,
            schema=self.extraction_schema(),
            prompt=self.extraction_prompt,
            wait_for=self.wait_for,
            headers=self.headers,
            proxy=self.proxy,
        )

        raw_json = result.get("json") or result.get("extract") or {}
        stocks = raw_json.get("stocks", []) if isinstance(raw_json, dict) else []
        stocks = self.post_process(stocks)

        candidates: list[WebCandidate] = []
        for rec in stocks:
            raw_ticker = rec.get("ticker")
            if not isinstance(raw_ticker, str) or not raw_ticker.strip():
                continue
            ticker = raw_ticker.strip().upper()
            try:
                candidates.append(
                    WebCandidate(
                        ticker=ticker,
                        source=self.name,
                        signal_type=self.signal_type,
                        confidence=self.default_confidence,
                        sector=rec.get("sector"),
                        market_cap=rec.get("market_cap"),
                        pe_ratio=rec.get("pe_ratio"),
                        price=rec.get("price"),
                        extra={
                            k: v
                            for k, v in rec.items()
                            if k not in {"ticker", "sector", "market_cap", "pe_ratio", "price"}
                        },
                    )
                )
            except (ValueError, TypeError):
                continue  # Invalid ticker format — skip silently

        return candidates
