"""
Manual test for Owner's Letter & Post-Mortem engine.

Uses mock data only — NO AWS calls. Run from project root:
  python tests\\manual\\test_postmortem.py
"""

# ruff: noqa: E501
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

# Add src to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

# Mock decisions — 4 decisions for Q1 2026
MOCK_DECISIONS = [
    {
        "decision_id": "BUY#FAKECO#abc123",
        "timestamp": "2026-01-15T14:00:00+00:00",
        "decision_type": "BUY",
        "ticker": "FAKECO",
        "signal": "BUY",
        "payload": {
            "signal": "BUY",
            "reasons_pass": ["MoS 35%", "Moat 8/10", "Mgmt 7/10"],
            "price_at_decision": 50.00,
            "moat_score": 8,
            "margin_of_safety": 0.35,
            "management_score": 7,
        },
    },
    {
        "decision_id": "BUY#WINNER#def456",
        "timestamp": "2026-01-20T14:00:00+00:00",
        "decision_type": "BUY",
        "ticker": "WINNER",
        "signal": "BUY",
        "payload": {
            "signal": "BUY",
            "reasons_pass": ["MoS 40%", "Moat 9/10", "Mgmt 8/10"],
            "price_at_decision": 100.00,
            "moat_score": 9,
            "margin_of_safety": 0.40,
            "management_score": 8,
        },
    },
    {
        "decision_id": "BUY#MISSEDCO#ghi789",
        "timestamp": "2026-02-01T14:00:00+00:00",
        "decision_type": "BUY",
        "ticker": "MISSEDCO",
        "signal": "NO_BUY",
        "payload": {
            "signal": "NO_BUY",
            "reasons_fail": ["Moat 6/10 < 7"],
            "price_at_decision": 30.00,
            "moat_score": 6,
            "margin_of_safety": 0.28,
        },
    },
    {
        "decision_id": "BUY#AVOIDCO#jkl012",
        "timestamp": "2026-02-15T14:00:00+00:00",
        "decision_type": "BUY",
        "ticker": "AVOIDCO",
        "signal": "NO_BUY",
        "payload": {
            "signal": "NO_BUY",
            "reasons_fail": ["Moat 4/10 < 7", "MoS 15% < 30%"],
            "price_at_decision": 80.00,
            "moat_score": 4,
            "margin_of_safety": 0.15,
        },
    },
]

MOCK_ACCOUNT = {
    "pk": "ACCOUNT",
    "sk": "SUMMARY",
    "cash_available": 25000,
    "portfolio_value": 100000,
}

MOCK_POSITIONS = [
    {
        "pk": "POSITION",
        "sk": "FAKECO",
        "ticker": "FAKECO",
        "shares": 200,
        "cost_basis": 10000,
        "market_value": 7000,
    },
    {
        "pk": "POSITION",
        "sk": "WINNER",
        "ticker": "WINNER",
        "shares": 50,
        "cost_basis": 5000,
        "market_value": 6250,
    },
]

MOCK_CONFIG = {
    "screening_thresholds": {
        "value": {"min_moat_score": 7, "mos_min": 0.30},
    },
}

MOCK_LETTER = """# Q1 2026 Owner's Letter

This was a mixed quarter. FAKECO was a mistake — we overrated the moat in Technology. WINNER validated our process. We missed MISSEDCO because our moat threshold was too strict for Healthcare. AVOIDCO was a correct pass.

## Mistakes
FAKECO: We confused high market share with structural moat. The moat score of 8 was too generous.

## Lessons
1. Technology moats based on market share alone are fragile
2. Consider lowering moat threshold for Healthcare where regulatory barriers provide partial protection"""

