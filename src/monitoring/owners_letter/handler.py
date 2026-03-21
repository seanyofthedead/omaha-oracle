"""
Quarterly post-mortem engine — 5 phases.

Phase 1: Outcome audit (decisions vs prices)
Phase 2: Letter generation (LLM, S3)
Phase 3: Lesson extraction (LLM, DynamoDB)
Phase 4: Threshold adjustment (config table)
Phase 5: Downstream — analysis handlers inject lessons (LessonsClient)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from shared.config import get_config
from shared.dynamo_client import DynamoClient
from shared.lessons_client import LessonsClient
from shared.llm_client import LLMClient
from shared.logger import get_logger
from shared.portfolio_helpers import load_portfolio_state
from shared.s3_client import S3Client

from .audit import (
    run_outcome_audit,
)
from .pipeline import (
    _load_previous_lessons,
    apply_threshold_adjustments,
    extract_lessons,
    generate_letter,
)

# Backwards-compatible aliases for tests that import private names
_apply_threshold_adjustments = apply_threshold_adjustments
_run_outcome_audit = run_outcome_audit

_log = get_logger(__name__)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entry point.

    Input:
        year, quarter: optional override (default: current quarter)

    Output:
        letter_key, postmortem_key, decisions_audited, lessons_extracted,
        threshold_adjustments, auto_applied
    """
    cfg = get_config()
    now = datetime.now(UTC)
    year = int(event.get("year", now.year))
    quarter = int(event.get("quarter", (now.month - 1) // 3 + 1))
    q_label = f"Q{quarter}_{year}"

    decisions_client = DynamoClient(cfg.table_decisions)
    config_client = DynamoClient(cfg.table_config)
    lessons_client = LessonsClient()
    llm_client = LLMClient()
    s3_client = S3Client()

    lessons_client.expire_stale_lessons()

    decision_audit, audit_summary = run_outcome_audit(decisions_client, year, quarter)
    portfolio_summary = load_portfolio_state(cfg.table_portfolio)
    previous_lessons = _load_previous_lessons(lessons_client)

    letter_key, letter_md = generate_letter(
        llm_client,
        s3_client,
        q_label,
        audit_summary,
        decision_audit,
        portfolio_summary,
        previous_lessons,
        year,
        quarter,
    )

    extracted_lessons = extract_lessons(
        llm_client,
        lessons_client,
        letter_md,
        decision_audit,
        year,
        quarter,
    )

    auto_applied, flagged = apply_threshold_adjustments(extracted_lessons, config_client)

    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    postmortem_key = f"postmortems/{year}/Q{quarter}_{date_str}.json"
    postmortem = {
        "quarter": q_label,
        "date": date_str,
        "audit_summary": audit_summary,
        "decision_audit": decision_audit,
        "lessons_extracted": extracted_lessons,
        "auto_applied": auto_applied,
        "flagged_for_review": flagged,
    }
    s3_client.write_json(postmortem_key, postmortem)

    return {
        "letter_key": letter_key,
        "postmortem_key": postmortem_key,
        "decisions_audited": len(decision_audit),
        "lessons_extracted": len(extracted_lessons),
        "threshold_adjustments": auto_applied + flagged,
        "auto_applied": auto_applied,
    }
