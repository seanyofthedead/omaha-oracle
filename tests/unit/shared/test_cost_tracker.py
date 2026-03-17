"""
Unit tests for src/shared/cost_tracker.py.

Coverage:
  - _price_for_model()          pure price lookup with prefix / substring matching
  - compute_cost()              pure Decimal arithmetic
  - CostTracker.log_usage()     writes to DynamoDB, returns float cost
  - CostTracker.get_monthly_spend()  paginated DynamoDB query, Decimal sum
  - CostTracker.check_budget()  budget math on top of get_monthly_spend

DynamoDB is mocked via moto (mock_aws context in shared fixtures).
ClientError scenarios use unittest.mock.patch so the error path is tested
independently of moto internals.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from shared.cost_tracker import (
    CostTracker,
    _price_for_model,
    compute_cost,
)

# Re-export the table name constant so tests stay in sync with conftest.
TABLE_NAME = "omaha-oracle-dev-cost-tracking"

# ------------------------------------------------------------------ #
# Helpers                                                             #
# ------------------------------------------------------------------ #


def _make_client_error(code: str = "ProvisionedThroughputExceededException") -> ClientError:
    """Build a realistic botocore ClientError for error-handling tests."""
    return ClientError(
        {"Error": {"Code": code, "Message": "Simulated DynamoDB error"}},
        "Operation",
    )


def _seed_cost_item(
    table,
    month_key: str,
    timestamp: str,
    cost_usd: Decimal,
) -> None:
    """Write a minimal cost record directly to the moto table."""
    table.put_item(
        Item={
            "month_key": month_key,
            "timestamp": timestamp,
            "model": "claude-opus-4-20250514",
            "input_tokens": 1000,
            "output_tokens": 500,
            "cost_usd": cost_usd,
            "module": "test_module",
            "ticker": "",
        }
    )


# ================================================================== #
# 1. _price_for_model — pure lookup, no DynamoDB                     #
# ================================================================== #


class TestPriceForModel:
    """
    _price_for_model resolves (input_$/1M, output_$/1M) from a hardcoded
    pricing table using prefix/substring matching, falling back to the most
    expensive tier for unrecognised model strings.
    """

    def test_known_model_opus(self):
        # Exact prefix — ensures the most expensive model is priced correctly
        # so cost calculations never under-bill high-value calls.
        input_p, output_p = _price_for_model("claude-opus-4-20250514")
        assert input_p == Decimal("15.00")
        assert output_p == Decimal("75.00")

    def test_known_model_sonnet(self):
        # Sonnet is a mid-tier model; verify its lower rates are resolved.
        input_p, output_p = _price_for_model("claude-sonnet-4-20250514")
        assert input_p == Decimal("3.00")
        assert output_p == Decimal("15.00")

    def test_known_model_haiku(self):
        # Haiku is the cheapest model; confirm the floor rates apply.
        input_p, output_p = _price_for_model("claude-haiku-4-5-20251001")
        assert input_p == Decimal("0.80")
        assert output_p == Decimal("4.00")

    def test_model_with_version_suffix_still_matches(self):
        # The Anthropic API may return versioned strings like "…:3".
        # Substring matching ensures these still resolve to the correct tier.
        input_p, output_p = _price_for_model("claude-opus-4-20250514:3")
        assert input_p == Decimal("15.00")
        assert output_p == Decimal("75.00")

    def test_unknown_model_falls_back_to_most_expensive(self):
        # Unrecognised models use the fallback tier so we never undercount spend.
        # If a new model is added we'd rather overestimate than underestimate.
        input_p, output_p = _price_for_model("gpt-4-turbo")
        assert input_p == Decimal("15.00")
        assert output_p == Decimal("75.00")


# ================================================================== #
# 2. compute_cost — pure Decimal arithmetic, no DynamoDB             #
# ================================================================== #


class TestComputeCost:
    """
    compute_cost(model, input_tokens, output_tokens) → Decimal

    All results are quantized to exactly 6 decimal places (ROUND_HALF_UP).
    """

    def test_opus_known_tokens(self):
        # 1000 input @ $15/1M + 500 output @ $75/1M = $0.015 + $0.0375 = $0.052500
        result = compute_cost("claude-opus-4-20250514", 1000, 500)
        assert result == Decimal("0.052500")

    def test_sonnet_known_tokens(self):
        # 1000 input @ $3/1M + 500 output @ $15/1M = $0.003 + $0.0075 = $0.010500
        result = compute_cost("claude-sonnet-4-20250514", 1000, 500)
        assert result == Decimal("0.010500")

    def test_haiku_known_tokens(self):
        # 1000 input @ $0.80/1M + 500 output @ $4/1M = $0.0008 + $0.002 = $0.002800
        result = compute_cost("claude-haiku-4-5-20251001", 1000, 500)
        assert result == Decimal("0.002800")

    def test_zero_tokens_returns_zero(self):
        # A call with no tokens should cost nothing — not an error, not a
        # negative number, not undefined.
        result = compute_cost("claude-opus-4-20250514", 0, 0)
        assert result == Decimal("0.000000")

    def test_only_input_tokens(self):
        # Confirms output tokens are correctly excluded from the calculation.
        result = compute_cost("claude-opus-4-20250514", 1000, 0)
        # 1000 * $15.00 / 1_000_000 = $0.015000
        assert result == Decimal("0.015000")

    def test_only_output_tokens(self):
        # Confirms input tokens are correctly excluded from the calculation.
        result = compute_cost("claude-opus-4-20250514", 0, 1000)
        # 1000 * $75.00 / 1_000_000 = $0.075000
        assert result == Decimal("0.075000")

    def test_large_token_counts_no_overflow(self):
        # 10M tokens of each type should produce a sensible result, not an
        # OverflowError or precision loss (Decimal handles arbitrary magnitude).
        result = compute_cost("claude-opus-4-20250514", 10_000_000, 10_000_000)
        # 10M * $15/1M + 10M * $75/1M = $150 + $750 = $900
        assert result == Decimal("900.000000")

    def test_result_quantized_to_six_decimal_places(self):
        # The 6-digit precision is a contract: callers and DynamoDB both rely
        # on this fixed scale for consistent formatting and storage.
        result = compute_cost("claude-opus-4-20250514", 1, 1)
        assert result.as_tuple().exponent == -6

    def test_unknown_model_uses_fallback_pricing(self):
        # An unrecognised model string must be billed at the most expensive
        # (opus) rates rather than silently costing zero or raising.
        result = compute_cost("unknown-model-v99", 1_000_000, 1_000_000)
        expected = compute_cost("claude-opus-4-20250514", 1_000_000, 1_000_000)
        assert result == expected


# ================================================================== #
# 3. CostTracker.log_usage — writes to DynamoDB                      #
# ================================================================== #


class TestLogUsage:
    """
    log_usage computes cost, writes a record to DynamoDB, and returns the
    cost as a float.  All DynamoDB interactions use the moto-backed table
    provided by the conftest fixtures.
    """

    def test_happy_path_returns_float_cost(self, cost_tracker: CostTracker):
        # The float return value is used by callers to gate budget decisions.
        # It must equal compute_cost for the same inputs.
        result = cost_tracker.log_usage(
            "claude-opus-4-20250514", 1000, 500, "analysis", "AAPL"
        )
        expected = float(compute_cost("claude-opus-4-20250514", 1000, 500))
        assert isinstance(result, float)
        assert result == pytest.approx(expected)

    def test_item_written_to_dynamodb_has_correct_shape(
        self, cost_tracker: CostTracker, dynamodb_table
    ):
        # Every field in the item schema must be present and correctly typed;
        # a missing field would break get_monthly_spend's cost_usd aggregation.
        cost_tracker.log_usage(
            "claude-opus-4-20250514", 1000, 500, "analysis", "AAPL"
        )
        response = dynamodb_table.scan()
        items = response["Items"]
        assert len(items) == 1
        item = items[0]
        assert item["model"] == "claude-opus-4-20250514"
        assert int(item["input_tokens"]) == 1000
        assert int(item["output_tokens"]) == 500
        assert Decimal(str(item["cost_usd"])) == Decimal("0.052500")
        assert item["module"] == "analysis"
        assert item["ticker"] == "AAPL"
        # Key fields must be present for the query access pattern to work.
        assert "month_key" in item
        assert "timestamp" in item

    def test_default_ticker_is_empty_string(
        self, cost_tracker: CostTracker, dynamodb_table
    ):
        # ticker is optional — omitting it must not raise and must store "".
        cost_tracker.log_usage("claude-opus-4-20250514", 100, 50, "screening")
        items = dynamodb_table.scan()["Items"]
        assert items[0]["ticker"] == ""

    def test_zero_input_tokens_stored_correctly(
        self, cost_tracker: CostTracker, dynamodb_table
    ):
        # A zero-token call is valid (e.g. a cached response); cost_usd must
        # reflect output-only pricing, not an error or a zero cost.
        cost_tracker.log_usage("claude-opus-4-20250514", 0, 1000, "portfolio")
        items = dynamodb_table.scan()["Items"]
        stored_cost = Decimal(str(items[0]["cost_usd"]))
        assert stored_cost == compute_cost("claude-opus-4-20250514", 0, 1000)

    def test_dynamodb_client_error_is_reraised(self, cost_tracker: CostTracker):
        # If DynamoDB is throttling or unavailable the caller must see the
        # error — silently dropping records would undercount spend.
        with patch.object(
            cost_tracker._table,
            "put_item",
            side_effect=_make_client_error("ProvisionedThroughputExceededException"),
        ):
            with pytest.raises(ClientError):
                cost_tracker.log_usage(
                    "claude-opus-4-20250514", 1000, 500, "analysis"
                )


# ================================================================== #
# 4. CostTracker.get_monthly_spend — paginated DynamoDB query        #
# ================================================================== #


class TestGetMonthlySpend:
    """
    get_monthly_spend queries a specific month_key partition and sums all
    cost_usd values, handling pagination automatically.
    """

    def test_empty_month_returns_zero(self, cost_tracker: CostTracker):
        # A month with no records must return exactly zero, not raise or
        # return None — callers always expect a Decimal.
        result = cost_tracker.get_monthly_spend("2099-01")
        assert result == Decimal("0.000000")

    def test_single_item_summed_correctly(
        self, cost_tracker: CostTracker, dynamodb_table
    ):
        # Verifies the basic query + sum path against a known cost value.
        _seed_cost_item(dynamodb_table, "2026-03", "2026-03-01T00:00:00+00:00", Decimal("0.052500"))
        result = cost_tracker.get_monthly_spend("2026-03")
        assert result == Decimal("0.052500")

    def test_multiple_items_summed_correctly(
        self, cost_tracker: CostTracker, dynamodb_table
    ):
        # Three records across different timestamps in the same month must be
        # aggregated into a single total (tests the accumulation loop).
        _seed_cost_item(dynamodb_table, "2026-03", "2026-03-01T00:00:00+00:00", Decimal("0.052500"))
        _seed_cost_item(dynamodb_table, "2026-03", "2026-03-02T00:00:00+00:00", Decimal("0.010500"))
        _seed_cost_item(dynamodb_table, "2026-03", "2026-03-03T00:00:00+00:00", Decimal("0.002800"))
        result = cost_tracker.get_monthly_spend("2026-03")
        assert result == Decimal("0.065800")

    def test_default_month_key_uses_current_month(
        self, cost_tracker: CostTracker, dynamodb_table
    ):
        # When month_key=None the implementation falls back to UTC now().
        # Seed an item using the same logic to avoid coupling to a hard-coded date.
        from datetime import UTC, datetime

        current = datetime.now(tz=UTC).strftime("%Y-%m")
        _seed_cost_item(
            dynamodb_table,
            current,
            datetime.now(tz=UTC).isoformat(),
            Decimal("0.100000"),
        )
        result = cost_tracker.get_monthly_spend(None)
        assert result == Decimal("0.100000")

    def test_records_from_other_months_are_excluded(
        self, cost_tracker: CostTracker, dynamodb_table
    ):
        # Items in a different month_key partition must not pollute the query;
        # the KeyConditionExpression must filter correctly.
        _seed_cost_item(dynamodb_table, "2026-02", "2026-02-28T00:00:00+00:00", Decimal("9.999999"))
        _seed_cost_item(dynamodb_table, "2026-03", "2026-03-01T00:00:00+00:00", Decimal("0.050000"))
        result = cost_tracker.get_monthly_spend("2026-03")
        assert result == Decimal("0.050000")

    def test_dynamodb_client_error_is_reraised(self, cost_tracker: CostTracker):
        # A DynamoDB query failure must surface to the caller; swallowing it
        # would return 0 and allow an exhausted budget to appear healthy.
        with patch.object(
            cost_tracker._table,
            "query",
            side_effect=_make_client_error("ResourceNotFoundException"),
        ):
            with pytest.raises(ClientError):
                cost_tracker.get_monthly_spend("2026-03")


# ================================================================== #
# 5. CostTracker.check_budget — budget math                          #
# ================================================================== #


class TestCheckBudget:
    """
    check_budget delegates spend retrieval to get_monthly_spend and applies
    budget arithmetic to produce a BudgetStatus TypedDict.

    Budget is set to $100.00 by the aws_env fixture (MONTHLY_LLM_BUDGET_USD=100.0).
    """

    def test_no_spend_gives_full_remaining(
        self, cost_tracker: CostTracker
    ):
        # When no records exist the budget is untouched: spent=0, remaining=budget.
        status = cost_tracker.check_budget("2026-03")
        assert status["spent_usd"] == pytest.approx(0.0)
        assert status["remaining_usd"] == pytest.approx(100.0)
        assert status["budget_usd"] == pytest.approx(100.0)
        assert status["exhausted"] is False
        assert status["utilization_pct"] == pytest.approx(0.0)

    def test_under_budget(self, cost_tracker: CostTracker, dynamodb_table):
        # Spending $25 against a $100 budget → 25% utilisation, not exhausted.
        _seed_cost_item(
            dynamodb_table, "2026-03", "2026-03-01T00:00:00+00:00", Decimal("25.000000")
        )
        status = cost_tracker.check_budget("2026-03")
        assert status["spent_usd"] == pytest.approx(25.0)
        assert status["remaining_usd"] == pytest.approx(75.0)
        assert status["exhausted"] is False
        assert status["utilization_pct"] == pytest.approx(25.0)

    def test_exactly_at_budget_is_exhausted(
        self, cost_tracker: CostTracker, dynamodb_table
    ):
        # spent == budget must trigger exhausted=True; the boundary condition
        # is spend >= budget (not strictly greater-than).
        _seed_cost_item(
            dynamodb_table, "2026-03", "2026-03-01T00:00:00+00:00", Decimal("100.000000")
        )
        status = cost_tracker.check_budget("2026-03")
        assert status["exhausted"] is True
        assert status["remaining_usd"] == pytest.approx(0.0)
        assert status["utilization_pct"] == pytest.approx(100.0)

    def test_over_budget_remaining_clamped_to_zero(
        self, cost_tracker: CostTracker, dynamodb_table
    ):
        # Spend above the cap must clamp remaining to 0 (never go negative)
        # and cap utilisation at 100 — callers must not see negative headroom.
        _seed_cost_item(
            dynamodb_table, "2026-03", "2026-03-01T00:00:00+00:00", Decimal("150.000000")
        )
        status = cost_tracker.check_budget("2026-03")
        assert status["exhausted"] is True
        assert status["remaining_usd"] == pytest.approx(0.0)
        assert status["utilization_pct"] == pytest.approx(100.0)

    def test_zero_budget_utilization_is_always_100(
        self, dynamodb_table, monkeypatch: pytest.MonkeyPatch  # noqa: ARG002
    ):
        """
        When the configured budget is $0 the division-by-zero branch fires and
        utilisation is forced to 100%.  This test creates its own CostTracker
        after overriding the budget env vars so the special-case path is
        exercised without affecting the other tests' budget baseline.
        """
        from shared.config import get_config

        # Override budget to zero and refresh the config singleton so the new
        # CostTracker reads $0 from Settings.monthly_budget_usd().
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_USD", "0")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")
        get_config.cache_clear()

        zero_budget_tracker = CostTracker(table_name=TABLE_NAME)
        status = zero_budget_tracker.check_budget("2026-03")
        assert status["exhausted"] is True
        assert status["utilization_pct"] == pytest.approx(100.0)
        assert status["budget_usd"] == pytest.approx(0.0)
