"""
Lambda handler that merges sfn.Parallel branch outputs into a single dict.

Receives a list of dicts (one per parallel branch) and flattens them
into a single dict via sequential update. Used between the Parallel
(mgmt + IV) block and the MoS gate / thesis generator.
"""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)


def handler(event: Any, context: Any) -> dict[str, Any]:
    """
    Merge parallel branch outputs.

    Input:  [{"ticker": ..., "management_score": ...}, {"ticker": ..., "margin_of_safety": ...}]
    Output: {"ticker": ..., "management_score": ..., "margin_of_safety": ...}

    Both branches pass through moat-era keys (reasoning, confidence, skipped,
    filing_context_degraded, cost_usd) via dict(event). Management overwrites
    these with its own values; IV does not. To preserve management context for
    downstream consumers (thesis generator), IV is applied first and management
    second so management's values win on overlapping keys.
    """
    if not isinstance(event, list):
        _log.warning(
            "merge_results received non-list event (type=%s), returning as-is",
            type(event).__name__,
        )
        return event  # type: ignore[return-value]

    # Sort: IV branch first, management branch second. Management is identified
    # by the presence of "management_score"; IV by "margin_of_safety".
    # This ensures management's reasoning/confidence/cost_usd win on overlap,
    # matching the old sequential flow where IV inherited management's values.
    iv_branches = []
    mgmt_branches = []
    other_branches = []
    for branch in event:
        if "management_score" in branch:
            mgmt_branches.append(branch)
        elif "margin_of_safety" in branch:
            iv_branches.append(branch)
        else:
            other_branches.append(branch)

    merged: dict[str, Any] = {}
    for branch_output in iv_branches + other_branches + mgmt_branches:
        merged.update(branch_output)
    return merged
