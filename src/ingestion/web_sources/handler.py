"""Lambda handler for Firecrawl web scraping orchestration.

Triggered by EventBridge on two schedules:
  - Daily 06:00 UTC: ``{"frequency": "daily"}``
  - Weekly Sunday 04:00 UTC: ``{"frequency": "weekly"}``

Follows the same handler pattern as all other ingestion Lambdas:
  - ``handler(event, context) -> dict`` with status/processed/errors
  - ThreadPoolExecutor for concurrent source scraping
  - ``check_failure_threshold()`` for systemic failure detection
  - Structured logging via ``get_logger()``
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from typing import Any

from shared.config import get_config
from shared.converters import check_failure_threshold
from shared.firecrawl_client import FirecrawlClient
from shared.logger import get_logger

from .aggregator import CandidateAggregator
from .base import BaseWebSource
from .models import WebCandidate
from .sources import build_default_registry
from .storage import WebCandidateStore

_log = get_logger(__name__)

_MAX_WORKERS = 3  # Match Firecrawl client concurrency


def _scrape_source(
    source: BaseWebSource,
    client: FirecrawlClient,
) -> tuple[str, list[WebCandidate], str | None]:
    """Scrape a single source.  Returns (name, candidates, error_or_None)."""
    try:
        _log.info("Scraping source", extra={"source": source.name})
        candidates = source.scrape(client)
        _log.info(
            "Source scrape complete",
            extra={"source": source.name, "candidates": len(candidates)},
        )
        return source.name, candidates, None
    except Exception as exc:
        _log.warning(
            "Source scrape failed",
            extra={"source": source.name, "error": str(exc)},
        )
        return source.name, [], str(exc)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Orchestrate web scraping for stock candidate discovery.

    Event schema:
        ``{"frequency": "daily" | "weekly"}`` — which sources to run.
        ``{"action": "manual"}`` — run all enabled sources regardless.
    """
    cfg = get_config()
    frequency = (event.get("frequency") or "").strip().lower()
    action = (event.get("action") or "").strip().lower()

    # Manual trigger runs all enabled sources
    if action == "manual":
        frequency = None

    registry = build_default_registry()
    sources = registry.get_enabled(frequency=frequency if frequency else None)

    if not sources:
        _log.warning("No enabled sources for frequency", extra={"frequency": frequency})
        return {"status": "ok", "processed": 0, "errors": [], "message": "No sources to scrape"}

    _log.info(
        "Starting web scrape run",
        extra={
            "frequency": frequency or "all",
            "sources": [s.name for s in sources],
            "source_count": len(sources),
        },
    )

    # Initialize Firecrawl client
    client = FirecrawlClient(api_key=cfg.get_firecrawl_key())

    # Scrape all sources concurrently
    all_candidates: list[WebCandidate] = []
    errors: list[str] = []
    source_results: dict[str, int] = {}

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
        futures = {
            executor.submit(_scrape_source, source, client): source.name for source in sources
        }
        for future in as_completed(futures):
            source_name = futures[future]
            try:
                name, candidates, error = future.result()
                source_results[name] = len(candidates)
                all_candidates.extend(candidates)
                if error:
                    errors.append(f"{name}: {error}")
            except Exception as exc:
                errors.append(f"{source_name}: {exc}")
                source_results[source_name] = 0

    # Check for systemic failure (>50% sources failed)
    check_failure_threshold(errors, len(sources), "Web scraper")

    # Aggregate, deduplicate, score, filter
    aggregator = CandidateAggregator()
    ranked = aggregator.process(all_candidates)

    # Store to DynamoDB
    store = WebCandidateStore()
    stored = store.store_candidates(ranked)

    summary = {
        "status": "ok" if not errors else "partial",
        "processed": stored,
        "errors": errors[:10],
        "raw_candidates": len(all_candidates),
        "unique_tickers": len(ranked),
        "sources_scraped": len(sources),
        "sources_failed": len(errors),
        "firecrawl_credits": client.total_credits_used,
        "source_breakdown": source_results,
        "timestamp": datetime.now(UTC).isoformat(),
    }

    _log.info("Web scrape run complete", extra=summary)
    return summary
