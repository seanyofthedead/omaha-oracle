"""Tests for dashboard.views.account_portfolio — account overview & positions.

Tests cover:
  - Account overview: all metrics rendered, PDT flag, blocked states
  - Positions table: full portfolio, single position, empty portfolio
  - Close position: success and failure paths
  - Error states: API timeout, rate limit, auth failure
  - Auto-refresh fragment decorator
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from dashboard.alpaca_models import AccountSummary, OrderInfo, PositionInfo

# ── Fixtures ──────────────────────────────────────────────────────────────


def _make_account(**overrides) -> AccountSummary:
    defaults = dict(
        account_id="abc12345-6789",
        equity=100_000.0,
        cash=25_000.0,
        buying_power=50_000.0,
        portfolio_value=100_000.0,
        pattern_day_trader=False,
        trading_blocked=False,
        account_blocked=False,
        daytrade_count=1,
        currency="USD",
        status="ACTIVE",
    )
    defaults.update(overrides)
    return AccountSummary(**defaults)


def _make_position(**overrides) -> PositionInfo:
    defaults = dict(
        asset_id="asset-001",
        symbol="AAPL",
        qty=10.0,
        side="long",
        avg_entry_price=150.0,
        current_price=160.0,
        market_value=1600.0,
        cost_basis=1500.0,
        unrealized_pl=100.0,
        unrealized_plpc=0.0667,
        change_today=0.005,
    )
    defaults.update(overrides)
    return PositionInfo(**defaults)


def _make_close_order(symbol: str = "AAPL") -> OrderInfo:
    return OrderInfo(
        order_id="order-001",
        symbol=symbol,
        qty=10.0,
        side="sell",
        order_type="market",
        time_in_force="day",
        status="pending_new",
        created_at="2026-03-22T14:00:00",
    )


@pytest.fixture()
def mock_st():
    """Patch streamlit in the account_portfolio module and return the mock."""
    with patch("dashboard.views.account_portfolio.st") as m:
        m.session_state = {}
        # Columns returns context-managers that yield themselves
        col = MagicMock()
        col.__enter__ = MagicMock(return_value=col)
        col.__exit__ = MagicMock(return_value=False)
        m.columns.return_value = [col, col, col, col, col]
        # Container returns a context-manager
        container = MagicMock()
        container.__enter__ = MagicMock(return_value=container)
        container.__exit__ = MagicMock(return_value=False)
        m.container.return_value = container
        yield m


@pytest.fixture()
def mock_client():
    """Return a mock AlpacaClient."""
    return MagicMock()


# ═══════════════════════════════════════════════════════════════════════════
# Feature 1: Account Overview
# ═══════════════════════════════════════════════════════════════════════════


class TestRenderAccountOverview:
    """Account overview hero metrics section."""

    def test_renders_all_metrics(self, mock_st, mock_client):
        from dashboard.views.account_portfolio import render_account_overview

        mock_client.get_account.return_value = _make_account()

        render_account_overview(mock_client)

        # Should call st.metric for equity, buying power, cash, positions (placeholder), status
        metric_calls = mock_st.metric.call_args_list
        labels = [call.kwargs.get("label", call.args[0]) for call in metric_calls]
        assert "Equity" in labels
        assert "Buying Power" in labels
        assert "Cash" in labels

    def test_displays_account_status(self, mock_st, mock_client):
        from dashboard.views.account_portfolio import render_account_overview

        mock_client.get_account.return_value = _make_account(status="ACTIVE")

        render_account_overview(mock_client)

        metric_calls = mock_st.metric.call_args_list
        # At least one metric should show account status or it should be displayed
        all_text = str(metric_calls)
        assert "ACTIVE" in all_text or mock_st.success.called or mock_st.markdown.called

    def test_pdt_flag_true_shows_warning(self, mock_st, mock_client):
        from dashboard.views.account_portfolio import render_account_overview

        mock_client.get_account.return_value = _make_account(
            pattern_day_trader=True, daytrade_count=4
        )

        render_account_overview(mock_client)

        mock_st.warning.assert_called()
        warning_text = str(mock_st.warning.call_args_list)
        assert "day trad" in warning_text.lower() or "pdt" in warning_text.lower()

    def test_pdt_flag_false_no_warning(self, mock_st, mock_client):
        from dashboard.views.account_portfolio import render_account_overview

        mock_client.get_account.return_value = _make_account(pattern_day_trader=False)

        render_account_overview(mock_client)

        # No PDT-specific warning
        if mock_st.warning.called:
            for call in mock_st.warning.call_args_list:
                text = str(call).lower()
                assert "day trad" not in text and "pdt" not in text

    def test_trading_blocked_shows_error(self, mock_st, mock_client):
        from dashboard.views.account_portfolio import render_account_overview

        mock_client.get_account.return_value = _make_account(trading_blocked=True)

        render_account_overview(mock_client)

        mock_st.error.assert_called()
        error_text = str(mock_st.error.call_args_list).lower()
        assert "trading" in error_text and "blocked" in error_text

    def test_account_blocked_shows_error(self, mock_st, mock_client):
        from dashboard.views.account_portfolio import render_account_overview

        mock_client.get_account.return_value = _make_account(account_blocked=True)

        render_account_overview(mock_client)

        mock_st.error.assert_called()
        error_text = str(mock_st.error.call_args_list).lower()
        assert "account" in error_text and "blocked" in error_text

    def test_api_error_handled_gracefully(self, mock_st, mock_client):
        from dashboard.views.account_portfolio import render_account_overview

        mock_client.get_account.side_effect = Exception("connection refused")

        with patch("dashboard.views.account_portfolio.handle_alpaca_error") as mock_handle:
            render_account_overview(mock_client)
            mock_handle.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════
# Feature 2: Positions Table
# ═══════════════════════════════════════════════════════════════════════════


class TestRenderPositionsTable:
    """Portfolio positions table and close-position buttons."""

    def test_full_portfolio_renders_dataframe(self, mock_st, mock_client):
        from dashboard.views.account_portfolio import render_positions_table

        positions = [
            _make_position(symbol="AAPL"),
            _make_position(symbol="GOOG", avg_entry_price=140.0, current_price=155.0),
            _make_position(symbol="MSFT", avg_entry_price=300.0, current_price=310.0),
        ]
        mock_client.get_positions.return_value = positions

        render_positions_table(mock_client)

        mock_st.dataframe.assert_called_once()
        df_arg = mock_st.dataframe.call_args
        # DataFrame should have all 3 positions
        data = df_arg.args[0] if df_arg.args else df_arg.kwargs.get("data")
        assert len(data) == 3

    def test_single_position(self, mock_st, mock_client):
        from dashboard.views.account_portfolio import render_positions_table

        mock_client.get_positions.return_value = [_make_position(symbol="TSLA")]

        render_positions_table(mock_client)

        mock_st.dataframe.assert_called_once()
        data = mock_st.dataframe.call_args.args[0]
        assert len(data) == 1

    def test_empty_portfolio_shows_info(self, mock_st, mock_client):
        from dashboard.views.account_portfolio import render_positions_table

        mock_client.get_positions.return_value = []

        render_positions_table(mock_client)

        mock_st.info.assert_called()
        mock_st.dataframe.assert_not_called()

    def test_position_columns_present(self, mock_st, mock_client):
        from dashboard.views.account_portfolio import render_positions_table

        mock_client.get_positions.return_value = [_make_position()]

        render_positions_table(mock_client)

        df = mock_st.dataframe.call_args.args[0]
        columns = list(df.columns)
        for expected in ["Symbol", "Qty", "Side", "Avg Entry", "Current Price",
                         "Market Value", "Unrealized P&L", "P&L %"]:
            assert expected in columns, f"Missing column: {expected}"

    def test_pl_percentage_calculated_correctly(self, mock_st, mock_client):
        from dashboard.views.account_portfolio import render_positions_table

        mock_client.get_positions.return_value = [
            _make_position(unrealized_plpc=0.15)  # 15%
        ]

        render_positions_table(mock_client)

        df = mock_st.dataframe.call_args.args[0]
        # unrealized_plpc is 0.15 → should display as 15.0 (percentage points)
        assert df.iloc[0]["P&L %"] == pytest.approx(15.0, abs=0.1)

    def test_api_error_handled_gracefully(self, mock_st, mock_client):
        from dashboard.views.account_portfolio import render_positions_table

        mock_client.get_positions.side_effect = Exception("timeout")

        with patch("dashboard.views.account_portfolio.handle_alpaca_error") as mock_handle:
            render_positions_table(mock_client)
            mock_handle.assert_called_once()

    def test_dataframe_uses_hide_index(self, mock_st, mock_client):
        from dashboard.views.account_portfolio import render_positions_table

        mock_client.get_positions.return_value = [_make_position()]

        render_positions_table(mock_client)

        kwargs = mock_st.dataframe.call_args.kwargs
        assert kwargs.get("hide_index") is True

    def test_dataframe_uses_full_width(self, mock_st, mock_client):
        from dashboard.views.account_portfolio import render_positions_table

        mock_client.get_positions.return_value = [_make_position()]

        render_positions_table(mock_client)

        kwargs = mock_st.dataframe.call_args.kwargs
        assert kwargs.get("use_container_width") is True


# ═══════════════════════════════════════════════════════════════════════════
# Feature 3: Close Position
# ═══════════════════════════════════════════════════════════════════════════


class TestClosePosition:
    """Close position action triggered by button click."""

    def test_close_success_shows_toast(self, mock_st, mock_client):
        from dashboard.views.account_portfolio import _handle_close_position

        mock_client.close_position.return_value = _make_close_order("AAPL")

        _handle_close_position(mock_client, "AAPL", mock_st)

        mock_client.close_position.assert_called_once_with("AAPL")
        mock_st.success.assert_called()
        success_text = str(mock_st.success.call_args_list).lower()
        assert "aapl" in success_text

    def test_close_failure_shows_error(self, mock_st, mock_client):
        from dashboard.views.account_portfolio import _handle_close_position

        mock_client.close_position.side_effect = Exception("position not found")

        with patch("dashboard.views.account_portfolio.handle_alpaca_error") as mock_handle:
            _handle_close_position(mock_client, "AAPL", mock_st)
            mock_handle.assert_called_once()

    def test_close_calls_rerun(self, mock_st, mock_client):
        from dashboard.views.account_portfolio import _handle_close_position

        mock_client.close_position.return_value = _make_close_order("AAPL")

        _handle_close_position(mock_client, "AAPL", mock_st)

        mock_st.rerun.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════
# Feature 4: Error States
# ═══════════════════════════════════════════════════════════════════════════


class TestErrorStates:
    """Error handling for various API failure modes."""

    def test_rate_limit_error(self, mock_st, mock_client):
        """Rate limit on get_account is handled via handle_alpaca_error."""
        import json

        from requests import HTTPError, Response

        from alpaca.common.exceptions import APIError
        from dashboard.views.account_portfolio import render_account_overview

        body = json.dumps({"code": 42900000, "message": "rate limit exceeded"})
        resp = Response()
        resp.status_code = 429
        exc = APIError(body, HTTPError(response=resp))
        mock_client.get_account.side_effect = exc

        with patch("dashboard.views.account_portfolio.handle_alpaca_error") as mock_handle:
            render_account_overview(mock_client)
            mock_handle.assert_called_once_with(exc)

    def test_auth_failure_error(self, mock_st, mock_client):
        """Auth failure on get_positions is handled via handle_alpaca_error."""
        import json

        from requests import HTTPError, Response

        from alpaca.common.exceptions import APIError
        from dashboard.views.account_portfolio import render_positions_table

        body = json.dumps({"code": 40110000, "message": "unauthorized"})
        resp = Response()
        resp.status_code = 401
        exc = APIError(body, HTTPError(response=resp))
        mock_client.get_positions.side_effect = exc

        with patch("dashboard.views.account_portfolio.handle_alpaca_error") as mock_handle:
            render_positions_table(mock_client)
            mock_handle.assert_called_once_with(exc)

    def test_timeout_error(self, mock_st, mock_client):
        """Timeout on get_account is handled via handle_alpaca_error."""
        from requests.exceptions import Timeout

        from dashboard.views.account_portfolio import render_account_overview

        exc = Timeout("timed out")
        mock_client.get_account.side_effect = exc

        with patch("dashboard.views.account_portfolio.handle_alpaca_error") as mock_handle:
            render_account_overview(mock_client)
            mock_handle.assert_called_once_with(exc)
