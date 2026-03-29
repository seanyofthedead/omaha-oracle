"""
Unit tests for insider transactions ingestion handler.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

_FAKE_TICKERS_JSON = {"0": {"ticker": "AAPL", "cik_str": 320193}}

_FORM4_XML = """<?xml version="1.0"?>
<ownershipDocument>
  <transactionAcquiredDisposedCode>A</transactionAcquiredDisposedCode>
  <transactionShares>10000</transactionShares>
  <transactionPricePerShare>150.00</transactionPricePerShare>
</ownershipDocument>"""

# Use a date within the 30-day lookback window
_RECENT_DATE = (datetime.now(UTC) - timedelta(days=5)).strftime("%Y-%m-%d")

_SUBMISSIONS = {
    "filings": {
        "recent": {
            "form": ["4"],
            "accessionNumber": ["0001234567-24-000001"],
            "filingDate": [_RECENT_DATE],
            "primaryDocument": ["form4.xml"],
        }
    }
}


class TestInsiderTransactionsHandler:
    """Tests for insider transactions handler."""

    def test_handler_stores_filing(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("SEC_USER_AGENT", "TestApp test@example.com")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        def _mock_fetch_json(url, headers):
            if "company_tickers" in url:
                return _FAKE_TICKERS_JSON
            return _SUBMISSIONS

        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.text = _FORM4_XML

        with (
            patch(
                "ingestion.insider_transactions.handler.get_watchlist_tickers",
                return_value=["AAPL"],
            ),
            patch(
                "ingestion.insider_transactions.handler._get_ticker_to_cik",
                return_value={"AAPL": "0000320193"},
            ),
            patch(
                "ingestion.insider_transactions.handler._fetch_json", side_effect=_mock_fetch_json
            ),
            patch("ingestion.insider_transactions.handler._fetch", return_value=mock_resp),
            patch("ingestion.insider_transactions.handler.S3Client") as mock_s3_cls,
        ):
            mock_s3 = MagicMock()
            mock_s3_cls.return_value = mock_s3

            from ingestion.insider_transactions.handler import handler

            result = handler({}, None)

        assert result["filings_stored"] >= 1
        assert result["significant_buys"] == 1  # 10000 shares * $150 = $1.5M > $100K threshold
        mock_s3.write_json.assert_called_once()

    def test_empty_watchlist(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("SEC_USER_AGENT", "TestApp test@example.com")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        with (
            patch("ingestion.insider_transactions.handler.get_watchlist_tickers", return_value=[]),
            patch("ingestion.insider_transactions.handler._get_ticker_to_cik", return_value={}),
            patch("ingestion.insider_transactions.handler._fetch_json", return_value={}),
            patch("ingestion.insider_transactions.handler.S3Client"),
        ):
            from ingestion.insider_transactions.handler import handler

            result = handler({}, None)

        assert result["filings_stored"] == 0
        assert result["significant_buys"] == 0

    def test_cik_not_found_populates_errors(self, monkeypatch):
        """CIK-missing tickers are recorded in errors; handler raises when error rate > 50%."""
        import pytest

        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("SEC_USER_AGENT", "TestApp test@example.com")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        with (
            # Two tickers, both unknown → 100% error rate → check_failure_threshold raises
            patch(
                "ingestion.insider_transactions.handler.get_watchlist_tickers",
                return_value=["FAKE1", "FAKE2"],
            ),
            patch("ingestion.insider_transactions.handler._get_ticker_to_cik", return_value={}),
            patch("ingestion.insider_transactions.handler._fetch_json", return_value={}),
            patch("ingestion.insider_transactions.handler.S3Client"),
        ):
            from ingestion.insider_transactions.handler import handler

            with pytest.raises(RuntimeError, match="Insider transactions"):
                handler({}, None)
