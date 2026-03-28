"""
Unit tests for src/shared/evaluated_store.py.

Coverage:
  - mark_evaluated()          writes a ticker record to DynamoDB
  - get_evaluated_tickers()   returns non-expired tickers, filters expired
  - get_evaluation_count()    returns count of non-expired tickers
  - clear_all()               deletes all records

DynamoDB is mocked via moto (mock_aws context in shared fixtures).
"""

from __future__ import annotations

import time

import pytest

from shared.evaluated_store import EvaluatedTickerStore

TABLE_NAME = "omaha-oracle-dev-universe"


# ------------------------------------------------------------------ #
# Fixtures                                                            #
# ------------------------------------------------------------------ #


@pytest.fixture()
def universe_table(_moto_session, aws_env, monkeypatch):
    """Create a fresh universe table for each test."""
    monkeypatch.setenv("TABLE_UNIVERSE", TABLE_NAME)

    table = _moto_session.create_table(
        TableName=TABLE_NAME,
        KeySchema=[{"AttributeName": "ticker", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "ticker", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    yield table
    table.delete()


# ------------------------------------------------------------------ #
# mark_evaluated + get_evaluated_tickers                              #
# ------------------------------------------------------------------ #


class TestMarkAndGet:
    """Verify tickers are stored and retrieved correctly."""

    def test_mark_single_ticker(self, universe_table):
        store = EvaluatedTickerStore(TABLE_NAME)
        store.mark_evaluated("AAPL", passed=True, search_id="test-1")

        tickers = store.get_evaluated_tickers()
        assert tickers == {"AAPL"}

    def test_mark_multiple_tickers(self, universe_table):
        store = EvaluatedTickerStore(TABLE_NAME)
        store.mark_evaluated("AAPL", passed=True, search_id="test-1")
        store.mark_evaluated("MSFT", passed=False, search_id="test-1")
        store.mark_evaluated("GOOG", passed=True, search_id="test-1")

        tickers = store.get_evaluated_tickers()
        assert tickers == {"AAPL", "MSFT", "GOOG"}

    def test_duplicate_ticker_overwrites(self, universe_table):
        store = EvaluatedTickerStore(TABLE_NAME)
        store.mark_evaluated("AAPL", passed=False, search_id="test-1")
        store.mark_evaluated("AAPL", passed=True, search_id="test-2")

        tickers = store.get_evaluated_tickers()
        assert tickers == {"AAPL"}

    def test_expired_tickers_filtered(self, universe_table):
        store = EvaluatedTickerStore(TABLE_NAME)

        # Write a record with an already-expired TTL
        from shared.dynamo_client import DynamoClient

        client = DynamoClient(TABLE_NAME)
        client.put_item(
            {
                "ticker": "EXPIRED",
                "evaluated_at": "2025-01-01T00:00:00+00:00",
                "passed_gates": False,
                "search_id": "old",
                "ttl": int(time.time()) - 1,  # Already expired
            }
        )

        # Write a valid record
        store.mark_evaluated("VALID", passed=True, search_id="test-1")

        tickers = store.get_evaluated_tickers()
        assert "VALID" in tickers
        assert "EXPIRED" not in tickers

    def test_empty_table_returns_empty_set(self, universe_table):
        store = EvaluatedTickerStore(TABLE_NAME)
        tickers = store.get_evaluated_tickers()
        assert tickers == set()


# ------------------------------------------------------------------ #
# get_evaluation_count                                                #
# ------------------------------------------------------------------ #


class TestEvaluationCount:
    """Verify count reflects non-expired records only."""

    def test_count_matches_tickers(self, universe_table):
        store = EvaluatedTickerStore(TABLE_NAME)
        assert store.get_evaluation_count() == 0

        store.mark_evaluated("AAPL", passed=True)
        store.mark_evaluated("MSFT", passed=False)
        assert store.get_evaluation_count() == 2


# ------------------------------------------------------------------ #
# clear_all                                                           #
# ------------------------------------------------------------------ #


class TestClearAll:
    """Verify all records are deleted."""

    def test_clear_removes_all(self, universe_table):
        store = EvaluatedTickerStore(TABLE_NAME)
        store.mark_evaluated("AAPL", passed=True)
        store.mark_evaluated("MSFT", passed=False)
        store.mark_evaluated("GOOG", passed=True)

        deleted = store.clear_all()
        assert deleted == 3
        assert store.get_evaluated_tickers() == set()
        assert store.get_evaluation_count() == 0

    def test_clear_empty_table(self, universe_table):
        store = EvaluatedTickerStore(TABLE_NAME)
        deleted = store.clear_all()
        assert deleted == 0
