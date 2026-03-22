"""Tests for dashboard.alpaca_auth — API key UI component logic."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from dashboard.alpaca_auth import (
    clear_alpaca_session,
    get_alpaca_client_from_session,
    is_alpaca_connected,
    store_alpaca_keys,
)


@pytest.fixture()
def mock_session_state():
    """Provide a plain dict as st.session_state."""
    state = {}
    with patch("dashboard.alpaca_auth.st") as mock_st:
        mock_st.session_state = state
        yield mock_st, state


class TestStoreAlpacaKeys:
    def test_stores_keys_in_session(self, mock_session_state):
        mock_st, state = mock_session_state
        store_alpaca_keys("my-key", "my-secret")
        assert state["alpaca_api_key"] == "my-key"
        assert state["alpaca_secret_key"] == "my-secret"

    def test_empty_keys_not_stored(self, mock_session_state):
        mock_st, state = mock_session_state
        store_alpaca_keys("", "")
        assert "alpaca_api_key" not in state


class TestIsAlpacaConnected:
    def test_true_when_client_present(self, mock_session_state):
        mock_st, state = mock_session_state
        state["alpaca_client"] = MagicMock()
        assert is_alpaca_connected() is True

    def test_false_when_no_client(self, mock_session_state):
        mock_st, state = mock_session_state
        assert is_alpaca_connected() is False


class TestGetAlpacaClientFromSession:
    def test_returns_client_when_present(self, mock_session_state):
        mock_st, state = mock_session_state
        sentinel = MagicMock()
        state["alpaca_client"] = sentinel
        assert get_alpaca_client_from_session() is sentinel

    def test_returns_none_when_absent(self, mock_session_state):
        mock_st, state = mock_session_state
        assert get_alpaca_client_from_session() is None


class TestClearAlpacaSession:
    def test_clears_all_keys(self, mock_session_state):
        mock_st, state = mock_session_state
        state["alpaca_api_key"] = "k"
        state["alpaca_secret_key"] = "s"
        state["alpaca_client"] = MagicMock()
        clear_alpaca_session()
        assert "alpaca_api_key" not in state
        assert "alpaca_secret_key" not in state
        assert "alpaca_client" not in state

    def test_noop_when_already_clear(self, mock_session_state):
        mock_st, state = mock_session_state
        clear_alpaca_session()  # should not raise
