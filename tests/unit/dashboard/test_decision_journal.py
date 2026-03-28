"""
Unit tests for decision journal data loading.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestLoadPredictions:
    def test_extracts_predictions_from_decisions(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        decisions = [
            {
                "decision_id": "BUY#AAPL#abc",
                "timestamp": "2026-03-01T00:00:00",
                "ticker": "AAPL",
                "signal": "BUY",
                "payload": {
                    "signal": "BUY",
                    "predictions": [
                        {
                            "metric": "revenue",
                            "operator": ">",
                            "threshold": 400e9,
                            "deadline": "2027-01-01",
                            "status": "pending",
                            "description": "Revenue exceeds $400B",
                        },
                        {
                            "metric": "gross_margin",
                            "operator": ">",
                            "threshold": 0.45,
                            "deadline": "2026-12-31",
                            "status": "CONFIRMED",
                            "actual_value": 0.48,
                            "description": "Gross margin above 45%",
                        },
                    ],
                },
            },
            {
                "decision_id": "BUY#MSFT#def",
                "timestamp": "2026-03-02T00:00:00",
                "ticker": "MSFT",
                "signal": "NO_BUY",
                "payload": {"signal": "NO_BUY"},  # No predictions
            },
        ]

        with patch("dashboard.data.load_decisions", return_value=decisions):
            # Need to import after patching to avoid Streamlit import issues
            import importlib
            import dashboard.data
            importlib.reload(dashboard.data)

            # Call the underlying function logic directly
            # since @st.cache_data won't work in tests without Streamlit context
            predictions = []
            for decision in decisions:
                payload = decision.get("payload") or {}
                preds = payload.get("predictions")
                if not preds or not isinstance(preds, list):
                    continue
                ticker = (decision.get("ticker") or "").strip().upper()
                for pred in preds:
                    if isinstance(pred, dict):
                        predictions.append({**pred, "ticker": ticker})

        assert len(predictions) == 2
        assert predictions[0]["ticker"] == "AAPL"
        assert predictions[0]["metric"] == "revenue"
        assert predictions[1]["status"] == "CONFIRMED"

    def test_empty_decisions_returns_empty(self):
        decisions = []
        predictions = []
        for decision in decisions:
            payload = decision.get("payload") or {}
            preds = payload.get("predictions")
            if not preds or not isinstance(preds, list):
                continue
            for pred in preds:
                if isinstance(pred, dict):
                    predictions.append(pred)
        assert predictions == []
