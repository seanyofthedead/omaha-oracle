"""DynamoDB storage for web-sourced stock candidates.

Handles CRUD operations, TTL management, and ranked retrieval via the
``status-score-index`` GSI.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from boto3.dynamodb.conditions import Key

from shared.config import get_config
from shared.dynamo_client import DynamoClient
from shared.logger import get_logger

from .models import AggregatedCandidate

_log = get_logger(__name__)

_DEFAULT_TTL_DAYS = 7
_SECONDS_PER_DAY = 86_400


def _to_dynamo_item(candidate: AggregatedCandidate) -> dict[str, Any]:
    """Convert an AggregatedCandidate to a DynamoDB-safe item."""
    ttl = int(time.time()) + (_DEFAULT_TTL_DAYS * _SECONDS_PER_DAY)
    sources_str = ",".join(sorted(candidate.sources))
    return {
        "ticker": candidate.ticker,
        "source_key": f"{sources_str}#{candidate.first_seen.isoformat()}",
        "status": "pending",
        "composite_score": Decimal(str(round(candidate.composite_score, 4))),
        "sources": candidate.sources,
        "signal_types": candidate.signal_types,
        "source_count": candidate.source_count,
        "sector": candidate.sector or "",
        "market_cap": Decimal(str(candidate.market_cap)) if candidate.market_cap else None,
        "pe_ratio": Decimal(str(round(candidate.pe_ratio, 2))) if candidate.pe_ratio else None,
        "price": Decimal(str(round(candidate.price, 2))) if candidate.price else None,
        "first_seen": candidate.first_seen.isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "expires_at": ttl,
    }


def _from_dynamo_item(item: dict[str, Any]) -> AggregatedCandidate:
    """Convert a DynamoDB item back to an AggregatedCandidate."""
    return AggregatedCandidate(
        ticker=item["ticker"],
        sources=item.get("sources", []),
        signal_types=item.get("signal_types", []),
        source_count=int(item.get("source_count", 1)),
        composite_score=float(item.get("composite_score", 0)),
        first_seen=(
            datetime.fromisoformat(item["first_seen"])
            if item.get("first_seen")
            else datetime.now(UTC)
        ),
        sector=item.get("sector") or None,
        market_cap=float(item["market_cap"]) if item.get("market_cap") else None,
        pe_ratio=float(item["pe_ratio"]) if item.get("pe_ratio") else None,
        price=float(item["price"]) if item.get("price") else None,
    )


class WebCandidateStore:
    """CRUD for the web-candidates DynamoDB table."""

    def __init__(self, table_name: str | None = None) -> None:
        self._table_name = table_name or get_config().table_web_candidates
        self._client = DynamoClient(self._table_name)

    def store_candidates(
        self, candidates: list[AggregatedCandidate]
    ) -> tuple[int, int]:
        """Upsert aggregated candidates.  Returns (stored_count, failure_count)."""
        stored = 0
        failures: list[str] = []
        for candidate in candidates:
            item = _to_dynamo_item(candidate)
            # Remove None values (DynamoDB doesn't accept None)
            item = {k: v for k, v in item.items() if v is not None}
            try:
                self._client.put_item(item)
                stored += 1
            except Exception:
                failures.append(candidate.ticker)
                _log.warning(
                    "Failed to store web candidate",
                    extra={"ticker": candidate.ticker},
                )
        if failures:
            _log.error(
                "Web candidate store had write failures",
                extra={
                    "failed_count": len(failures),
                    "failed_tickers": failures,
                    "total": len(candidates),
                },
            )
        _log.info(
            "Stored web candidates",
            extra={"stored": stored, "failed": len(failures), "total": len(candidates)},
        )
        return stored, len(failures)

    def get_top_candidates(
        self,
        limit: int = 500,
        min_score: float = 0.0,
        status: str = "pending",
    ) -> list[AggregatedCandidate]:
        """Retrieve top-scored candidates via the GSI.

        Returns candidates sorted by composite_score descending.
        """
        try:
            items = self._client.query(
                key_condition=Key("status").eq(status),
                index_name="status-score-index",
                scan_forward=False,  # Highest score first
                limit=limit,
            )
        except Exception:
            _log.warning("Failed to query web candidates GSI, falling back to scan")
            items = self._client.scan_all()

        candidates = []
        for item in items:
            score = float(item.get("composite_score", 0))
            if score >= min_score:
                candidates.append(_from_dynamo_item(item))
        return candidates

    def mark_evaluated(self, ticker: str, source_key: str) -> None:
        """Mark a candidate as evaluated (removes from pending pool)."""
        try:
            self._client.update_item(
                key={"ticker": ticker, "source_key": source_key},
                update_expression="SET #s = :val, updated_at = :ts",
                expression_attribute_names={"#s": "status"},
                expression_attribute_values={
                    ":val": "evaluated",
                    ":ts": datetime.now(UTC).isoformat(),
                },
            )
        except Exception:
            _log.warning(
                "Failed to mark candidate evaluated",
                extra={"ticker": ticker},
            )

    def get_source_stats(self) -> dict[str, int]:
        """Return count of pending candidates per source (for dashboard)."""
        items = self._client.scan_all()
        stats: dict[str, int] = {}
        for item in items:
            if item.get("status") != "pending":
                continue
            for source in item.get("sources", []):
                stats[source] = stats.get(source, 0) + 1
        return stats

    def get_last_scrape_times(self) -> dict[str, str]:
        """Return the most recent updated_at per source."""
        items = self._client.scan_all()
        latest: dict[str, str] = {}
        for item in items:
            updated = item.get("updated_at", "")
            for source in item.get("sources", []):
                if source not in latest or updated > latest[source]:
                    latest[source] = updated
        return latest
