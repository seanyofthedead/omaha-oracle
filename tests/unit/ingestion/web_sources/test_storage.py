"""Tests for web candidate DynamoDB storage."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ingestion.web_sources.models import AggregatedCandidate
from ingestion.web_sources.storage import WebCandidateStore

TABLE_NAME = "omaha-oracle-dev-web-candidates"


@pytest.fixture()
def web_candidates_table(aws_env, _moto_session):
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


def _make_candidate(
    ticker: str = "AAPL",
    sources: list[str] | None = None,
    score: float = 0.75,
    market_cap: float | None = 3e12,
) -> AggregatedCandidate:
    return AggregatedCandidate(
        ticker=ticker,
        sources=sources or ["finviz_value"],
        signal_types=["value_screen"],
        source_count=len(sources) if sources else 1,
        composite_score=score,
        first_seen=datetime(2026, 3, 27, tzinfo=UTC),
        market_cap=market_cap,
    )


class TestWebCandidateStore:
    def test_store_and_retrieve(self, web_candidates_table):
        store = WebCandidateStore(table_name=TABLE_NAME)
        candidates = [_make_candidate("AAPL"), _make_candidate("GOOG", score=0.9)]

        stored = store.store_candidates(candidates)
        assert stored == 2

        retrieved = store.get_top_candidates(limit=10)
        tickers = {c.ticker for c in retrieved}
        assert "AAPL" in tickers
        assert "GOOG" in tickers

    def test_get_top_respects_min_score(self, web_candidates_table):
        store = WebCandidateStore(table_name=TABLE_NAME)
        store.store_candidates(
            [
                _make_candidate("HIGH", score=0.9),
                _make_candidate("LOW", score=0.2),
            ]
        )

        retrieved = store.get_top_candidates(min_score=0.5)
        tickers = {c.ticker for c in retrieved}
        assert "HIGH" in tickers
        assert "LOW" not in tickers

    def test_mark_evaluated(self, web_candidates_table):
        store = WebCandidateStore(table_name=TABLE_NAME)
        candidate = _make_candidate("AAPL")
        store.store_candidates([candidate])

        # Get the stored item to find the source_key
        all_items = store.get_top_candidates(limit=10)
        assert len(all_items) == 1

        # After marking, should not appear in "pending" queries
        table = web_candidates_table
        items = table.scan()["Items"]
        source_key = items[0]["source_key"]

        store.mark_evaluated("AAPL", source_key)

        pending = store.get_top_candidates(status="pending")
        pending_tickers = {c.ticker for c in pending}
        assert "AAPL" not in pending_tickers

    def test_store_handles_none_fields(self, web_candidates_table):
        store = WebCandidateStore(table_name=TABLE_NAME)
        candidate = _make_candidate("AAPL", market_cap=None)
        stored = store.store_candidates([candidate])
        assert stored == 1

    def test_get_source_stats(self, web_candidates_table):
        store = WebCandidateStore(table_name=TABLE_NAME)
        store.store_candidates(
            [
                _make_candidate("AAPL", sources=["finviz_value"]),
                _make_candidate("GOOG", sources=["finviz_value", "sec_fulltext"]),
            ]
        )
        stats = store.get_source_stats()
        assert stats["finviz_value"] == 2
        assert stats["sec_fulltext"] == 1
