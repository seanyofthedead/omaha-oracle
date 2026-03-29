"""Persistent tracking of evaluated stock tickers.

Records which tickers have been run through the analysis pipeline so that
subsequent searches skip them and surface new candidates instead.  Records
expire after a configurable TTL (default 90 days) so tickers are eventually
re-evaluated.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any

from shared.config import get_config
from shared.dynamo_client import DynamoClient
from shared.logger import get_logger

_log = get_logger(__name__)

_DEFAULT_TTL_DAYS = 90
_SECONDS_PER_DAY = 86_400


class EvaluatedTickerStore:
    """CRUD for the evaluated-tickers DynamoDB table (``table_universe``)."""

    def __init__(self, table_name: str | None = None) -> None:
        self._table_name = table_name or get_config().table_universe
        self._client = DynamoClient(self._table_name)

    def get_evaluated_tickers(self) -> set[str]:
        """Return all non-expired evaluated tickers."""
        items = self._client.scan_all()
        now = int(time.time())
        tickers: set[str] = set()
        for item in items:
            ttl = int(item.get("ttl", 0))
            # DynamoDB TTL deletion is best-effort and may lag; filter here too
            if ttl > now:
                tickers.add(item["ticker"])
        return tickers

    def mark_evaluated(
        self,
        ticker: str,
        passed: bool,
        search_id: str | None = None,
        ttl_days: int = _DEFAULT_TTL_DAYS,
    ) -> bool:
        """Record that *ticker* was evaluated by the pipeline.

        Returns True on success, False on failure.
        """
        now = datetime.now(UTC)
        item: dict[str, Any] = {
            "ticker": ticker,
            "evaluated_at": now.isoformat(),
            "passed_gates": passed,
            "search_id": search_id or str(uuid.uuid4()),
            "ttl": int(time.time()) + (ttl_days * _SECONDS_PER_DAY),
        }
        try:
            self._client.put_item(item)
            return True
        except Exception:
            _log.error(
                "Failed to record evaluated ticker",
                extra={"ticker": ticker},
            )
            return False

    def get_evaluation_count(self) -> int:
        """Return the number of non-expired evaluated tickers."""
        return len(self.get_evaluated_tickers())

    def clear_all(self) -> int:
        """Delete all records.  Returns count deleted."""
        items = self._client.scan_all()
        deleted = 0
        for item in items:
            try:
                self._client.delete_item({"ticker": item["ticker"]})
                deleted += 1
            except Exception:
                _log.warning(
                    "Failed to delete evaluated ticker",
                    extra={"ticker": item.get("ticker")},
                )
        _log.info("Cleared evaluated tickers", extra={"deleted": deleted})
        return deleted
