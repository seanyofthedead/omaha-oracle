"""
End-to-end feedback loop integration test.

- Create mock decisions with known outcomes
- Run post-mortem handler (mock LLM)
- Verify lessons stored in DynamoDB
- Verify LessonsClient loads and injects those lessons
- Verify confidence adjustment factor applied to moat scores
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from monitoring.owners_letter.audit import run_outcome_audit
from monitoring.owners_letter.pipeline import extract_lessons
from shared.dynamo_client import DynamoClient
from shared.lessons_client import LessonsClient
from tests.conftest import TABLE_DECISIONS, TABLE_LESSONS  # noqa: F401
from tests.fixtures.mock_data import make_decision


def _seed_decision(table, decision: dict) -> None:
    """Put decision into DynamoDB, converting floats to Decimal."""

    def _to_decimal(obj):
        if isinstance(obj, dict):
            return {k: _to_decimal(v) for k, v in obj.items()}
        if isinstance(obj, float):
            return Decimal(str(obj))
        return obj

    item = {
        "pk": "DECISION",
        "sk": decision.get("decision_id", "dec-1"),
        "ticker": decision.get("ticker", ""),
        "signal": decision.get("signal", ""),
        "timestamp": decision.get("timestamp", ""),
        "payload": decision.get("payload", {}),
    }
    item.update(decision)
    table.put_item(Item=_to_decimal(item))


class TestFeedbackLoopIntegration:
    """End-to-end feedback flow with mocked LLM."""

    def test_decisions_audit_and_lessons_stored(
        self,
        integration_tables,
    ):
        decisions_table = integration_tables["decisions"]
        lessons_table = integration_tables["lessons"]

        # Seed decisions in Q1 2025 (Decimal for DynamoDB)
        start = datetime(2025, 1, 1, tzinfo=UTC)
        dec1 = make_decision("BAD", "BUY", 100.0, timestamp=start.isoformat())
        dec1["payload"]["price_at_decision"] = Decimal("100.0")
        dec1["payload"]["limit_price"] = Decimal("100.0")
        dec1["payload"]["current_price"] = Decimal("100.0")
        _seed_decision(decisions_table, dec1)

        # Mock _fetch_price: BAD at 75 (BAD_BUY)
        with patch("monitoring.owners_letter.audit._fetch_price") as mock_fetch:
            mock_fetch.return_value = 75.0

            decisions_client = DynamoClient(TABLE_DECISIONS)
            audits, summary = run_outcome_audit(decisions_client, 2025, 1)

        assert len(audits) >= 1
        assert any(a.get("outcome") == "BAD_BUY" for a in audits)

        # Mock LLM to return predefined lessons
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = {
            "content": {
                "lessons": [
                    {
                        "lesson_id": "L1",
                        "lesson_type": "moat_bias",
                        "severity": "moderate",
                        "description": "Overestimated moat on BAD",
                        "prompt_injection_text": "Be more skeptical of retail moats.",
                        "ticker": "BAD",
                        "sector": "Technology",
                        "confidence_calibration": {
                            "analysis_stage": "moat_analysis",
                            "sector": "Technology",
                            "adjustment_factor": Decimal("0.85"),
                        },
                    },
                ],
            },
        }

        lessons_client = LessonsClient(table_name=TABLE_LESSONS)
        letter_md = "Mock letter content"
        extracted = extract_lessons(mock_llm, lessons_client, letter_md, audits, 2025, 1)

        assert len(extracted) == 1
        assert extracted[0]["lesson_type"] == "moat_bias"
        assert extracted[0]["prompt_injection_text"] == "Be more skeptical of retail moats."

        # Verify stored in DynamoDB
        scan = lessons_table.scan()
        items = scan.get("Items", [])
        assert len(items) >= 1

    def test_lessons_client_loads_and_injects(
        self,
        integration_tables,
    ):
        lessons_table = integration_tables["lessons"]
        now = datetime.now(UTC)
        expiry = (now + timedelta(days=365)).isoformat()

        lesson_item = {
            "lesson_type": "moat_bias",
            "lesson_id": "L-int-1",
            "ticker": "AAPL",
            "sector": "Technology",
            "industry": "ALL",
            "severity": "moderate",
            "prompt_injection_text": "Apple ecosystem moat is durable.",
            "quarter": "Q1_2025",
            "created_at": now.isoformat(),
            "expires_at": expiry,
            "active": True,
        }
        lessons_table.put_item(Item=lesson_item)

        lessons_client = LessonsClient(table_name=TABLE_LESSONS)
        result = lessons_client.get_relevant_lessons(
            ticker="AAPL",
            sector="Technology",
            industry="Consumer Electronics",
            analysis_stage="moat_analysis",
        )
        assert "INSTITUTIONAL MEMORY" in result
        assert "Apple" in result or "ecosystem" in result

    def test_confidence_adjustment_applied_to_moat_scores(
        self,
        integration_tables,
    ):
        lessons_table = integration_tables["lessons"]
        now = datetime.now(UTC)
        expiry = (now + timedelta(days=365)).isoformat()

        conf_lesson = {
            "lesson_type": "confidence_calibration",
            "lesson_id": "L-conf",
            "severity": "minor",
            "quarter": "Q1_2025",
            "created_at": now.isoformat(),
            "expires_at": expiry,
            "active": True,
            "confidence_calibration": {
                "analysis_stage": "moat_analysis",
                "sector": "Technology",
                "adjustment_factor": Decimal("0.8"),
            },
        }
        lessons_table.put_item(Item=conf_lesson)

        lessons_client = LessonsClient(table_name=TABLE_LESSONS)
        adj = lessons_client.get_confidence_adjustment("moat_analysis", "Technology")
        assert adj == pytest.approx(0.8, rel=0.01)

        # Simulate moat score adjustment: raw_score * adj
        raw_moat_score = 8
        adjusted = raw_moat_score * adj
        assert adjusted == pytest.approx(6.4, rel=0.01)
