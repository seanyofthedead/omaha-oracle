"""
LLM token-usage tracking and budget enforcement for Omaha Oracle.

Each call to ``CostTracker.log_usage()`` writes a record to the DynamoDB
cost-tracking table keyed by calendar month so that monthly spend can be
aggregated efficiently with a single Query (instead of a Scan).

Table schema
------------
PK  month_key  str   "2026-03"
SK  timestamp  str   ISO-8601 UTC, e.g. "2026-03-15T18:00:00.123456+00:00"
    model      str
    input_tokens   int (stored as N)
    output_tokens  int (stored as N)
    cost_usd   Decimal  (DynamoDB requires Decimal, not float)
    module     str
    ticker     str   (empty string when not applicable)
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import TypedDict

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from shared.config import get_config
from shared.logger import get_logger

_log = get_logger(__name__)

# ------------------------------------------------------------------ #
# Pricing table  (USD per 1 M tokens)                                #
# Keys are model ID prefixes so partial matches work for versioned   #
# model strings the API may return.                                  #
# ------------------------------------------------------------------ #

_PRICING: dict[str, tuple[Decimal, Decimal]] = {
    # model-id-prefix              input $/1M   output $/1M
    "claude-opus-4-20250514": (Decimal("15.00"), Decimal("75.00")),
    "claude-sonnet-4-20250514": (Decimal("3.00"), Decimal("15.00")),
    "claude-haiku-4-5-20251001": (Decimal("0.80"), Decimal("4.00")),
}

# Fallback when the model string is unrecognised — use the most expensive
# tier so we never undercount spend.
_FALLBACK_PRICING: tuple[Decimal, Decimal] = (Decimal("15.00"), Decimal("75.00"))


def _price_for_model(model: str) -> tuple[Decimal, Decimal]:
    """Return (input_per_1m, output_per_1m) for *model*."""
    for prefix, prices in _PRICING.items():
        if model.startswith(prefix) or prefix in model:
            return prices
    _log.warning("Unknown model %r — using fallback pricing", model)
    return _FALLBACK_PRICING


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """Return the USD cost for a single LLM call as a ``Decimal``."""
    input_price, output_price = _price_for_model(model)
    million = Decimal("1_000_000")
    cost = (
        input_price * Decimal(input_tokens) / million
        + output_price * Decimal(output_tokens) / million
    )
    return cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


# ------------------------------------------------------------------ #
# Return-type for check_budget()                                      #
# ------------------------------------------------------------------ #


class BudgetStatus(TypedDict):
    """Return value from :meth:`CostTracker.check_budget`."""

    budget_usd: float
    spent_usd: float
    remaining_usd: float
    exhausted: bool
    utilization_pct: float


# ------------------------------------------------------------------ #
# CostTracker                                                         #
# ------------------------------------------------------------------ #


class CostTracker:
    """
    Records LLM token usage to DynamoDB and checks monthly spend against
    the configured budget.

    Parameters
    ----------
    table_name:
        Override the DynamoDB table name.  When *None* the value from
        ``get_config().table_cost_tracking`` is used.
    """

    def __init__(self, table_name: str | None = None) -> None:
        """Initialize the tracker, defaulting to the configured cost-tracking table."""
        cfg = get_config()
        self._table_name = table_name or cfg.table_cost_tracking
        self._region = cfg.aws_region
        self._budget_usd = Decimal(str(cfg.monthly_budget_usd()))
        self._table = boto3.resource("dynamodb", region_name=self._region).Table(self._table_name)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def log_usage(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        module: str,
        ticker: str = "",
    ) -> float:
        """
        Write a usage record and return the cost in USD (as a float).

        Parameters
        ----------
        model:
            Anthropic model ID string (e.g. "claude-opus-4-20250514").
        input_tokens:
            Number of input/prompt tokens consumed.
        output_tokens:
            Number of output/completion tokens generated.
        module:
            Logical module or Lambda function name (for attribution).
        ticker:
            Equity ticker if the call was for a specific company, else "".
        """
        now = datetime.now(tz=UTC)
        month_key = now.strftime("%Y-%m")
        timestamp = now.isoformat()
        cost = compute_cost(model, input_tokens, output_tokens)

        item: dict[str, str | int | Decimal] = {
            "month_key": month_key,
            "timestamp": timestamp,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost,
            "module": module,
            "ticker": ticker,
        }

        try:
            self._table.put_item(Item=item)
            _log.info(
                "LLM usage logged",
                extra={
                    "model": model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": str(cost),
                    "module": module,
                    "ticker": ticker or None,
                    "month_key": month_key,
                },
            )
        except ClientError as exc:
            _log.error(
                "Failed to write cost record to DynamoDB",
                extra={"error": str(exc), "table": self._table_name},
            )
            raise

        return float(cost)

    def get_monthly_spend(self, month_key: str | None = None) -> Decimal:
        """
        Return the total USD spend for *month_key* (default: current month).

        Queries all records for the given month and sums ``cost_usd``.
        Handles DynamoDB pagination automatically.

        Parameters
        ----------
        month_key:
            String in "YYYY-MM" format.  Defaults to the current calendar
            month in UTC.
        """
        if month_key is None:
            month_key = datetime.now(tz=UTC).strftime("%Y-%m")

        total = Decimal("0")
        kwargs: dict[str, object] = {
            "KeyConditionExpression": Key("month_key").eq(month_key),
            "ProjectionExpression": "cost_usd",
        }

        while True:
            try:
                response = self._table.query(**kwargs)  # type: ignore[arg-type]
            except ClientError as exc:
                _log.error(
                    "DynamoDB query failed for monthly spend",
                    extra={"month_key": month_key, "error": str(exc)},
                )
                raise

            for item in response.get("Items", []):
                raw = item.get("cost_usd", Decimal("0"))
                total += Decimal(str(raw))

            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            kwargs["ExclusiveStartKey"] = last_key

        return total.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

    def get_spend_history(self, month_keys: list[str]) -> dict[str, float]:
        """
        Return total USD spend for each key in *month_keys* using a single
        table scan, rather than one Query per month.

        Parameters
        ----------
        month_keys:
            List of "YYYY-MM" strings.

        Returns
        -------
        dict mapping month_key → total spend in USD (float).
        Missing months are omitted from the result (caller should default to 0).
        """
        if not month_keys:
            return {}

        totals: dict[str, Decimal] = {}

        for mk in month_keys:
            kwargs: dict[str, object] = {
                "KeyConditionExpression": Key("month_key").eq(mk),
                "ProjectionExpression": "cost_usd",
            }
            while True:
                try:
                    response = self._table.query(**kwargs)  # type: ignore[arg-type]
                except ClientError as exc:
                    _log.error(
                        "DynamoDB query failed for spend history",
                        extra={"month_key": mk, "error": str(exc)},
                    )
                    raise

                for item in response.get("Items", []):
                    raw = item.get("cost_usd", Decimal("0"))
                    totals[mk] = totals.get(mk, Decimal("0")) + Decimal(str(raw))

                last_key = response.get("LastEvaluatedKey")
                if not last_key:
                    break
                kwargs["ExclusiveStartKey"] = last_key

        return {mk: float(total) for mk, total in totals.items()}

    def check_budget(self, month_key: str | None = None) -> BudgetStatus:
        """
        Return a snapshot of budget usage for *month_key* (default: this month).

        Returns
        -------
        BudgetStatus
            A ``TypedDict`` with keys:
            - ``budget_usd``      – configured monthly cap
            - ``spent_usd``       – total spend so far this month
            - ``remaining_usd``   – budget minus spend (clamped to 0)
            - ``exhausted``       – True when spend ≥ budget
            - ``utilization_pct`` – (spent / budget) × 100, capped at 100
        """
        spent = self.get_monthly_spend(month_key)
        budget = self._budget_usd
        remaining = max(Decimal("0"), budget - spent)
        exhausted = spent >= budget
        utilization = float(
            min(Decimal("100"), (spent / budget * 100).quantize(Decimal("0.01")))
            if budget > 0
            else Decimal("100")
        )

        status: BudgetStatus = {
            "budget_usd": float(budget),
            "spent_usd": float(spent),
            "remaining_usd": float(remaining),
            "exhausted": exhausted,
            "utilization_pct": utilization,
        }

        _log.info(
            "Budget check",
            extra={
                "month_key": month_key or datetime.now(tz=UTC).strftime("%Y-%m"),
                **{k: str(v) if isinstance(v, Decimal) else v for k, v in status.items()},
            },
        )
        return status
