"""
LLM pipeline phases 2–4: letter generation, lesson extraction, threshold adjustments.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from boto3.dynamodb.conditions import Attr, Key

from shared.dynamo_client import DynamoClient
from shared.lessons_client import LessonsClient
from shared.llm_client import LLMClient
from shared.logger import get_logger
from shared.s3_client import S3Client

from .prompts import LESSON_EXTRACTION_PROMPT, OWNERS_LETTER_PROMPT

_log = get_logger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"

# Threshold adjustment cap
MAX_CHANGE_PCT_PER_QUARTER = 0.20

# Allowlist of parameter names that may be written to the config table.
# Any parameter not on this list is silently rejected to prevent LLM-invented
# keys from polluting the screening_thresholds config.
ALLOWED_THRESHOLD_PARAMS: frozenset[str] = frozenset(
    {
        # Quant screen thresholds (keys used in screening_thresholds config)
        "min_piotroski_f",
        "max_pb_ratio",
        "max_pe",
        "min_roic",
        "min_roic_avg",
        "min_current_ratio",
        "max_debt_equity",
        "min_market_cap",
        # Analysis quality gates
        "min_moat_score",
        "min_management_score",
        "min_margin_of_safety",
        # Portfolio risk parameters
        "max_position_pct",
        "max_sector_pct",
        "min_cash_reserve_pct",
        # Confidence calibration
        "confidence_calibration_factor",
    }
)


def _load_previous_lessons(lessons_client: LessonsClient) -> str:
    """Format active lessons for letter context."""
    from shared.lessons_client import HEADER

    now = datetime.now(UTC).isoformat()
    all_lessons = lessons_client._db.query(
        Key("active_flag").eq("1") & Key("expires_at").gt(now),
        index_name="active_flag-expires_at-index",
        filter_expression=Attr("lesson_type").is_in(
            ["moat_bias", "valuation_bias", "management_bias", "sector_bias"]
        ),
    )
    if not all_lessons:
        return "No previous lessons on record."
    lines = [HEADER, ""]
    for lesson in all_lessons[:15]:
        text = lesson.get("prompt_injection_text") or lesson.get("description", "")
        lines.append(f"- [{lesson.get('quarter', '?')}]: {text}")
    return "\n".join(lines)


def generate_letter(
    llm_client: LLMClient,
    s3_client: S3Client,
    quarter: str,
    audit_summary: dict[str, Any],
    decision_audit: list[dict[str, Any]],
    portfolio_summary: dict[str, Any],
    previous_lessons: str,
    year: int,
    q_num: int,
) -> tuple[str, str]:
    """Phase 2: Generate letter via LLM, store to S3."""
    template_path = PROMPTS_DIR / "owners_letter.md"
    if template_path.exists():
        template = template_path.read_text(encoding="utf-8")
    else:
        _log.error(
            "owners_letter.md template not found at %s — falling back to built-in prompt",
            template_path,
        )
        template = OWNERS_LETTER_PROMPT

    audit_text = json.dumps(decision_audit, indent=2, default=str)
    portfolio_text = json.dumps(portfolio_summary, indent=2, default=str)

    # Build prediction accuracy context if available
    pred_stats = audit_summary.get("prediction_stats", {})
    prediction_context = ""
    if pred_stats.get("total_predictions", 0) > 0:
        accuracy = pred_stats.get("accuracy")
        accuracy_str = f"{accuracy:.0%}" if accuracy is not None else "N/A"
        prediction_context = (
            f"\n\n## Prediction Accuracy\n"
            f"Total predictions evaluated: {pred_stats['total_predictions']}\n"
            f"Confirmed: {pred_stats['confirmed']}, "
            f"Falsified: {pred_stats['falsified']}\n"
            f"Accuracy: {accuracy_str}\n"
        )

    system_prompt = template.replace("{{quarter}}", quarter)
    system_prompt = system_prompt.replace("{{audit_summary}}", json.dumps(audit_summary, indent=2))
    system_prompt = system_prompt.replace("{{decision_audit}}", audit_text)
    system_prompt = system_prompt.replace("{{portfolio_summary}}", portfolio_text)
    system_prompt = system_prompt.replace(
        "{{previous_lessons}}", previous_lessons + prediction_context
    )

    user_prompt = f"Write the full Owner's Letter for {quarter}. Be brutally honest."

    response = llm_client.invoke(
        tier="analysis",
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        module="owners_letter",
        max_tokens=4096,
        temperature=0.3,
        require_json=False,
    )
    letter_md = response.get("content", "")
    if not isinstance(letter_md, str):
        letter_md = str(letter_md)

    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    letter_key = f"letters/{year}/Q{q_num}_{date_str}.md"
    s3_client.write_markdown(letter_key, letter_md)
    return letter_key, letter_md


def extract_lessons(
    llm_client: LLMClient,
    lessons_client: LessonsClient,
    letter_md: str,
    decision_audit: list[dict[str, Any]],
    year: int,
    quarter: int,
) -> list[dict[str, Any]]:
    """Phase 3: Extract structured lessons via LLM, store to DynamoDB."""
    template = LESSON_EXTRACTION_PROMPT

    system_prompt = template.replace("{{letter_text}}", letter_md)
    system_prompt = system_prompt.replace(
        "{{audit_json}}",
        json.dumps(decision_audit, indent=2, default=str),
    )

    user_prompt = f"Extract structured lessons from the Q{quarter} {year} Owner's Letter."

    response = llm_client.invoke(
        tier="analysis",
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        module="lesson_extraction",
        max_tokens=4096,
        temperature=0.2,
        require_json=True,
    )
    content = response.get("content", {})
    if not isinstance(content, dict):
        content = {}
    raw_lessons = content.get("lessons", [])
    if not isinstance(raw_lessons, list):
        raw_lessons = []

    q_label = f"Q{quarter}_{year}"
    now = datetime.now(UTC)
    created = now.isoformat()
    extracted: list[dict[str, Any]] = []

    for i, lesson in enumerate(raw_lessons):
        if not isinstance(lesson, dict):
            continue
        lesson_id = lesson.get("lesson_id") or f"{q_label}_{i + 1}"
        lesson_type = (lesson.get("lesson_type") or "process_improvement").lower()
        expiry_q = int(lesson.get("expiry_quarters", 8))
        expiry_q = max(4, min(12, expiry_q))
        expiry_date = now + timedelta(days=expiry_q * 91)
        expires_at = expiry_date.isoformat()

        item = {
            "lesson_type": lesson_type,
            "lesson_id": lesson_id,
            "severity": (lesson.get("severity") or "moderate").lower(),
            "description": lesson.get("description", ""),
            "actionable_rule": lesson.get("actionable_rule", ""),
            "prompt_injection_text": lesson.get("prompt_injection_text", ""),
            "ticker": lesson.get("ticker", ""),
            "sector": lesson.get("sector", "ALL"),
            "quarter": q_label,
            "created_at": created,
            "expires_at": expires_at,
            "expiry_quarters": expiry_q,
            "active": True,
            "active_flag": "1",
        }
        if lesson.get("threshold_adjustment"):
            item["threshold_adjustment"] = lesson["threshold_adjustment"]
        if lesson.get("confidence_calibration"):
            item["confidence_calibration"] = lesson["confidence_calibration"]

        extracted.append(item)

    if extracted:
        failed: list[dict[str, Any]] = []
        for lesson_item in extracted:
            try:
                lessons_client._db.put_item(lesson_item)
            except Exception as exc:
                failed.append(lesson_item)
                _log.error(
                    "Failed to write lesson to DynamoDB",
                    extra={
                        "lesson_id": lesson_item.get("lesson_id"),
                        "lesson_type": lesson_item.get("lesson_type"),
                        "error": str(exc),
                    },
                )
        if failed:
            _log.warning(
                "Partial lesson write: %d/%d failed",
                len(failed),
                len(extracted),
            )

    return extracted


def apply_threshold_adjustments(
    lessons: list[dict[str, Any]],
    config_client: DynamoClient,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Phase 4: Auto-apply minor severity, flag moderate+ for review."""
    auto_applied: list[dict[str, Any]] = []
    flagged: list[dict[str, Any]] = []

    config_item = config_client.get_item({"config_key": "screening_thresholds"})
    current = (config_item.get("value") or {}) if config_item else {}
    if not isinstance(current, dict):
        current = {}

    for lesson in lessons:
        severity = (lesson.get("severity") or "").lower()
        adj = lesson.get("threshold_adjustment") or {}
        if not adj or not isinstance(adj, dict):
            continue
        param = adj.get("parameter")
        proposed = adj.get("proposed_value")
        if param is None or proposed is None:
            continue

        # Reject any parameter not on the allowlist to prevent LLM-invented
        # keys from being written to the config table.
        if param not in ALLOWED_THRESHOLD_PARAMS:
            _log.warning(
                "Rejected unknown threshold parameter from LLM: %s",
                param,
            )
            continue

        old_val = current.get(param)
        if old_val is not None:
            try:
                old_f = float(old_val)
                new_f = float(proposed)
                change_pct = abs(new_f - old_f) / max(abs(old_f), 1e-9)
                if change_pct > MAX_CHANGE_PCT_PER_QUARTER:
                    new_f = (
                        old_f * (1 + MAX_CHANGE_PCT_PER_QUARTER)
                        if new_f > old_f
                        else old_f * (1 - MAX_CHANGE_PCT_PER_QUARTER)
                    )
            except (TypeError, ValueError):
                new_f = proposed
        else:
            new_f = float(proposed)

        if severity == "minor":
            current[param] = new_f
            auto_applied.append(
                {
                    "parameter": param,
                    "old_value": old_val,
                    "new_value": new_f,
                    "source": "post_mortem_auto",
                    "lesson_id": lesson.get("lesson_id"),
                }
            )
        else:
            flagged.append(
                {
                    "parameter": param,
                    "proposed_value": proposed,
                    "severity": severity,
                    "lesson_id": lesson.get("lesson_id"),
                }
            )

    if auto_applied:
        config_client.put_item(
            {
                "config_key": "screening_thresholds",
                "value": current,
            }
        )

    return auto_applied, flagged