MOCK_LESSONS = {
    "lessons": [
        {
            "lesson_id": "Q1_2026_1",
            "lesson_type": "moat_bias",
            "severity": "moderate",
            "ticker": "FAKECO",
            "sector": "Technology",
            "description": "Overrated FAKECO moat — confused market share with structural defensibility",
            "actionable_rule": "For Technology companies, verify switching costs exist independent of market share before scoring moat above 6",
            "prompt_injection_text": "HISTORICAL LESSON: In Q1 2026, we scored FAKECO moat at 8 based on high market share, but the moat eroded when a competitor launched. In Technology, always verify switching costs exist independent of market position.",
            "threshold_adjustment": {
                "parameter": None,
                "current_value": None,
                "proposed_value": None,
                "scope": "sector:Technology",
                "reasoning": "N/A",
            },
            "confidence_calibration": {
                "analysis_stage": "moat_analysis",
                "bias_direction": "overconfident",
                "adjustment_factor": 0.85,
                "reasoning": "Tech moat scores have been ~15% too high",
            },
            "expiry_quarters": 8,
        },
        {
            "lesson_id": "Q1_2026_2",
            "lesson_type": "threshold_adjustment",
            "severity": "minor",
            "ticker": "MISSEDCO",
            "sector": "Healthcare",
            "description": "Missed MISSEDCO because moat threshold too strict for Healthcare where regulatory barriers help",
            "actionable_rule": "For Healthcare companies with regulatory barriers, consider moat score of 6 as passing threshold instead of 7",
            "prompt_injection_text": "HISTORICAL LESSON: We passed on MISSEDCO (Healthcare) because moat was 6, but regulatory barriers provided more protection than our score reflected. Healthcare moats may deserve slightly lower thresholds.",
            "threshold_adjustment": {
                "parameter": "min_moat_score",
                "current_value": 7,
                "proposed_value": 6,
                "scope": "sector:Healthcare",
                "reasoning": "Regulatory barriers undervalued in moat scoring",
            },
            "confidence_calibration": {
                "analysis_stage": "moat_analysis",
                "bias_direction": "underconfident",
                "adjustment_factor": 1.10,
                "reasoning": "Healthcare moat scores slightly too conservative",
            },
            "expiry_quarters": 6,
        },
    ],
}

CURRENT_PRICES = {"FAKECO": 35.0, "WINNER": 125.0, "MISSEDCO": 45.0, "AVOIDCO": 60.0}


class MockDynamoClient:
    """Mock DynamoDB client that returns canned data based on table name."""

    def __init__(self, table_name: str):
        self._table_name = table_name

    def scan_all(self, filter_expression=None, **kwargs):
        if "decisions" in self._table_name:
            return MOCK_DECISIONS
        if "lessons" in self._table_name:
            return []
        return []

    def get_item(self, key):
        if "portfolio" in self._table_name and key.get("pk") == "ACCOUNT":
            return MOCK_ACCOUNT
        if "config" in self._table_name:
            cfg = MOCK_CONFIG.get(key.get("config_key"))
            return cfg if cfg else None
        return None

    def query(self, key_condition, limit=None, filter_expression=None, **kwargs):
        if "portfolio" in self._table_name:
            return MOCK_POSITIONS
        if "lessons" in self._table_name:
            return []
        return []

    def put_item(self, item):
        print("[DynamoDB put_item captured]")
        print(json.dumps(item, indent=2, default=str))
        print()

    def update_item(self, key, update_expression=None, expression_attribute_values=None, **kwargs):
        pass


_captured_s3 = None


class MockS3Client:
    """Mock S3 client that captures writes."""

    def __init__(self, bucket=None):
        global _captured_s3
        _captured_s3 = self
        self.last_markdown = None
        self.last_markdown_key = None
        self.last_json = None
        self.last_json_key = None

    def write_markdown(self, key: str, text: str):
        self.last_markdown_key = key
        self.last_markdown = text
        print("[S3 write_markdown captured]", key)
        print(text[:500] + "..." if len(text) > 500 else text)
        print()

    def write_json(self, key: str, data):
        self.last_json_key = key
        self.last_json = data
        print("[S3 write_json captured]", key)
        print(
            json.dumps(data, indent=2, default=str)[:1000] + "..."
            if len(json.dumps(data)) > 1000
            else json.dumps(data, indent=2, default=str)
        )
        print()


