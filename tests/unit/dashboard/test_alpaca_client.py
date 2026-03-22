"""Tests for dashboard.alpaca_client — singleton Alpaca wrapper.

Every Alpaca SDK call is mocked.  Tests cover:
  - success paths for all 12 methods
  - empty responses
  - API timeout / connection errors
  - auth failures (401/403)
  - rate limits (429)
  - market-closed errors
  - invalid symbol errors
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from requests import HTTPError, Response

from dashboard.alpaca_client import AlpacaClient
from dashboard.alpaca_models import (
    AccountSummary,
    AssetInfo,
    OrderInfo,
    PositionInfo,
    WatchlistInfo,
)

# ── Helpers ──────────────────────────────────────────────────────────────


def _make_api_error(status_code: int, code: int, message: str):
    from alpaca.common.exceptions import APIError

    body = json.dumps({"code": code, "message": message})
    resp = Response()
    resp.status_code = status_code
    return APIError(body, HTTPError(response=resp))


def _mock_trading_client() -> MagicMock:
    return MagicMock()


def _make_client(mock_tc: MagicMock | None = None) -> AlpacaClient:
    """Create an AlpacaClient with an injected mock TradingClient."""
    client = AlpacaClient.__new__(AlpacaClient)
    client._client = mock_tc or _mock_trading_client()
    return client


# ── Fake SDK models ──────────────────────────────────────────────────────

_NOW = datetime(2026, 3, 22, 14, 0, 0, tzinfo=UTC)
_UUID = uuid4()


def _fake_account():
    m = MagicMock()
    m.id = _UUID
    m.equity = "100000.00"
    m.cash = "25000.00"
    m.buying_power = "50000.00"
    m.portfolio_value = "100000.00"
    m.pattern_day_trader = False
    m.trading_blocked = False
    m.account_blocked = False
    m.daytrade_count = 1
    m.currency = "USD"
    m.status = MagicMock()
    m.status.value = "ACTIVE"
    return m


def _fake_position(symbol: str = "AAPL"):
    m = MagicMock()
    m.asset_id = _UUID
    m.symbol = symbol
    m.qty = "10"
    m.side = MagicMock()
    m.side.value = "long"
    m.avg_entry_price = "150.00"
    m.current_price = "160.00"
    m.market_value = "1600.00"
    m.cost_basis = "1500.00"
    m.unrealized_pl = "100.00"
    m.unrealized_plpc = "0.0667"
    m.change_today = "0.005"
    return m


def _fake_order(symbol: str = "AAPL", status: str = "filled"):
    m = MagicMock()
    m.id = _UUID
    m.symbol = symbol
    m.qty = "5"
    m.side = MagicMock()
    m.side.value = "buy"
    m.order_type = MagicMock()
    m.order_type.value = "market"
    m.type = MagicMock()
    m.type.value = "market"
    m.time_in_force = MagicMock()
    m.time_in_force.value = "day"
    m.status = MagicMock()
    m.status.value = status
    m.created_at = _NOW
    m.filled_at = _NOW if status == "filled" else None
    m.filled_avg_price = "155.00" if status == "filled" else None
    m.limit_price = None
    m.stop_price = None
    return m


def _fake_asset(symbol: str = "AAPL"):
    m = MagicMock()
    m.id = _UUID
    m.symbol = symbol
    m.name = "Apple Inc."
    m.asset_class = MagicMock()
    m.asset_class.value = "us_equity"
    m.exchange = MagicMock()
    m.exchange.value = "NASDAQ"
    m.tradable = True
    m.fractionable = True
    m.status = MagicMock()
    m.status.value = "active"
    return m


def _fake_watchlist(name: str = "Tech", symbols: list[str] | None = None):
    m = MagicMock()
    m.id = _UUID
    m.name = name
    m.created_at = _NOW
    m.updated_at = _NOW
    assets = []
    for s in symbols or []:
        a = MagicMock()
        a.symbol = s
        assets.append(a)
    m.assets = assets
    return m


# ═════════════════════════════════════════════════════════════════════════
# get_account
# ═════════════════════════════════════════════════════════════════════════


class TestGetAccount:
    def test_success(self):
        tc = _mock_trading_client()
        tc.get_account.return_value = _fake_account()
        client = _make_client(tc)

        result = client.get_account()
        assert isinstance(result, AccountSummary)
        assert result.equity == 100_000.0
        assert result.cash == 25_000.0
        assert result.daytrade_count == 1

    def test_auth_failure(self):
        tc = _mock_trading_client()
        tc.get_account.side_effect = _make_api_error(401, 40110000, "unauthorized")
        client = _make_client(tc)

        with pytest.raises(Exception) as exc_info:
            client.get_account()
        assert exc_info.value.__class__.__name__ == "APIError"

    def test_timeout(self):
        from requests.exceptions import Timeout

        tc = _mock_trading_client()
        tc.get_account.side_effect = Timeout("timed out")
        client = _make_client(tc)

        with pytest.raises(Timeout):
            client.get_account()


# ═════════════════════════════════════════════════════════════════════════
# get_positions
# ═════════════════════════════════════════════════════════════════════════


class TestGetPositions:
    def test_success(self):
        tc = _mock_trading_client()
        tc.get_all_positions.return_value = [_fake_position("AAPL"), _fake_position("GOOG")]
        client = _make_client(tc)

        result = client.get_positions()
        assert len(result) == 2
        assert all(isinstance(p, PositionInfo) for p in result)
        assert result[0].symbol == "AAPL"

    def test_empty(self):
        tc = _mock_trading_client()
        tc.get_all_positions.return_value = []
        client = _make_client(tc)

        result = client.get_positions()
        assert result == []

    def test_rate_limit(self):
        tc = _mock_trading_client()
        tc.get_all_positions.side_effect = _make_api_error(429, 42900000, "rate limit")
        client = _make_client(tc)

        with pytest.raises(Exception):
            client.get_positions()


# ═════════════════════════════════════════════════════════════════════════
# submit_order
# ═════════════════════════════════════════════════════════════════════════


class TestSubmitOrder:
    def test_market_order_success(self):
        tc = _mock_trading_client()
        tc.submit_order.return_value = _fake_order("AAPL", "new")
        client = _make_client(tc)

        result = client.submit_order(
            symbol="AAPL", qty=5, side="buy", order_type="market", time_in_force="day"
        )
        assert isinstance(result, OrderInfo)
        assert result.symbol == "AAPL"
        tc.submit_order.assert_called_once()

    def test_limit_order_success(self):
        tc = _mock_trading_client()
        tc.submit_order.return_value = _fake_order("GOOG", "new")
        client = _make_client(tc)

        result = client.submit_order(
            symbol="GOOG",
            qty=3,
            side="buy",
            order_type="limit",
            time_in_force="gtc",
            limit_price=140.0,
        )
        assert isinstance(result, OrderInfo)
        tc.submit_order.assert_called_once()

    def test_stop_limit_order_success(self):
        tc = _mock_trading_client()
        tc.submit_order.return_value = _fake_order("TSLA", "new")
        client = _make_client(tc)

        result = client.submit_order(
            symbol="TSLA",
            qty=2,
            side="sell",
            order_type="stop_limit",
            time_in_force="day",
            limit_price=200.0,
            stop_price=195.0,
        )
        assert isinstance(result, OrderInfo)

    def test_insufficient_buying_power(self):
        tc = _mock_trading_client()
        tc.submit_order.side_effect = _make_api_error(
            403, 40310000, "insufficient buying power"
        )
        client = _make_client(tc)

        with pytest.raises(Exception):
            client.submit_order(
                symbol="BRK.A", qty=1, side="buy", order_type="market", time_in_force="day"
            )

    def test_market_closed(self):
        tc = _mock_trading_client()
        tc.submit_order.side_effect = _make_api_error(
            403, 40310000, "market is not open for trading"
        )
        client = _make_client(tc)

        with pytest.raises(Exception):
            client.submit_order(
                symbol="AAPL", qty=1, side="buy", order_type="market", time_in_force="day"
            )


# ═════════════════════════════════════════════════════════════════════════
# get_orders
# ═════════════════════════════════════════════════════════════════════════


class TestGetOrders:
    def test_success(self):
        tc = _mock_trading_client()
        tc.get_orders.return_value = [_fake_order("AAPL", "filled"), _fake_order("GOOG", "new")]
        client = _make_client(tc)

        result = client.get_orders()
        assert len(result) == 2
        assert all(isinstance(o, OrderInfo) for o in result)

    def test_with_status_filter(self):
        tc = _mock_trading_client()
        tc.get_orders.return_value = [_fake_order("AAPL", "filled")]
        client = _make_client(tc)

        result = client.get_orders(status="closed")
        assert len(result) == 1
        tc.get_orders.assert_called_once()

    def test_empty(self):
        tc = _mock_trading_client()
        tc.get_orders.return_value = []
        client = _make_client(tc)

        result = client.get_orders()
        assert result == []


# ═════════════════════════════════════════════════════════════════════════
# get_asset
# ═════════════════════════════════════════════════════════════════════════


class TestGetAsset:
    def test_success(self):
        tc = _mock_trading_client()
        tc.get_asset.return_value = _fake_asset("AAPL")
        client = _make_client(tc)

        result = client.get_asset("AAPL")
        assert isinstance(result, AssetInfo)
        assert result.symbol == "AAPL"
        assert result.tradable is True

    def test_invalid_symbol(self):
        tc = _mock_trading_client()
        tc.get_asset.side_effect = _make_api_error(404, 40410000, "asset not found")
        client = _make_client(tc)

        with pytest.raises(Exception):
            client.get_asset("XYZZZZ")


# ═════════════════════════════════════════════════════════════════════════
# cancel_order
# ═════════════════════════════════════════════════════════════════════════


class TestCancelOrder:
    def test_success(self):
        tc = _mock_trading_client()
        tc.cancel_order_by_id.return_value = None
        client = _make_client(tc)

        client.cancel_order(str(_UUID))
        tc.cancel_order_by_id.assert_called_once_with(str(_UUID))

    def test_order_not_found(self):
        tc = _mock_trading_client()
        tc.cancel_order_by_id.side_effect = _make_api_error(
            404, 40410000, "order not found"
        )
        client = _make_client(tc)

        with pytest.raises(Exception):
            client.cancel_order("nonexistent")


# ═════════════════════════════════════════════════════════════════════════
# close_position
# ═════════════════════════════════════════════════════════════════════════


class TestClosePosition:
    def test_success(self):
        tc = _mock_trading_client()
        tc.close_position.return_value = _fake_order("AAPL", "pending_new")
        client = _make_client(tc)

        result = client.close_position("AAPL")
        assert isinstance(result, OrderInfo)
        tc.close_position.assert_called_once_with("AAPL")

    def test_no_position(self):
        tc = _mock_trading_client()
        tc.close_position.side_effect = _make_api_error(
            404, 40410000, "position does not exist"
        )
        client = _make_client(tc)

        with pytest.raises(Exception):
            client.close_position("ZZZZ")


# ═════════════════════════════════════════════════════════════════════════
# get_watchlists
# ═════════════════════════════════════════════════════════════════════════


class TestGetWatchlists:
    def test_success(self):
        tc = _mock_trading_client()
        tc.get_watchlists.return_value = [
            _fake_watchlist("Tech", ["AAPL", "GOOG"]),
            _fake_watchlist("Finance", ["JPM"]),
        ]
        client = _make_client(tc)

        result = client.get_watchlists()
        assert len(result) == 2
        assert all(isinstance(w, WatchlistInfo) for w in result)
        assert result[0].name == "Tech"
        assert result[0].symbols == ["AAPL", "GOOG"]

    def test_empty(self):
        tc = _mock_trading_client()
        tc.get_watchlists.return_value = []
        client = _make_client(tc)

        result = client.get_watchlists()
        assert result == []

    def test_auth_failure(self):
        tc = _mock_trading_client()
        tc.get_watchlists.side_effect = _make_api_error(401, 40110000, "unauthorized")
        client = _make_client(tc)

        with pytest.raises(Exception):
            client.get_watchlists()


# ═════════════════════════════════════════════════════════════════════════
# create_watchlist
# ═════════════════════════════════════════════════════════════════════════


class TestCreateWatchlist:
    def test_success(self):
        tc = _mock_trading_client()
        tc.create_watchlist.return_value = _fake_watchlist("New List", ["AAPL"])
        client = _make_client(tc)

        result = client.create_watchlist("New List", symbols=["AAPL"])
        assert isinstance(result, WatchlistInfo)
        assert result.name == "New List"
        tc.create_watchlist.assert_called_once()

    def test_empty_symbols(self):
        tc = _mock_trading_client()
        tc.create_watchlist.return_value = _fake_watchlist("Empty WL", [])
        client = _make_client(tc)

        result = client.create_watchlist("Empty WL")
        assert result.symbols == []

    def test_duplicate_name(self):
        tc = _mock_trading_client()
        tc.create_watchlist.side_effect = _make_api_error(
            422, 42210000, "watchlist name is not unique"
        )
        client = _make_client(tc)

        with pytest.raises(Exception):
            client.create_watchlist("Duplicate")


# ═════════════════════════════════════════════════════════════════════════
# add_to_watchlist
# ═════════════════════════════════════════════════════════════════════════


class TestAddToWatchlist:
    def test_success(self):
        tc = _mock_trading_client()
        tc.add_asset_to_watchlist_by_id.return_value = _fake_watchlist(
            "Tech", ["AAPL", "MSFT"]
        )
        client = _make_client(tc)

        result = client.add_to_watchlist(str(_UUID), "MSFT")
        assert isinstance(result, WatchlistInfo)
        assert "MSFT" in result.symbols
        tc.add_asset_to_watchlist_by_id.assert_called_once_with(str(_UUID), "MSFT")

    def test_invalid_symbol(self):
        tc = _mock_trading_client()
        tc.add_asset_to_watchlist_by_id.side_effect = _make_api_error(
            404, 40410000, "asset not found"
        )
        client = _make_client(tc)

        with pytest.raises(Exception):
            client.add_to_watchlist(str(_UUID), "XYZZZZ")


# ═════════════════════════════════════════════════════════════════════════
# remove_from_watchlist
# ═════════════════════════════════════════════════════════════════════════


class TestRemoveFromWatchlist:
    def test_success(self):
        tc = _mock_trading_client()
        tc.remove_asset_from_watchlist_by_id.return_value = _fake_watchlist("Tech", ["AAPL"])
        client = _make_client(tc)

        result = client.remove_from_watchlist(str(_UUID), "GOOG")
        assert isinstance(result, WatchlistInfo)
        tc.remove_asset_from_watchlist_by_id.assert_called_once_with(str(_UUID), "GOOG")

    def test_symbol_not_in_watchlist(self):
        tc = _mock_trading_client()
        tc.remove_asset_from_watchlist_by_id.side_effect = _make_api_error(
            404, 40410000, "asset not found in watchlist"
        )
        client = _make_client(tc)

        with pytest.raises(Exception):
            client.remove_from_watchlist(str(_UUID), "XYZZZZ")


# ═════════════════════════════════════════════════════════════════════════
# delete_watchlist
# ═════════════════════════════════════════════════════════════════════════


class TestDeleteWatchlist:
    def test_success(self):
        tc = _mock_trading_client()
        tc.delete_watchlist_by_id.return_value = None
        client = _make_client(tc)

        client.delete_watchlist(str(_UUID))
        tc.delete_watchlist_by_id.assert_called_once_with(str(_UUID))

    def test_not_found(self):
        tc = _mock_trading_client()
        tc.delete_watchlist_by_id.side_effect = _make_api_error(
            404, 40410000, "watchlist not found"
        )
        client = _make_client(tc)

        with pytest.raises(Exception):
            client.delete_watchlist("nonexistent")


# ═════════════════════════════════════════════════════════════════════════
# Connection test
# ═════════════════════════════════════════════════════════════════════════


class TestConnectionTest:
    def test_success(self):
        tc = _mock_trading_client()
        tc.get_account.return_value = _fake_account()
        client = _make_client(tc)

        ok, msg = client.test_connection()
        assert ok is True
        assert "connected" in msg.lower()

    def test_failure(self):
        tc = _mock_trading_client()
        tc.get_account.side_effect = _make_api_error(401, 40110000, "unauthorized")
        client = _make_client(tc)

        ok, msg = client.test_connection()
        assert ok is False
        assert msg  # non-empty error message
