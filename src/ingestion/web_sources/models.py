"""Normalised data models for web-sourced stock candidates.

Every scraper source must produce ``WebCandidate`` records conforming to this
schema before entering the pipeline.  The ``AggregatedCandidate`` model is the
output of the deduplication / multi-signal scoring layer.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

# 1-5 uppercase letters, optionally followed by a dot and 1-2 letters (BRK.B)
_TICKER_RE = re.compile(r"^[A-Z]{1,5}(?:\.[A-Z]{1,2})?$")


class WebCandidate(BaseModel):
    """A single candidate discovered from a web source."""

    ticker: str
    source: str  # e.g. "finviz_value", "dataroma_gurus"
    signal_type: str  # e.g. "value_screen", "insider_buy", "analyst_upgrade"
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    # Optional enrichment — populated when available from the source
    sector: str | None = None
    market_cap: float | None = None
    pe_ratio: float | None = None
    price: float | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ticker", mode="before")
    @classmethod
    def _normalise_ticker(cls, v: str) -> str:
        v = v.strip().upper().replace(" ", "")
        if not _TICKER_RE.match(v):
            raise ValueError(f"Invalid ticker format: {v!r}")
        return v

    def to_screener_dict(self) -> dict[str, Any]:
        """Convert to a dict compatible with ``SmartCandidateGenerator``."""
        d: dict[str, Any] = {
            "symbol": self.ticker,
            "_source": self.source,
            "_signal_type": self.signal_type,
            "_web_confidence": self.confidence,
        }
        if self.market_cap is not None:
            d["marketCap"] = self.market_cap
        if self.pe_ratio is not None:
            d["trailingPE"] = self.pe_ratio
        if self.price is not None:
            d["currentPrice"] = self.price
        if self.sector is not None:
            d["sector"] = self.sector
        return d


class AggregatedCandidate(BaseModel):
    """A ticker with merged signals from multiple web sources."""

    ticker: str
    sources: list[str]
    signal_types: list[str]
    source_count: int = 0
    composite_score: float = Field(default=0.0, ge=0.0, le=1.0)
    first_seen: datetime = Field(default_factory=lambda: datetime.now(UTC))
    candidates: list[WebCandidate] = Field(default_factory=list)

    # Best-known enrichment across all sources
    sector: str | None = None
    market_cap: float | None = None
    pe_ratio: float | None = None
    price: float | None = None

    def to_screener_dict(self) -> dict[str, Any]:
        """Convert to a dict compatible with ``SmartCandidateGenerator``."""
        d: dict[str, Any] = {
            "symbol": self.ticker,
            "_composite_score": self.composite_score,
            "_source_count": self.source_count,
            "_sources": self.sources,
            "_signal_types": self.signal_types,
        }
        if self.market_cap is not None:
            d["marketCap"] = self.market_cap
        if self.pe_ratio is not None:
            d["trailingPE"] = self.pe_ratio
        if self.price is not None:
            d["currentPrice"] = self.price
        if self.sector is not None:
            d["sector"] = self.sector
        return d
