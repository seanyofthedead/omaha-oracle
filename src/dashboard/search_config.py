"""Search configuration and quality gate logic for company search."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Quality gate thresholds
# ---------------------------------------------------------------------------

MOAT_MIN = 7
MANAGEMENT_MIN = 6
MOS_MIN = 0.30  # strictly greater than

MAX_EVALUATIONS = 200
SCREENER_MAX_RESULTS = 500


# ---------------------------------------------------------------------------
# Search configuration
# ---------------------------------------------------------------------------


class SearchConfig(BaseModel):
    """User-configurable search parameters."""

    num_results: int = Field(default=3, ge=1, le=10)
    time_limit_minutes: int = Field(default=15, ge=5, le=60)


# ---------------------------------------------------------------------------
# Quality gates
# ---------------------------------------------------------------------------


def check_quality_gates(result: dict[str, Any]) -> tuple[bool, dict[str, bool]]:
    """Check whether a pipeline result passes all quality gates.

    Returns (all_passed, {"moat": bool, "management": bool, "mos": bool}).
    """
    moat = result.get("moat_score", 0) >= MOAT_MIN
    management = result.get("management_score", 0) >= MANAGEMENT_MIN
    mos = result.get("margin_of_safety", 0) > MOS_MIN

    details = {"moat": moat, "management": management, "mos": mos}
    return (all(details.values()), details)


def count_gates_passed(gate_details: dict[str, bool]) -> int:
    """Count how many quality gates passed."""
    return sum(1 for v in gate_details.values() if v)
