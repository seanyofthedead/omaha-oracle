"""
Unit tests for prediction summary integration in outcome audit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from monitoring.owners_letter.audit import run_outcome_audit


class TestAuditPredictionSummary:
    def _make_decision(self, ticker, signal, predictions=None):
        ts = datetime.now(UTC).isoformat()
        payload = {"signal": signal, "price_at_decision": 150.0}
        if predictions is not None:
            payload["predictions"] = predictions
        return {
            "decision_id": f"BUY#{ticker}#abc",
            "timestamp": ts,
            "record_type": "DECISION",
            "decision_type": "BUY",
            "ticker": ticker,
            "signal": signal,
            "payload": payload,
        }

    def test_prediction_summary_included_in_audit(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")

        predictions = [
            {"status": "CONFIRMED", "metric": "revenue"},
            {"status": "FALSIFIED", "metric": "gross_margin"},
            {"status": "pending", "metric": "stock_price"},
        ]
        decision = self._make_decision("AAPL", "BUY", predictions)

        mock_client = MagicMock()
        mock_client.query.return_value = [decision]

        with patch("monitoring.owners_letter.audit.yf") as mock_yf:
            mock_yf.download.return_value = MagicMock(
                empty=True, columns=[], __contains__=lambda s, k: False
            )
            mock_yf.Ticker.return_value.info = {"currentPrice": 160.0}

            audits, summary = run_outcome_audit(mock_client, 2026, 1)

        assert len(audits) == 1
        ps = audits[0]["prediction_summary"]
        assert ps is not None
        assert ps["total"] == 3
        assert ps["confirmed"] == 1
        assert ps["falsified"] == 1
        assert ps["pending"] == 1

        # Summary includes prediction stats
        assert summary["prediction_stats"]["total_predictions"] == 3
        assert summary["prediction_stats"]["confirmed"] == 1
        assert summary["prediction_stats"]["accuracy"] == 0.5

    def test_no_predictions_summary_is_none(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")

        decision = self._make_decision("MSFT", "BUY")  # No predictions

        mock_client = MagicMock()
        mock_client.query.return_value = [decision]

        with patch("monitoring.owners_letter.audit.yf") as mock_yf:
            mock_yf.download.return_value = MagicMock(
                empty=True, columns=[], __contains__=lambda s, k: False
            )
            mock_yf.Ticker.return_value.info = {"currentPrice": 160.0}

            audits, summary = run_outcome_audit(mock_client, 2026, 1)

        assert audits[0]["prediction_summary"] is None
        assert summary["prediction_stats"]["total_predictions"] == 0
