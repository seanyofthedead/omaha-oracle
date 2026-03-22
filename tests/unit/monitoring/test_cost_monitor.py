"""
Unit tests for cost monitor handler.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestCostMonitorHandler:
    """Tests for cost monitor handler."""

    def test_under_budget_no_alert(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_USD", "100.0")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")
        monkeypatch.setenv("SNS_TOPIC_ARN", "")

        with (
            patch("monitoring.cost_monitor.handler._get_llm_spend", return_value=10.0),
            patch("monitoring.cost_monitor.handler._get_aws_spend", return_value=5.0),
        ):
            from monitoring.cost_monitor.handler import handler

            result = handler({}, None)

        assert result["alert_triggered"] is False
        assert result["llm_spend"] == 10.0
        assert result["aws_spend"] == 5.0
        total = result["total_spend"]
        assert total == 15.0

    def test_over_budget_triggers_alert(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_USD", "100.0")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")
        monkeypatch.setenv("SNS_TOPIC_ARN", "arn:aws:sns:us-east-1:123456789012:alerts")

        mock_sns = MagicMock()

        with (
            patch("monitoring.cost_monitor.handler._get_llm_spend", return_value=60.0),
            patch("monitoring.cost_monitor.handler._get_aws_spend", return_value=25.0),
            patch("monitoring.cost_monitor.handler._sns_client", return_value=mock_sns),
        ):
            from monitoring.cost_monitor.handler import handler

            result = handler({}, None)

        assert result["alert_triggered"] is True
        assert result["utilization_pct"] > 80.0
        mock_sns.publish.assert_called_once()

    def test_over_budget_no_topic_arn_no_publish(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_USD", "100.0")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")
        monkeypatch.setenv("SNS_TOPIC_ARN", "")

        mock_sns = MagicMock()

        with (
            patch("monitoring.cost_monitor.handler._get_llm_spend", return_value=60.0),
            patch("monitoring.cost_monitor.handler._get_aws_spend", return_value=25.0),
            patch("monitoring.cost_monitor.handler._sns_client", return_value=mock_sns),
        ):
            from monitoring.cost_monitor.handler import handler

            result = handler({}, None)

        assert result["alert_triggered"] is True
        mock_sns.publish.assert_not_called()  # No topic ARN → no publish
