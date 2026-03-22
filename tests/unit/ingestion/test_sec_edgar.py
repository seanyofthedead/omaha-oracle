"""
Unit tests for SEC EDGAR ingestion handler.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

_FAKE_TICKERS_JSON = {
    "0": {"ticker": "AAPL", "cik_str": 320193},
}

_FAKE_FACTS = {
    "facts": {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        {
                            "end": "2023-09-30",
                            "val": 394_000_000_000,
                            "form": "10-K",
                            "fp": "FY",
                            "fy": 2023,
                        }
                    ]
                }
            }
        },
        "dei": {},
    }
}

_FAKE_SUBMISSIONS = {
    "filings": {
        "recent": {"form": [], "accessionNumber": [], "filingDate": [], "primaryDocument": []}
    }
}


class TestSecEdgarHandler:
    """Tests for SEC EDGAR handler."""

    def _mock_fetch_json(self, url: str, headers: dict) -> dict:
        if "company_tickers.json" in url:
            return _FAKE_TICKERS_JSON
        if "companyfacts" in url:
            return _FAKE_FACTS
        return _FAKE_SUBMISSIONS

    def test_single_action_writes_financials(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("SEC_USER_AGENT", "TestApp test@example.com")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        with (
            patch("ingestion.sec_edgar.handler._fetch_json", side_effect=self._mock_fetch_json),
            patch("ingestion.sec_edgar.handler.DynamoClient") as mock_dynamo_cls,
            patch("ingestion.sec_edgar.handler.S3Client") as mock_s3_cls,
        ):
            mock_dynamo = MagicMock()
            mock_dynamo_cls.return_value = mock_dynamo
            mock_s3 = MagicMock()
            mock_s3_cls.return_value = mock_s3

            from ingestion.sec_edgar.handler import handler

            result = handler({"action": "single", "ticker": "AAPL"}, None)

        assert result["status"] == "ok"
        assert result["processed"] > 0
        mock_dynamo.put_item.assert_called()

    def test_single_cik_not_found(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("SEC_USER_AGENT", "TestApp test@example.com")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        with (
            patch("ingestion.sec_edgar.handler._fetch_json", return_value={}),
            patch("ingestion.sec_edgar.handler.DynamoClient"),
            patch("ingestion.sec_edgar.handler.S3Client"),
        ):
            from ingestion.sec_edgar.handler import handler

            result = handler({"action": "single", "ticker": "FAKE"}, None)

        assert result["status"] == "error"
        assert "CIK not found" in result["message"]

    def test_missing_ticker_for_single(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("SEC_USER_AGENT", "TestApp test@example.com")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        with (
            patch("ingestion.sec_edgar.handler._fetch_json", return_value={}),
            patch("ingestion.sec_edgar.handler.DynamoClient"),
            patch("ingestion.sec_edgar.handler.S3Client"),
        ):
            from ingestion.sec_edgar.handler import handler

            result = handler({"action": "single"}, None)

        assert result["status"] == "error"
        assert "ticker required" in result["message"]
