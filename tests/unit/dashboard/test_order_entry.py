"""Tests for dashboard.views.order_entry — order form & history logic.

Business logic functions are tested as pure functions (no Streamlit mocking).
AlpacaClient calls are mocked via unittest.mock.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from requests import HTTPError, Response

from dashboard.alpaca_models import OrderInfo
from dashboard.views.order_entry import (
    cancel_order_safe,
    filter_orders_by_status,
    format_order_row,
    submit_order_safe,
    validate_order_params,
)


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_order(
    symbol: str = "AAPL",
    status: str = "filled",
    side: str = "buy",
    order_type: str = "market",
    created_at: str = "2026-03-22T14:00:00",
) -> OrderInfo:
    return OrderInfo(
        order_id="ord-1",
        symbol=symbol,
        qty=5.0,
        side=side,
        order_type=order_type,
        time_in_force="day",
        status=status,
        created_at=created_at,
    )


def _make_api_error(status_code: int, message: str):
    from alpaca.common.exceptions import APIError

    body = json.dumps({"code": status_code * 10000, "message": message})
    resp = Response()
    resp.status_code = status_code
    return APIError(body, HTTPError(response=resp))


def _mock_client() -> MagicMock:
    return MagicMock()


# ═════════════════════════════════════════════════════════════════════════
# validate_order_params
# ═════════════════════════════════════════════════════════════════════════


class TestValidateOrderParams:
    def test_valid_market_order(self):
        errors = validate_order_params(
            symbol="AAPL", qty="10", side="buy",
            order_type="market", time_in_force="day",
            limit_price="", stop_price="",
        )
        assert errors == []

    def test_missing_symbol(self):
        errors = validate_order_params(
            symbol="", qty="10", side="buy",
            order_type="market", time_in_force="day",
            limit_price="", stop_price="",
        )
        assert any("symbol" in e.lower() for e in errors)

    def test_missing_qty(self):
        errors = validate_order_params(
            symbol="AAPL", qty="", side="buy",
            order_type="market", time_in_force="day",
            limit_price="", stop_price="",
        )
        assert any("quantity" in e.lower() for e in errors)

    def test_zero_qty(self):
        errors = validate_order_params(
            symbol="AAPL", qty="0", side="buy",
            order_type="market", time_in_force="day",
            limit_price="", stop_price="",
        )
        assert any("quantity" in e.lower() for e in errors)

    def test_negative_qty(self):
        errors = validate_order_params(
            symbol="AAPL", qty="-5", side="buy",
            order_type="market", time_in_force="day",
            limit_price="", stop_price="",
        )
        assert any("quantity" in e.lower() for e in errors)

    def test_non_numeric_qty(self):
        errors = validate_order_params(
            symbol="AAPL", qty="abc", side="buy",
            order_type="market", time_in_force="day",
            limit_price="", stop_price="",
        )
        assert any("quantity" in e.lower() for e in errors)

    def test_limit_order_missing_limit_price(self):
        errors = validate_order_params(
            symbol="AAPL", qty="10", side="buy",
            order_type="limit", time_in_force="day",
            limit_price="", stop_price="",
        )
        assert any("limit price" in e.lower() for e in errors)

    def test_limit_order_valid(self):
        errors = validate_order_params(
            symbol="AAPL", qty="10", side="buy",
            order_type="limit", time_in_force="gtc",
            limit_price="150.50", stop_price="",
        )
        assert errors == []

    def test_stop_order_missing_stop_price(self):
        errors = validate_order_params(
            symbol="AAPL", qty="10", side="sell",
            order_type="stop", time_in_force="day",
            limit_price="", stop_price="",
        )
        assert any("stop price" in e.lower() for e in errors)

    def test_stop_limit_missing_both_prices(self):
        errors = validate_order_params(
            symbol="AAPL", qty="10", side="sell",
            order_type="stop_limit", time_in_force="day",
            limit_price="", stop_price="",
        )
        assert any("limit price" in e.lower() for e in errors)
        assert any("stop price" in e.lower() for e in errors)

    def test_stop_limit_valid(self):
        errors = validate_order_params(
            symbol="AAPL", qty="2", side="sell",
            order_type="stop_limit", time_in_force="day",
            limit_price="200.00", stop_price="195.00",
        )
        assert errors == []

    def test_negative_limit_price(self):
        errors = validate_order_params(
            symbol="AAPL", qty="10", side="buy",
            order_type="limit", time_in_force="day",
            limit_price="-5", stop_price="",
        )
        assert any("limit price" in e.lower() for e in errors)

    def test_negative_stop_price(self):
        errors = validate_order_params(
            symbol="AAPL", qty="10", side="sell",
            order_type="stop", time_in_force="day",
            limit_price="", stop_price="-10",
        )
        assert any("stop price" in e.lower() for e in errors)


# ═════════════════════════════════════════════════════════════════════════
# filter_orders_by_status
# ═════════════════════════════════════════════════════════════════════════


class TestFilterOrdersByStatus:
    def test_all_returns_everything(self):
        orders = [
            _make_order(status="filled"),
            _make_order(status="new"),
            _make_order(status="canceled"),
        ]
        result = filter_orders_by_status(orders, "all")
        assert len(result) == 3

    def test_filter_filled(self):
        orders = [
            _make_order(status="filled"),
            _make_order(status="new"),
            _make_order(status="canceled"),
        ]
        result = filter_orders_by_status(orders, "filled")
        assert len(result) == 1
        assert result[0].status == "filled"

    def test_filter_open(self):
        orders = [
            _make_order(status="new"),
            _make_order(status="partially_filled"),
            _make_order(status="accepted"),
            _make_order(status="filled"),
            _make_order(status="canceled"),
        ]
        result = filter_orders_by_status(orders, "open")
        assert len(result) == 3
        assert all(o.status in ("new", "partially_filled", "accepted") for o in result)

    def test_filter_canceled(self):
        orders = [
            _make_order(status="filled"),
            _make_order(status="canceled"),
            _make_order(status="canceled"),
        ]
        result = filter_orders_by_status(orders, "canceled")
        assert len(result) == 2

    def test_filter_rejected(self):
        orders = [
            _make_order(status="filled"),
            _make_order(status="rejected"),
        ]
        result = filter_orders_by_status(orders, "rejected")
        assert len(result) == 1
        assert result[0].status == "rejected"

    def test_empty_list(self):
        result = filter_orders_by_status([], "all")
        assert result == []

    def test_no_matches(self):
        orders = [_make_order(status="filled")]
        result = filter_orders_by_status(orders, "rejected")
        assert result == []


# ═════════════════════════════════════════════════════════════════════════
# format_order_row
# ═════════════════════════════════════════════════════════════════════════


class TestFormatOrderRow:
    def test_market_order_filled(self):
        order = OrderInfo(
            order_id="ord-1",
            symbol="AAPL",
            qty=5.0,
            side="buy",
            order_type="market",
            time_in_force="day",
            status="filled",
            created_at="2026-03-22T14:00:00",
            filled_at="2026-03-22T14:00:05",
            filled_avg_price=155.50,
        )
        row = format_order_row(order)
        assert row["Symbol"] == "AAPL"
        assert row["Side"] == "BUY"
        assert row["Qty"] == 5.0
        assert row["Type"] == "MARKET"
        assert row["Status"] == "FILLED"
        assert "$155.50" in row["Fill Price"]
        assert row["TIF"] == "DAY"

    def test_limit_order_open(self):
        order = OrderInfo(
            order_id="ord-2",
            symbol="GOOG",
            qty=3.0,
            side="sell",
            order_type="limit",
            time_in_force="gtc",
            status="new",
            created_at="2026-03-22T10:00:00",
            limit_price=180.25,
        )
        row = format_order_row(order)
        assert row["Side"] == "SELL"
        assert row["Type"] == "LIMIT"
        assert "$180.25" in row["Limit"]
        assert row["Status"] == "NEW"
        # No fill price for unfilled order
        assert row["Fill Price"] == "\u2014"

    def test_stop_limit_order(self):
        order = OrderInfo(
            order_id="ord-3",
            symbol="TSLA",
            qty=2.0,
            side="sell",
            order_type="stop_limit",
            time_in_force="day",
            status="accepted",
            created_at="2026-03-22T09:30:00",
            limit_price=200.00,
            stop_price=195.00,
        )
        row = format_order_row(order)
        assert "$200.00" in row["Limit"]
        assert "$195.00" in row["Stop"]

    def test_no_prices_shows_dash(self):
        order = _make_order(order_type="market", status="filled")
        row = format_order_row(order)
        assert row["Limit"] == "\u2014"
        assert row["Stop"] == "\u2014"


# ═════════════════════════════════════════════════════════════════════════
# submit_order_safe
# ═════════════════════════════════════════════════════════════════════════


class TestSubmitOrderSafe:
    def test_success(self):
        client = _mock_client()
        client.submit_order.return_value = _make_order(status="new")
        ok, result = submit_order_safe(
            client, symbol="AAPL", qty=5.0, side="buy",
            order_type="market", time_in_force="day",
        )
        assert ok is True
        assert isinstance(result, OrderInfo)
        assert result.status == "new"

    def test_limit_order_passes_prices(self):
        client = _mock_client()
        client.submit_order.return_value = _make_order(status="new")
        ok, result = submit_order_safe(
            client, symbol="GOOG", qty=3.0, side="buy",
            order_type="limit", time_in_force="gtc",
            limit_price=140.0,
        )
        assert ok is True
        client.submit_order.assert_called_once_with(
            symbol="GOOG", qty=3.0, side="buy",
            order_type="limit", time_in_force="gtc",
            limit_price=140.0, stop_price=None,
        )

    def test_insufficient_buying_power(self):
        client = _mock_client()
        client.submit_order.side_effect = _make_api_error(403, "insufficient buying power")
        ok, msg = submit_order_safe(
            client, symbol="BRK.A", qty=1.0, side="buy",
            order_type="market", time_in_force="day",
        )
        assert ok is False
        assert "buying power" in msg.lower()

    def test_market_closed(self):
        client = _mock_client()
        client.submit_order.side_effect = _make_api_error(403, "market is not open")
        ok, msg = submit_order_safe(
            client, symbol="AAPL", qty=1.0, side="buy",
            order_type="market", time_in_force="day",
        )
        assert ok is False
        assert "closed" in msg.lower() or "market" in msg.lower()

    def test_invalid_symbol(self):
        client = _mock_client()
        client.submit_order.side_effect = _make_api_error(404, "asset not found")
        ok, msg = submit_order_safe(
            client, symbol="XYZZZZ", qty=1.0, side="buy",
            order_type="market", time_in_force="day",
        )
        assert ok is False
        assert "symbol" in msg.lower() or "ticker" in msg.lower()

    def test_pdt_violation(self):
        client = _mock_client()
        client.submit_order.side_effect = _make_api_error(
            403, "pattern day trading protection"
        )
        ok, msg = submit_order_safe(
            client, symbol="AAPL", qty=1.0, side="buy",
            order_type="market", time_in_force="day",
        )
        assert ok is False
        assert "day trad" in msg.lower()

    def test_rate_limit(self):
        client = _mock_client()
        client.submit_order.side_effect = _make_api_error(429, "rate limit exceeded")
        ok, msg = submit_order_safe(
            client, symbol="AAPL", qty=1.0, side="buy",
            order_type="market", time_in_force="day",
        )
        assert ok is False
        assert "rate limit" in msg.lower()

    def test_auth_failure(self):
        client = _mock_client()
        client.submit_order.side_effect = _make_api_error(401, "unauthorized")
        ok, msg = submit_order_safe(
            client, symbol="AAPL", qty=1.0, side="buy",
            order_type="market", time_in_force="day",
        )
        assert ok is False
        assert "auth" in msg.lower()

    def test_timeout(self):
        from requests.exceptions import Timeout

        client = _mock_client()
        client.submit_order.side_effect = Timeout("timed out")
        ok, msg = submit_order_safe(
            client, symbol="AAPL", qty=1.0, side="buy",
            order_type="market", time_in_force="day",
        )
        assert ok is False
        assert "timed out" in msg.lower() or "timeout" in msg.lower()


# ═════════════════════════════════════════════════════════════════════════
# cancel_order_safe
# ═════════════════════════════════════════════════════════════════════════


class TestCancelOrderSafe:
    def test_success(self):
        client = _mock_client()
        client.cancel_order.return_value = None
        ok, msg = cancel_order_safe(client, "ord-123")
        assert ok is True
        assert msg  # non-empty success message
        client.cancel_order.assert_called_once_with("ord-123")

    def test_order_not_found(self):
        client = _mock_client()
        client.cancel_order.side_effect = _make_api_error(404, "order not found")
        ok, msg = cancel_order_safe(client, "nonexistent")
        assert ok is False
        assert msg

    def test_already_filled(self):
        client = _mock_client()
        client.cancel_order.side_effect = _make_api_error(422, "order is not cancelable")
        ok, msg = cancel_order_safe(client, "ord-filled")
        assert ok is False
        assert msg
