"""
Regression tests for HIGH-severity monitoring bugs.

Bug 1: safe_float corrupts avg_moat_score — None moat_score appends 0.0 to bad_buy_moat_scores
Bug 2: sector_mistakes always "Unknown" — ignores actual sector from payload/item
Bug 3: Cost Explorer crash silences spend alert — exception propagates and kills Lambda
Bug 4: generate_letter falls back to empty prompt — missing template yields empty system prompt
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestBug1SafeFloatCorruptsMoatScore:
    """Bug 1: When moat_score is None, safe_float returns 0.0 which inflates bad_buy_moat_scores."""

    def test_none_moat_score_not_appended_to_bad_buy_scores(self):
        """A BAD_BUY with moat_score=None should NOT dilute a real score's average.

        Two BAD_BUYs: one with moat_score=7, one with moat_score=None.
        Correct avg = 7.0 (only one entry). Bug avg = 3.5 (two entries: 7.0, 0.0).
        """
        from monitoring.owners_letter.audit import run_outcome_audit

        decisions_client = MagicMock()
        decisions_client.query.return_value = [
            {
                "decision_id": "d1",
                "ticker": "FAIL1",
                "signal": "BUY",
                "timestamp": "2025-01-15T12:00:00+00:00",
                "payload": {"price_at_decision": 100.0, "moat_score": 7},
            },
            {
                "decision_id": "d2",
                "ticker": "FAIL2",
                "signal": "BUY",
                "timestamp": "2025-01-16T12:00:00+00:00",
                "payload": {"price_at_decision": 100.0, "moat_score": None},
            },
        ]
        with patch("monitoring.owners_letter.audit._fetch_price") as mock_fetch, \
             patch("monitoring.owners_letter.audit.yf"):
            mock_fetch.return_value = 75.0  # -25%, BAD_BUY
            audits, summary = run_outcome_audit(decisions_client, 2025, 1)

        # Should be 7.0 (only the valid score), not 3.5 (diluted by 0.0)
        assert summary["avg_moat_score_on_bad_buys"] == pytest.approx(7.0)

    def test_zero_moat_score_not_appended(self):
        """A BAD_BUY with moat_score=0 should NOT be appended (0 is not a valid score)."""
        from monitoring.owners_letter.audit import run_outcome_audit

        decisions_client = MagicMock()
        decisions_client.query.return_value = [
            {
                "decision_id": "d1",
                "ticker": "FAIL",
                "signal": "BUY",
                "timestamp": "2025-01-15T12:00:00+00:00",
                "payload": {"price_at_decision": 100.0, "moat_score": 0},
            },
        ]
        with patch("monitoring.owners_letter.audit._fetch_price") as mock_fetch, \
             patch("monitoring.owners_letter.audit.yf"):
            mock_fetch.return_value = 75.0
            audits, summary = run_outcome_audit(decisions_client, 2025, 1)

        assert summary["avg_moat_score_on_bad_buys"] == 0

    def test_valid_moat_score_still_appended(self):
        """A BAD_BUY with a real moat_score should still be counted."""
        from monitoring.owners_letter.audit import run_outcome_audit

        decisions_client = MagicMock()
        decisions_client.query.return_value = [
            {
                "decision_id": "d1",
                "ticker": "FAIL",
                "signal": "BUY",
                "timestamp": "2025-01-15T12:00:00+00:00",
                "payload": {"price_at_decision": 100.0, "moat_score": 6.5},
            },
        ]
        with patch("monitoring.owners_letter.audit._fetch_price") as mock_fetch, \
             patch("monitoring.owners_letter.audit.yf"):
            mock_fetch.return_value = 75.0
            audits, summary = run_outcome_audit(decisions_client, 2025, 1)

        assert summary["avg_moat_score_on_bad_buys"] == pytest.approx(6.5)


class TestBug2SectorMistakesAlwaysUnknown:
    """Bug 2: sector_mistakes hardcodes 'Unknown' instead of reading actual sector."""

    def test_sector_from_payload_used_in_mistakes(self):
        """sector_mistakes should use the sector from payload, not hardcode 'Unknown'."""
        from monitoring.owners_letter.audit import run_outcome_audit

        decisions_client = MagicMock()
        decisions_client.query.return_value = [
            {
                "decision_id": "d1",
                "ticker": "FAIL",
                "signal": "BUY",
                "timestamp": "2025-01-15T12:00:00+00:00",
                "payload": {
                    "price_at_decision": 100.0,
                    "moat_score": 5,
                    "sector": "Technology",
                },
            },
        ]
        with patch("monitoring.owners_letter.audit._fetch_price") as mock_fetch, \
             patch("monitoring.owners_letter.audit.yf"):
            mock_fetch.return_value = 75.0
            audits, summary = run_outcome_audit(decisions_client, 2025, 1)

        assert "Technology" in summary["sector_mistakes"]
        assert "Unknown" not in summary["sector_mistakes"]

    def test_sector_from_item_level_used_when_not_in_payload(self):
        """If sector is on the item (not payload), it should still be used."""
        from monitoring.owners_letter.audit import run_outcome_audit

        decisions_client = MagicMock()
        decisions_client.query.return_value = [
            {
                "decision_id": "d1",
                "ticker": "FAIL",
                "signal": "BUY",
                "timestamp": "2025-01-15T12:00:00+00:00",
                "sector": "Healthcare",
                "payload": {"price_at_decision": 100.0, "moat_score": 5},
            },
        ]
        with patch("monitoring.owners_letter.audit._fetch_price") as mock_fetch, \
             patch("monitoring.owners_letter.audit.yf"):
            mock_fetch.return_value = 75.0
            audits, summary = run_outcome_audit(decisions_client, 2025, 1)

        assert "Healthcare" in summary["sector_mistakes"]
        assert "Unknown" not in summary["sector_mistakes"]

    def test_sector_falls_back_to_unknown_when_absent(self):
        """When no sector anywhere, should fall back to 'Unknown'."""
        from monitoring.owners_letter.audit import run_outcome_audit

        decisions_client = MagicMock()
        decisions_client.query.return_value = [
            {
                "decision_id": "d1",
                "ticker": "FAIL",
                "signal": "BUY",
                "timestamp": "2025-01-15T12:00:00+00:00",
                "payload": {"price_at_decision": 100.0, "moat_score": 5},
            },
        ]
        with patch("monitoring.owners_letter.audit._fetch_price") as mock_fetch, \
             patch("monitoring.owners_letter.audit.yf"):
            mock_fetch.return_value = 75.0
            audits, summary = run_outcome_audit(decisions_client, 2025, 1)

        assert "Unknown" in summary["sector_mistakes"]


class TestBug3CostExplorerCrashSilencesAlert:
    """Bug 3: f_aws.result() propagates exception and kills Lambda."""

    def test_aws_spend_exception_does_not_crash_handler(self, monkeypatch):
        """When _get_aws_spend raises, handler should still return a result."""
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_USD", "100.0")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")
        monkeypatch.setenv("SNS_TOPIC_ARN", "")

        with (
            patch("monitoring.cost_monitor.handler._get_llm_spend", return_value=60.0),
            patch(
                "monitoring.cost_monitor.handler._get_aws_spend",
                side_effect=Exception("Cost Explorer unavailable"),
            ),
        ):
            from monitoring.cost_monitor.handler import handler

            result = handler({}, None)

        assert result["aws_spend"] == 0.0
        assert result["llm_spend"] == 60.0
        # LLM alert should still fire based on llm_spend alone
        assert "total_spend" in result

    def test_aws_spend_fallback_still_triggers_alert(self, monkeypatch):
        """Even with aws_spend=0 fallback, LLM spend alone can trigger alert."""
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_USD", "100.0")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")
        monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:alerts")

        mock_sns = MagicMock()

        with (
            patch("monitoring.cost_monitor.handler._get_llm_spend", return_value=90.0),
            patch(
                "monitoring.cost_monitor.handler._get_aws_spend",
                side_effect=Exception("Cost Explorer unavailable"),
            ),
            patch("monitoring.cost_monitor.handler._sns_client", return_value=mock_sns),
        ):
            from monitoring.cost_monitor.handler import handler

            result = handler({}, None)

        assert result["alert_triggered"] is True
        assert result["aws_spend"] == 0.0
        mock_sns.publish.assert_called_once()


class TestBug4GenerateLetterEmptyPromptFallback:
    """Bug 4: When template file is missing, template="" and LLM gets no instructions."""

    def test_missing_template_raises_or_uses_fallback(self):
        """generate_letter should raise FileNotFoundError or use OWNERS_LETTER_PROMPT constant."""
        from monitoring.owners_letter.pipeline import generate_letter
        from monitoring.owners_letter.prompts import OWNERS_LETTER_PROMPT

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = {"content": "# Letter\nTest letter content"}
        mock_s3 = MagicMock()

        # Patch PROMPTS_DIR to a nonexistent directory so template_path.exists() is False
        with patch(
            "monitoring.owners_letter.pipeline.PROMPTS_DIR",
            Path("/nonexistent/path"),
        ):
            letter_key, letter_md = generate_letter(
                llm_client=mock_llm,
                s3_client=mock_s3,
                quarter="Q1 2025",
                audit_summary={"total_decisions": 1, "mistake_rate": 0.5},
                decision_audit=[],
                portfolio_summary={},
                previous_lessons="No lessons",
                year=2025,
                q_num=1,
            )

        # The system_prompt passed to llm_client.invoke must contain the OWNERS_LETTER_PROMPT
        # content, not be empty
        call_kwargs = mock_llm.invoke.call_args
        system_prompt = call_kwargs.kwargs.get("system_prompt") or call_kwargs[1].get("system_prompt", "")
        assert len(system_prompt) > 100, "System prompt should not be empty when template is missing"
        assert "brutally honest" in system_prompt.lower() or "owner" in system_prompt.lower()
