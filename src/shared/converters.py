"""
Pure-stdlib converters shared across all Omaha Oracle Lambda handlers and
the Streamlit dashboard.

No AWS dependencies — no mocking required in unit tests.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any


MAINTENANCE_CAPEX_FACTOR = 0.7
"""Fraction of reported capex assumed to be maintenance (vs. growth) capex.

Used in the Buffett-style owner-earnings calculation.  The 0.7 estimate
follows the heuristic that ~70 % of capex is required just to maintain
current earning power.
"""


def compute_owner_earnings(ni: float, dep: float, capex: float) -> float:
    """Compute Buffett-style owner earnings.

    ``owner_earnings = net_income + depreciation - maintenance_capex``

    where *maintenance_capex* is estimated as
    ``MAINTENANCE_CAPEX_FACTOR * capex``.
    """
    return ni + dep - MAINTENANCE_CAPEX_FACTOR * capex


def safe_float(val: Any, default: float = 0.0) -> float:
    """Convert *val* to float, returning *default* on failure.

    Handles ``None``, ``Decimal``, numeric strings, and other numeric types.
    """
    if val is None:
        return default
    if isinstance(val, Decimal):
        return float(val)
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def safe_int(val: Any, default: int = 0) -> int:
    """Convert *val* to int via float intermediate, returning *default* on failure."""
    if val is None:
        return default
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def today_str() -> str:
    """Return today's date as ``YYYY-MM-DD`` in UTC."""
    return datetime.now(UTC).strftime("%Y-%m-%d")


def normalize_ticker(event: dict[str, Any]) -> str:
    """Extract and normalise the ``ticker`` field from a Lambda event dict."""
    return (event.get("ticker") or "").strip().upper()


def check_failure_threshold(
    errors: list[Any],
    total: int,
    module_name: str,
    threshold: float = 0.5,
) -> None:
    """Raise ``RuntimeError`` when the error rate exceeds *threshold*.

    Parameters
    ----------
    errors:
        List of error messages / objects collected so far.
    total:
        Total number of items attempted.
    module_name:
        Name of the calling module (used in the error message).
    threshold:
        Fraction of failures that triggers the error (default 0.5 = 50%).
    """
    if total > 0 and len(errors) > total * threshold:
        raise RuntimeError(
            f"{module_name}: {len(errors)}/{total} items failed — "
            f"likely a systemic outage. First errors: {errors[:3]}"
        )


def format_metrics(metrics: dict[str, Any]) -> str:
    """Serialise *metrics* to an indented JSON string for prompt injection.

    Returns a placeholder string when *metrics* is empty or falsy.
    """
    if not metrics:
        return "No metrics provided."
    return json.dumps(metrics, indent=2, default=str)