class MockLLMClient:
    """Mock LLM client that returns canned responses."""

    def __init__(self, cost_tracker=None):
        self._call_count = 0

    def invoke(
        self,
        tier=None,
        user_prompt=None,
        system_prompt=None,
        module=None,
        max_tokens=None,
        temperature=None,
        require_json=None,
    ):
        self._call_count += 1
        if self._call_count == 1:
            return {
                "content": MOCK_LETTER,
                "model": "claude-sonnet-4-20250514",
                "input_tokens": 5000,
                "output_tokens": 2000,
                "cost_usd": 0.04,
            }
        return {
            "content": MOCK_LESSONS,
            "model": "claude-sonnet-4-20250514",
            "input_tokens": 6000,
            "output_tokens": 3000,
            "cost_usd": 0.06,
        }


def mock_fetch_price(ticker: str, date=None) -> float:
    """Mock price fetcher — returns canned current prices."""
    return CURRENT_PRICES.get((ticker or "").upper(), 0.0)


def main():
    from shared.config import get_config

    cfg = get_config()
    cfg.table_decisions = "omaha-oracle-dev-decisions"
    cfg.table_portfolio = "omaha-oracle-dev-portfolio"
    cfg.table_config = "omaha-oracle-dev-config"
    cfg.table_lessons = "omaha-oracle-dev-lessons"

    with (
        patch("shared.dynamo_client.DynamoClient", MockDynamoClient),
        patch("monitoring.owners_letter.handler.S3Client", MockS3Client),
        patch("monitoring.owners_letter.handler.LLMClient", MockLLMClient),
        patch("monitoring.owners_letter.handler._fetch_price", mock_fetch_price),
        patch("shared.lessons_client.get_config", return_value=cfg),
        patch("shared.config.get_config", return_value=cfg),
    ):
        from monitoring.owners_letter.handler import handler

        event = {"year": 2026, "quarter": 1}
        result = handler(event, None)

    postmortem = _captured_s3.last_json if _captured_s3 else {}
    decision_audit = postmortem.get("decision_audit", [])
    lessons_extracted = postmortem.get("lessons_extracted", [])
    letter_md = _captured_s3.last_markdown if _captured_s3 else MOCK_LETTER

    print("\n" + "=" * 60)
    print("=== PHASE 1: OUTCOME AUDIT ===")
    print("=" * 60)
    for d in decision_audit:
        print(
            f"  {d.get('ticker')}: {d.get('signal')} @ ${d.get('price_at_decision')} -> ${d.get('current_price')} => {d.get('outcome')}"
        )

    print("\n" + "=" * 60)
    print("=== PHASE 2: LETTER ===")
    print("=" * 60)
    print(letter_md)

    print("\n" + "=" * 60)
    print("=== PHASE 3: LESSONS EXTRACTED ===")
    print("=" * 60)
    for i, lesson in enumerate(lessons_extracted, 1):
        print(
            f"Lesson {i}: {lesson.get('lesson_id')} ({lesson.get('lesson_type')}, {lesson.get('severity')})"
        )
        print(f"  Ticker: {lesson.get('ticker')}, Sector: {lesson.get('sector')}")
        print(f"  Description: {lesson.get('description')}")
        print(f"  Actionable: {lesson.get('actionable_rule')}")
        print(f"  Prompt injection: {(lesson.get('prompt_injection_text') or '')[:120]}...")
        if lesson.get("threshold_adjustment"):
            ta = lesson["threshold_adjustment"]
            print(
                f"  Threshold: {ta.get('parameter')} -> {ta.get('proposed_value')} (scope: {ta.get('scope')})"
            )
        print()

    print("=" * 60)
    print("=== PHASE 4: THRESHOLD ADJUSTMENTS ===")
    print("=" * 60)
    print("Auto-applied:", result.get("auto_applied", []))
    print("All adjustments:", result.get("threshold_adjustments", []))

    print("\n" + "=" * 60)
    print("=== FINAL RESULT ===")
    print("=" * 60)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
