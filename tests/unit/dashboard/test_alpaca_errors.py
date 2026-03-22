"""Tests for dashboard.alpaca_errors — unified Alpaca error handling."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from requests import HTTPError, Response

from dashboard.alpaca_errors import (
    AlpacaAuthError,
    AlpacaDashboardError,
    AlpacaInsufficientFundsError,
    AlpacaInvalidSymbolError,
    AlpacaMarketClosedError,
    AlpacaPDTViolationError,
    AlpacaRateLimitError,
    classify_alpaca_error,
    handle_alpaca_error,
)


def _make_api_error(status_code: int, code: int, message: str):
    """Build an alpaca APIError with a realistic shape."""
    from alpaca.common.exceptions import APIError

    error_body = json.dumps({"code": code, "message": message})
    http_response = Response()
    http_response.status_code = status_code
    http_error = HTTPError(response=http_response)
    return APIError(error_body, http_error)


# ── classify_alpaca_error ──


class TestClassifyAlpacaError:
    def test_auth_failure_401(self):
        exc = _make_api_error(401, 40110000, "access key verification failed")
        result = classify_alpaca_error(exc)
        assert isinstance(result, AlpacaAuthError)
        assert "API keys" in result.user_message

    def test_auth_failure_403(self):
        exc = _make_api_error(403, 40310000, "forbidden")
        result = classify_alpaca_error(exc)
        assert isinstance(result, AlpacaAuthError)

    def test_rate_limit_429(self):
        exc = _make_api_error(429, 42900000, "rate limit exceeded")
        result = classify_alpaca_error(exc)
        assert isinstance(result, AlpacaRateLimitError)
        assert "rate limit" in result.user_message.lower()

    def test_invalid_symbol(self):
        exc = _make_api_error(404, 40410000, "asset not found")
        result = classify_alpaca_error(exc)
        assert isinstance(result, AlpacaInvalidSymbolError)

    def test_invalid_symbol_from_422(self):
        exc = _make_api_error(422, 42210000, "could not find asset")
        result = classify_alpaca_error(exc)
        assert isinstance(result, AlpacaInvalidSymbolError)

    def test_insufficient_buying_power(self):
        exc = _make_api_error(403, 40310000, "insufficient buying power")
        result = classify_alpaca_error(exc)
        assert isinstance(result, AlpacaInsufficientFundsError)

    def test_pdt_violation(self):
        exc = _make_api_error(403, 40310000, "pattern day trader")
        result = classify_alpaca_error(exc)
        assert isinstance(result, AlpacaPDTViolationError)

    def test_market_closed(self):
        exc = _make_api_error(403, 40310000, "market is not open")
        result = classify_alpaca_error(exc)
        assert isinstance(result, AlpacaMarketClosedError)

    def test_generic_api_error(self):
        exc = _make_api_error(500, 50010000, "internal server error")
        result = classify_alpaca_error(exc)
        assert isinstance(result, AlpacaDashboardError)
        assert "500" in result.user_message or "server" in result.user_message.lower()

    def test_non_api_error_passthrough(self):
        exc = ConnectionError("network down")
        result = classify_alpaca_error(exc)
        assert isinstance(result, AlpacaDashboardError)
        assert "connection" in result.user_message.lower()

    def test_timeout_error(self):
        from requests.exceptions import Timeout

        exc = Timeout("timed out")
        result = classify_alpaca_error(exc)
        assert isinstance(result, AlpacaDashboardError)
        assert "timed out" in result.user_message.lower()


# ── handle_alpaca_error (Streamlit integration) ──


class TestHandleAlpacaError:
    @patch("dashboard.alpaca_errors.st")
    def test_auth_error_shows_st_error(self, mock_st: MagicMock):
        exc = _make_api_error(401, 40110000, "unauthorized")
        handle_alpaca_error(exc)
        mock_st.error.assert_called_once()
        msg = mock_st.error.call_args[0][0]
        assert "API keys" in msg

    @patch("dashboard.alpaca_errors.st")
    def test_rate_limit_shows_st_warning(self, mock_st: MagicMock):
        exc = _make_api_error(429, 42900000, "rate limit exceeded")
        handle_alpaca_error(exc)
        mock_st.warning.assert_called_once()

    @patch("dashboard.alpaca_errors.st")
    def test_generic_error_shows_st_error(self, mock_st: MagicMock):
        exc = _make_api_error(500, 50010000, "boom")
        handle_alpaca_error(exc)
        mock_st.error.assert_called_once()

    @patch("dashboard.alpaca_errors.st")
    def test_market_closed_shows_st_warning(self, mock_st: MagicMock):
        exc = _make_api_error(403, 40310000, "market is not open")
        handle_alpaca_error(exc)
        mock_st.warning.assert_called_once()
