"""
Unit tests for FRED macro data ingestion handler.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestFredHandler:
    """Tests for FRED ingestion handler."""

    def _fake_response(self, observations: list | None = None) -> MagicMock:
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "observations": observations or [{"date": "2024-01-01", "value": "5.33"}]
        }
        return resp

    def test_happy_path_stores_all_series(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("FRED_API_KEY", "fake-key")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        with (
            patch("ingestion.fred.handler.get_session") as mock_session_fn,
            patch("ingestion.fred.handler.S3Client") as mock_s3_cls,
        ):
            mock_session = MagicMock()
            mock_session.get.return_value = self._fake_response()
            mock_session_fn.return_value = mock_session

            mock_s3 = MagicMock()
            mock_s3_cls.return_value = mock_s3

            from ingestion.fred.handler import MACRO_SERIES, handler

            result = handler({}, None)

        assert result["status"] in ("ok", "partial")
        assert result["stored"] == len(MACRO_SERIES)
        assert mock_s3.write_json.call_count == len(MACRO_SERIES)

    def test_one_series_fails_partial_result(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "dev")
        monkeypatch.setenv("FRED_API_KEY", "fake-key")
        monkeypatch.setenv("MONTHLY_LLM_BUDGET_CENTS", "0")

        call_count = 0

        def _get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("connection timeout")
            return self._fake_response()

        with (
            patch("ingestion.fred.handler.get_session") as mock_session_fn,
            patch("ingestion.fred.handler.S3Client"),
        ):
            mock_session = MagicMock()
            mock_session.get.side_effect = _get
            mock_session_fn.return_value = mock_session

            from ingestion.fred.handler import handler

            result = handler({}, None)

        assert result["status"] == "partial"
        assert len(result["errors"]) == 1
