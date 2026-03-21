"""
Unit tests for post-mortem outcome audit.

- BAD_BUY when price drops >20%
- GOOD_SELL when price drops after sell
- MISSED_OPPORTUNITY when passed stock rises >30%
- CORRECT_PASS when passed stock stays flat or drops
- Audit summary statistics (mistake_rate, sector breakdown)
- Threshold adjustment safety: changes capped at 20%
- Auto-apply only for MINOR severity
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from monitoring.owners_letter.handler import (
    _apply_threshold_adjustments,
    _classify_outcome,
)


class TestClassifyOutcome:
    """Outcome classification."""

    def test_bad_buy_when_price_drops_over_20_pct(self):
        assert _classify_outcome("BUY", 100.0, 75.0) == "BAD_BUY"
        assert _classify_outcome("BUY", 100.0, 79.0) == "BAD_BUY"

    def test_good_sell_when_price_drops_after_sell(self):
        assert _classify_outcome("SELL", 100.0, 85.0) == "GOOD_SELL"
        assert _classify_outcome("SELL", 100.0, 88.0) == "GOOD_SELL"

    def test_missed_opportunity_when_passed_stock_rises_over_30_pct(self):
        assert _classify_outcome("NO_BUY", 100.0, 135.0) == "MISSED_OPPORTUNITY"
        assert _classify_outcome("NO_BUY", 100.0, 140.0) == "MISSED_OPPORTUNITY"

    def test_correct_pass_when_passed_stock_stays_flat_or_drops(self):
        assert _classify_outcome("NO_BUY", 100.0, 100.0) == "CORRECT_PASS"
        assert _classify_outcome("NO_BUY", 100.0, 95.0) == "CORRECT_PASS"
        assert _classify_outcome("NO_BUY", 100.0, 120.0) == "CORRECT_PASS"  # <30% rise

    def test_neutral_buy_in_range(self):
        assert _classify_outcome("BUY", 100.0, 110.0) == "NEUTRAL_BUY"

    def test_unknown_when_price_zero(self):
        assert _classify_outcome("BUY", 0.0, 100.0) == "UNKNOWN"


class TestApplyThresholdAdjustments:
    """Threshold adjustment safety and auto-apply."""

    def test_changes_capped_at_20_pct(self):
        config_client = MagicMock()
        config_client.get_item.return_value = {
            "config_key": "screening_thresholds",
            "value": {"pe_max": 15.0},
        }
        lessons = [
            {
                "severity": "minor",
                "threshold_adjustment": {"parameter": "pe_max", "proposed_value": 25.0},
                "lesson_id": "L1",
            },
        ]
        auto_applied, flagged = _apply_threshold_adjustments(lessons, config_client)
        assert len(auto_applied) == 1
        # 15 * 1.20 = 18 (capped), not 25
        assert auto_applied[0]["new_value"] == pytest.approx(18.0, rel=0.01)

    def test_auto_apply_only_for_minor_severity(self):
        config_client = MagicMock()
        config_client.get_item.return_value = {
            "config_key": "screening_thresholds",
            "value": {"pe_max": 15.0},
        }
        lessons_minor = [
            {
                "severity": "minor",
                "threshold_adjustment": {"parameter": "pe_max", "proposed_value": 14.0},
                "lesson_id": "L1",
            },
        ]
        lessons_moderate = [
            {
                "severity": "moderate",
                "threshold_adjustment": {"parameter": "pe_max", "proposed_value": 14.0},
                "lesson_id": "L2",
            },
        ]
        auto_minor, flagged_minor = _apply_threshold_adjustments(lessons_minor, config_client)
        auto_mod, flagged_mod = _apply_threshold_adjustments(lessons_moderate, config_client)
        assert len(auto_minor) == 1
        assert len(flagged_mod) == 1
        assert len(auto_mod) == 0

    def test_moderate_severity_flagged_not_auto_applied(self):
        config_client = MagicMock()
        config_client.get_item.return_value = {"config_key": "screening_thresholds", "value": {}}
        lessons = [
            {
                "severity": "moderate",
                "threshold_adjustment": {"parameter": "roic_min", "proposed_value": 15},
                "lesson_id": "L1",
            },
        ]
        auto_applied, flagged = _apply_threshold_adjustments(lessons, config_client)
        assert len(auto_applied) == 0
        assert len(flagged) == 1
        assert flagged[0]["severity"] == "moderate"


class TestAuditSummary:
    """Audit summary statistics."""

    def test_audit_summary_mistake_rate_and_sector(self):
        from monitoring.owners_letter.handler import _run_outcome_audit

        decisions_client = MagicMock()
        decisions_client.query.return_value = [
            {
                "decision_id": "d1",
                "ticker": "BAD",
                "signal": "BUY",
                "timestamp": "2025-01-15T12:00:00+00:00",
                "payload": {"price_at_decision": 100.0, "moat_score": 7},
            },
        ]
        with patch("monitoring.owners_letter.audit._fetch_price") as mock_fetch:
            mock_fetch.side_effect = lambda t, d=None: 75.0 if t == "BAD" else 0.0
            audits, summary = _run_outcome_audit(decisions_client, 2025, 1)
        assert summary["total_decisions"] == 1
        assert summary["mistake_rate"] == 1.0
        assert "sector_mistakes" in summary
