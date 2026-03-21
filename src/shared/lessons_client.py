"""
LessonsClient — feedback loop glue connecting post-mortems to future analysis.

Queries lessons DynamoDB table, scores by relevance, formats for prompt injection.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from boto3.dynamodb.conditions import Attr, Key

from shared.config import get_config
from shared.dynamo_client import DynamoClient
from shared.logger import get_logger

_log = get_logger(__name__)

HEADER = "INSTITUTIONAL MEMORY — LESSONS FROM PAST MISTAKES"

# Relevance scoring
SCORE_TICKER = 100
SCORE_SECTOR = 50
SCORE_INDUSTRY = 40
SCORE_STAGE = 30
SEVERITY_BOOST: dict[str, int] = {"critical": 25, "high": 15, "moderate": 5, "minor": 0}
RECENCY_QUARTERS_MAX = 8  # Max quarters for recency boost

# Confidence adjustment bounds
ADJUSTMENT_MIN = 0.5
ADJUSTMENT_MAX = 1.5


def _stage_to_lesson_types(stage: str) -> list[str]:
    """Map analysis stage to relevant lesson_type values."""
    mapping: dict[str, list[str]] = {
        "moat_analysis": ["moat_bias", "sector_bias"],
        "management_quality": ["management_bias"],
        "intrinsic_value": ["valuation_bias", "threshold_adjustment"],
        "thesis_generator": ["moat_bias", "valuation_bias", "management_bias", "sector_bias"],
    }
    return mapping.get(stage, [])


class LessonsClient:
    """
    Feedback loop glue: loads lessons from post-mortems for prompt injection.
    """

    def __init__(self, table_name: str | None = None) -> None:
        """Initialize the client, defaulting to the configured lessons table."""
        cfg = get_config()
        self._table_name = table_name or cfg.table_lessons
        self._db = DynamoClient(self._table_name)

    def get_relevant_lessons(
        self,
        ticker: str,
        sector: str,
        industry: str,
        analysis_stage: str,
        max_lessons: int = 5,
    ) -> str:
        """
        Retrieve and format lessons relevant to the current analysis context.

        Filters for active=True and expires_at > now. Scores by relevance:
        ticker (+100), sector (+50), industry (+40), stage (+30), severity, recency.

        Returns formatted string for prompt injection, or empty string if none.
        """
        now = datetime.now(UTC).isoformat()
        stage_types = _stage_to_lesson_types(analysis_stage)
        if not stage_types:
            return ""

        all_lessons = self._db.query(
            Key("active_flag").eq("1") & Key("expires_at").gt(now),
            index_name="active_flag-expires_at-index",
            filter_expression=Attr("lesson_type").is_in(stage_types),
        )

        # Filter and score
        scored: list[tuple[int, dict[str, Any]]] = []
        ticker_upper = (ticker or "").strip().upper()
        sector_lower = (sector or "").strip().lower()
        industry_lower = (industry or "").strip().lower()

        for lesson in all_lessons:
            score = 0

            lesson_ticker = (lesson.get("ticker") or "").strip().upper()
            if lesson_ticker and lesson_ticker == ticker_upper:
                score += SCORE_TICKER

            lesson_sector = (lesson.get("sector") or "ALL").strip().lower()
            if lesson_sector != "all" and lesson_sector == sector_lower:
                score += SCORE_SECTOR

            lesson_industry = (lesson.get("industry") or "ALL").strip().lower()
            if lesson_industry != "all" and lesson_industry == industry_lower:
                score += SCORE_INDUSTRY

            lesson_stage = (lesson.get("confidence_calibration", {}) or {}).get(
                "analysis_stage", ""
            )
            scope = (lesson.get("threshold_adjustment", {}) or {}).get("scope", "")
            if lesson_stage == analysis_stage or (scope and analysis_stage in scope.lower()):
                score += SCORE_STAGE

            severity = (lesson.get("severity") or "minor").lower()
            score += SEVERITY_BOOST.get(severity, 0)

            # Recency boost (newer = higher)
            quarter = lesson.get("quarter", "")
            if quarter:
                try:
                    parts = quarter.replace("Q", "").split("_")
                    if len(parts) >= 2:
                        q_num = int(parts[0])
                        year = int(parts[1])
                        now_dt = datetime.now(UTC)
                        quarters_ago = (now_dt.year - year) * 4 + (
                            (now_dt.month - 1) // 3 + 1 - q_num
                        )
                        if quarters_ago >= 0 and quarters_ago < RECENCY_QUARTERS_MAX:
                            score += max(0, RECENCY_QUARTERS_MAX - quarters_ago)
                except (ValueError, IndexError):
                    _log.debug(
                        "Could not parse quarter for recency score",
                        extra={"quarter": quarter},
                    )

            if score > 0:
                scored.append((score, lesson))

        scored.sort(key=lambda x: -x[0])
        top = [lesson for _, lesson in scored[:max_lessons]]

        if not top:
            return ""

        return self._format_for_injection(top)

    def get_confidence_adjustment(
        self,
        analysis_stage: str,
        sector: str = "",
    ) -> float:
        """
        Compute geometric mean of adjustment_factor from confidence_calibration
        lessons matching stage and sector. Clamped to [0.5, 1.5].
        """
        now = datetime.now(UTC).isoformat()
        factors: list[float] = []

        items = self._db.query(
            Key("lesson_type").eq("confidence_calibration"),
            filter_expression=Attr("active").eq(True) & Attr("expires_at").gt(now),
        )

        sector_lower = (sector or "").strip().lower()
        for lesson in items:
            cal = lesson.get("confidence_calibration", {}) or {}
            if not cal:
                continue
            stage = (cal.get("analysis_stage") or "").strip()
            if stage != analysis_stage:
                continue
            lesson_sector = (cal.get("sector") or lesson.get("sector") or "ALL").strip().lower()
            if lesson_sector != "all" and lesson_sector != sector_lower:
                continue
            factor = cal.get("adjustment_factor")
            if factor is not None:
                try:
                    f = float(factor)
                    if f > 0:
                        factors.append(f)
                except (TypeError, ValueError):
                    pass

        if not factors:
            return 1.0

        geo_mean = math.exp(sum(math.log(f) for f in factors) / len(factors))
        return max(ADJUSTMENT_MIN, min(ADJUSTMENT_MAX, geo_mean))

    def expire_stale_lessons(self) -> int:
        """
        Mark lessons with expires_at < now as active=False.
        Called by post-mortem handler at start of each quarterly run.
        """
        now = datetime.now(UTC).isoformat()
        expired = 0

        items = self._db.query(
            Key("active_flag").eq("1") & Key("expires_at").lt(now),
            index_name="active_flag-expires_at-index",
        )

        if items:
            for lesson in items:
                lesson["active"] = False
                lesson["active_flag"] = "0"
            try:
                self._db.batch_write(items)
                expired = len(items)
            except Exception as exc:
                _log.warning(
                    "Failed to batch-expire lessons",
                    extra={"count": len(items), "error": str(exc)},
                )

        if expired:
            _log.info("Expired stale lessons", extra={"count": expired})
        return expired

    def _format_for_injection(self, lessons: list[dict[str, Any]]) -> str:
        """Format lessons into prompt-ready context block."""
        lines = [HEADER, ""]
        for i, lesson in enumerate(lessons, 1):
            text = lesson.get("prompt_injection_text") or lesson.get("description", "")
            severity = (lesson.get("severity") or "unknown").upper()
            source = lesson.get("quarter", "unknown")
            lines.append(f"- [{source}] ({severity}): {text}")
        return "\n".join(lines)
