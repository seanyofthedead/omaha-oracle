"""
Unit tests for CostTracker — Mock DynamoDB, test budget enforcement.

Uses moto for DynamoDB mocking (see conftest).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from shared.cost_tracker import CostTracker

TABLE_NAME = "omaha-oracle-dev-cost-tracking"


def _seed_cost_item(table, month_key: str, timestamp: str, cost_usd: Decimal) -> None:
    table.put_item(
        Item={
            "month_key": month_key,
            "timestamp": timestamp,
            "model": "claude-opus-4-20250514",
            "input_tokens": 1000,
            "output_tokens": 500,
            "cost_usd": cost_usd,
            "module": "test",
            "ticker": "",
        }
    )


class TestBudgetEnforcement:
    """Budget enforcement: exhausted when spend >= budget."""

    def test_under_budget_not_exhausted(self, cost_tracker: CostTracker, dynamodb_table):
        _seed_cost_item(dynamodb_table, "2026-03", "2026-03-01T00:00:00+00:00", Decimal("25.00"))
        status = cost_tracker.check_budget("2026-03")
        assert status["exhausted"] is False
        assert status["remaining_usd"] == pytest.approx(75.0)

    def test_at_budget_exhausted(self, cost_tracker: CostTracker, dynamodb_table):
        _seed_cost_item(dynamodb_table, "2026-03", "2026-03-01T00:00:00+00:00", Decimal("100.00"))
        status = cost_tracker.check_budget("2026-03")
        assert status["exhausted"] is True
        assert status["remaining_usd"] == pytest.approx(0.0)

    def test_over_budget_remaining_clamped(self, cost_tracker: CostTracker, dynamodb_table):
        _seed_cost_item(dynamodb_table, "2026-03", "2026-03-01T00:00:00+00:00", Decimal("150.00"))
        status = cost_tracker.check_budget("2026-03")
        assert status["exhausted"] is True
        assert status["remaining_usd"] == pytest.approx(0.0)
        assert status["utilization_pct"] == pytest.approx(100.0)
