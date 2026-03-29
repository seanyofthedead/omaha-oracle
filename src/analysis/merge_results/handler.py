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
    """
    if not isinstance(event, list):
        _log.warning(
            "merge_results received non-list event (type=%s), returning as-is",
            type(event).__name__,
        )
        return event  # type: ignore[return-value]

    merged: dict[str, Any] = {}
    for branch_output in event:
        merged.update(branch_output)
    return merged
