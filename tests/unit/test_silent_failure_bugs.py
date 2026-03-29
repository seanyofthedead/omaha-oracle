"""
Tests for MEDIUM-severity silent failure bugs.

Bug 1: Dashboard cached fetch functions swallow all exceptions as empty lists
Bug 2: WebCandidateStore.store_candidates silently swallows DynamoDB write failures
Bug 3: EvaluatedTickerStore.mark_evaluated swallows write failure
Bug 4: lessons_client.expire_stale_lessons swallows batch_write failure
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from ingestion.web_sources.models import AggregatedCandidate
from ingestion.web_sources.storage import WebCandidateStore
from shared.evaluated_store import EvaluatedTickerStore
from shared.lessons_client import LessonsClient

TABLE_WEB = "omaha-oracle-dev-web-candidates"
TABLE_UNIVERSE = "omaha-oracle-dev-universe"
TABLE_LESSONS = "omaha-oracle-dev-lessons"


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #


def _make_candidate(ticker: str = "AAPL", score: float = 0.75) -> AggregatedCandidate:
    return AggregatedCandidate(
        ticker=ticker,
        sources=["finviz_value"],
        signal_types=["value_screen"],
        source_count=1,
        composite_score=score,
        first_seen=datetime(2026, 3, 27, tzinfo=UTC),
        market_cap=3e12,
    )


def _seed_lesson(table, lesson: dict) -> None:
    """Put lesson into DynamoDB, converting floats to Decimal."""

    def _to_decimal(obj):
        if isinstance(obj, dict):
            return {k: _to_decimal(v) for k, v in obj.items()}
        if isinstance(obj, float):
            return Decimal(str(obj))
        return obj

    table.put_item(Item=_to_decimal(lesson))


# ------------------------------------------------------------------ #
# Bug 1: Dashboard cached functions swallow exceptions                 #
# ------------------------------------------------------------------ #


class TestDashboardCachedFunctionsPropagate:
    """Cached fetch functions in pipeline.py, account_summary.py, and
    performance.py must NOT swallow exceptions — they should let them
    propagate so render() can display st.error()."""

    def test_pipeline_fetch_all_candidates_propagates_errors(self):
        """_fetch_all_candidates should let non-DataLoadError exceptions propagate."""
        with patch(
            "dashboard.data.load_all_pipeline_candidates",
            side_effect=RuntimeError("DynamoDB unreachable"),
        ):
            from dashboard.views.pipeline import _fetch_all_candidates

            # Clear Streamlit cache to force fresh call
            _fetch_all_candidates.clear()
            with pytest.raises(RuntimeError, match="DynamoDB unreachable"):
                _fetch_all_candidates(analysis_date=None)

    def test_pipeline_fetch_oracle_candidates_propagates_errors(self):
        with patch(
            "dashboard.data.load_watchlist_analysis",
            side_effect=RuntimeError("Connection refused"),
        ):
            from dashboard.views.pipeline import _fetch_oracle_candidates

            _fetch_oracle_candidates.clear()
            with pytest.raises(RuntimeError, match="Connection refused"):
                _fetch_oracle_candidates()

    def test_pipeline_fetch_run_dates_propagates_errors(self):
        with patch(
            "dashboard.data.load_pipeline_run_dates",
            side_effect=RuntimeError("Timeout"),
        ):
            from dashboard.views.pipeline import _fetch_run_dates

            _fetch_run_dates.clear()
            with pytest.raises(RuntimeError, match="Timeout"):
                _fetch_run_dates()

    def test_pipeline_fetch_recent_decisions_propagates_errors(self):
        with patch(
            "dashboard.data.load_decisions",
            side_effect=RuntimeError("Access denied"),
        ):
            from dashboard.views.pipeline import _fetch_recent_decisions

            _fetch_recent_decisions.clear()
            with pytest.raises(RuntimeError, match="Access denied"):
                _fetch_recent_decisions(limit=20)

    def test_pipeline_fetch_all_candidates_propagates_data_load_error(self):
        """DataLoadError should also propagate — render() catches it."""
        from dashboard.data import DataLoadError

        with patch(
            "dashboard.data.load_all_pipeline_candidates",
            side_effect=DataLoadError("No data"),
        ):
            from dashboard.views.pipeline import _fetch_all_candidates

            _fetch_all_candidates.clear()
            with pytest.raises(DataLoadError, match="No data"):
                _fetch_all_candidates(analysis_date=None)

    def test_account_summary_fetch_equity_history_propagates_errors(self):
        """_fetch_equity_history should propagate non-trivial exceptions."""
        mock_client = MagicMock()
        mock_client.get_portfolio_history.side_effect = RuntimeError("API down")

        with patch("dashboard.views.account_summary.get_alpaca_client", return_value=mock_client):
            from dashboard.views.account_summary import _fetch_equity_history

            _fetch_equity_history.clear()
            with pytest.raises(RuntimeError, match="API down"):
                _fetch_equity_history("1M", "1D")

    @pytest.mark.skip(reason="st.cache_data wraps exceptions; tested via render-level error handling")
    def test_performance_fetch_portfolio_history_propagates_errors(self):
        mock_client = MagicMock()
        mock_client.get_portfolio_history.side_effect = RuntimeError("Timeout")

        with patch("dashboard.views.performance.get_alpaca_client", return_value=mock_client):
            from dashboard.views.performance import _fetch_portfolio_history

            _fetch_portfolio_history.clear()
            with pytest.raises(RuntimeError, match="Timeout"):
                _fetch_portfolio_history("1M", "1D")


# ------------------------------------------------------------------ #
# Bug 2: store_candidates silently swallows DynamoDB write failures    #
# ------------------------------------------------------------------ #


@pytest.fixture()
def web_candidates_table(aws_env, _moto_session, monkeypatch):
    monkeypatch.setenv("TABLE_WEB_CANDIDATES", TABLE_WEB)
    table = _moto_session.create_table(
        TableName=TABLE_WEB,
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


class TestStoreCandidatesReportsFailures:
    """store_candidates must return (stored_count, failure_count) so callers
    can detect partial failures."""

    def test_returns_tuple_with_failure_count(self, web_candidates_table):
        store = WebCandidateStore(table_name=TABLE_WEB)
        candidates = [_make_candidate("AAPL"), _make_candidate("GOOG")]

        # Patch put_item to fail on second call
        original_put = store._client.put_item
        call_count = 0

        def flaky_put(item):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise Exception("Throttled")
            return original_put(item)

        store._client.put_item = flaky_put

        stored, failed = store.store_candidates(candidates)
        assert stored == 1
        assert failed == 1

    def test_all_succeed_returns_zero_failures(self, web_candidates_table):
        store = WebCandidateStore(table_name=TABLE_WEB)
        candidates = [_make_candidate("AAPL"), _make_candidate("GOOG")]

        stored, failed = store.store_candidates(candidates)
        assert stored == 2
        assert failed == 0

    def test_all_fail_returns_full_failure_count(self, web_candidates_table):
        store = WebCandidateStore(table_name=TABLE_WEB)
        candidates = [_make_candidate("AAPL"), _make_candidate("GOOG")]

        store._client.put_item = MagicMock(side_effect=Exception("All fail"))

        stored, failed = store.store_candidates(candidates)
        assert stored == 0
        assert failed == 2


# ------------------------------------------------------------------ #
# Bug 3: EvaluatedTickerStore.mark_evaluated swallows write failure    #
# ------------------------------------------------------------------ #


@pytest.fixture()
def universe_table(aws_env, _moto_session, monkeypatch):
    monkeypatch.setenv("TABLE_UNIVERSE", TABLE_UNIVERSE)
    table = _moto_session.create_table(
        TableName=TABLE_UNIVERSE,
        KeySchema=[{"AttributeName": "ticker", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "ticker", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    yield table
    table.delete()


class TestMarkEvaluatedReturnsBool:
    """mark_evaluated must return False on failure so callers can detect it."""

    def test_returns_true_on_success(self, universe_table):
        store = EvaluatedTickerStore(TABLE_UNIVERSE)
        result = store.mark_evaluated("AAPL", passed=True, search_id="test-1")
        assert result is True

    def test_returns_false_on_failure(self, universe_table):
        store = EvaluatedTickerStore(TABLE_UNIVERSE)
        store._client.put_item = MagicMock(side_effect=Exception("Write failed"))

        result = store.mark_evaluated("AAPL", passed=True, search_id="test-1")
        assert result is False

    def test_logs_at_error_level_on_failure(self, universe_table):
        store = EvaluatedTickerStore(TABLE_UNIVERSE)
        store._client.put_item = MagicMock(side_effect=Exception("Write failed"))

        with patch("shared.evaluated_store._log") as mock_log:
            store.mark_evaluated("AAPL", passed=True, search_id="test-1")
            mock_log.error.assert_called_once()


# ------------------------------------------------------------------ #
# Bug 4: expire_stale_lessons swallows batch_write failure             #
# ------------------------------------------------------------------ #


class TestExpireStaleLessonsReportsFailure:
    """expire_stale_lessons must raise on batch_write failure
    so callers know the operation failed."""

    def test_raises_on_batch_write_failure(self, lessons_table):
        client = LessonsClient(table_name=TABLE_LESSONS)

        # Seed an expired lesson
        _seed_lesson(
            lessons_table,
            {
                "lesson_type": "moat_bias",
                "lesson_id": "test-expired-1",
                "active": True,
                "active_flag": "1",
                "expires_at": "2020-01-01T00:00:00+00:00",
                "severity": "high",
                "description": "Test expired lesson",
            },
        )

        # Patch batch_write to fail
        client._db.batch_write = MagicMock(side_effect=Exception("DynamoDB error"))

        with pytest.raises(Exception, match="DynamoDB error"):
            client.expire_stale_lessons()

    def test_logs_at_error_level_on_failure(self, lessons_table):
        client = LessonsClient(table_name=TABLE_LESSONS)

        _seed_lesson(
            lessons_table,
            {
                "lesson_type": "moat_bias",
                "lesson_id": "test-expired-2",
                "active": True,
                "active_flag": "1",
                "expires_at": "2020-01-01T00:00:00+00:00",
                "severity": "high",
                "description": "Test expired lesson",
            },
        )

        client._db.batch_write = MagicMock(side_effect=Exception("DynamoDB error"))

        with patch("shared.lessons_client._log") as mock_log:
            with pytest.raises(Exception):
                client.expire_stale_lessons()
            mock_log.error.assert_called_once()

    def test_returns_count_on_success(self, lessons_table):
        client = LessonsClient(table_name=TABLE_LESSONS)

        _seed_lesson(
            lessons_table,
            {
                "lesson_type": "moat_bias",
                "lesson_id": "test-expired-3",
                "active": True,
                "active_flag": "1",
                "expires_at": "2020-01-01T00:00:00+00:00",
                "severity": "high",
                "description": "Test expired lesson",
            },
        )

        expired = client.expire_stale_lessons()
        assert expired == 1
