"""
Unit tests for Yahoo Finance ingestion handler.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestBatchPrices:
    """Tests for action=batch_prices."""

    def test_happy_path_writes_to_dynamo(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        fake_info = {
            "currentPrice": 150.0,
            "trailingPE": 25.0,
            "marketCap": 3_000_000_000_000,
            "sector": "Technology",
        }

        with (
            patch("ingestion.yahoo_finance.handler.get_watchlist_tickers", return_value=["AAPL"]),
            patch("ingestion.yahoo_finance.handler.yf.Ticker") as mock_ticker_cls,
            patch("ingestion.yahoo_finance.handler.DynamoClient") as mock_dynamo_cls,
            patch("ingestion.yahoo_finance.handler.S3Client"),
        ):
            mock_t = MagicMock()
            mock_t.info = fake_info
            mock_ticker_cls.return_value = mock_t

            mock_dynamo = MagicMock()
            mock_dynamo_cls.return_value = mock_dynamo

            from ingestion.yahoo_finance.handler import handler

            result = handler({"action": "batch_prices"}, None)

        assert result["status"] == "ok"
        assert result["processed"] == 1
        assert result["errors"] == []
        mock_dynamo.put_item.assert_called_once()

    def test_empty_watchlist_returns_ok(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        with (
            patch("ingestion.yahoo_finance.handler.get_watchlist_tickers", return_value=[]),
            patch("ingestion.yahoo_finance.handler.DynamoClient"),
            patch("ingestion.yahoo_finance.handler.S3Client"),
        ):
            from ingestion.yahoo_finance.handler import handler

            result = handler({"action": "batch_prices"}, None)

        assert result["status"] == "ok"
        assert result["processed"] == 0

    def test_ticker_error_recorded_in_errors(self, monkeypatch):
        """A failed ticker is recorded; handler raises if error rate exceeds threshold."""
        import pytest

        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        with (
            patch(
                "ingestion.yahoo_finance.handler.get_watchlist_tickers",
                return_value=["FAIL1", "FAIL2"],
            ),
            patch(
                "ingestion.yahoo_finance.handler.yf.Ticker",
                side_effect=RuntimeError("network error"),
            ),
            patch("ingestion.yahoo_finance.handler.DynamoClient"),
            patch("ingestion.yahoo_finance.handler.S3Client"),
        ):
            from ingestion.yahoo_finance.handler import handler

            with pytest.raises(RuntimeError, match="Yahoo Finance"):
                handler({"action": "batch_prices"}, None)


class TestFullRefresh:
    """Tests for action=full_refresh."""

    def test_full_refresh_stores_history_to_s3(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        fake_info = {"currentPrice": 150.0, "sector": "Technology", "marketCap": 1e12}

        import pandas as pd

        fake_df = pd.DataFrame(
            {"Open": [100.0], "High": [105.0], "Low": [98.0], "Close": [103.0], "Volume": [1000]},
            index=pd.to_datetime(["2024-01-01"]),
        )

        with (
            patch("ingestion.yahoo_finance.handler.yf.Ticker") as mock_ticker_cls,
            patch("ingestion.yahoo_finance.handler.DynamoClient"),
            patch("ingestion.yahoo_finance.handler.S3Client") as mock_s3_cls,
        ):
            mock_t = MagicMock()
            mock_t.info = fake_info
            mock_t.history.return_value = fake_df
            mock_ticker_cls.return_value = mock_t

            mock_s3 = MagicMock()
            mock_s3_cls.return_value = mock_s3

            from ingestion.yahoo_finance.handler import handler

            result = handler({"action": "full_refresh", "ticker": "AAPL"}, None)

        assert result["status"] == "ok"
        assert result["processed"] == 1
        mock_s3.write_json.assert_called_once()

    def test_missing_ticker_returns_error(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        with (
            patch("ingestion.yahoo_finance.handler.DynamoClient"),
            patch("ingestion.yahoo_finance.handler.S3Client"),
        ):
            from ingestion.yahoo_finance.handler import handler

            result = handler({"action": "full_refresh"}, None)

        assert result["status"] == "error"
        assert "ticker required" in result["message"]
