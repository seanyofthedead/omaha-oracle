"""Tests for the options trading feature (Worktree D).

Covers:
  - OptionContractInfo dataclass
  - AlpacaClient options methods (get_option_contracts, get_option_contract,
    submit_option_order, submit_multi_leg_order)
  - Options chain filtering/formatting logic
  - Options order building logic (single-leg, multi-leg)
  - Error handling: invalid symbol, market closed, empty chain
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from requests import HTTPError, Response

from dashboard.alpaca_models import OptionContractInfo

# ── Helpers ──────────────────────────────────────────────────────────────

_NOW = datetime(2026, 3, 22, 14, 0, 0, tzinfo=UTC)
_UUID = uuid4()


def _make_api_error(status_code: int, code: int, message: str):
    from alpaca.common.exceptions import APIError

    body = json.dumps({"code": code, "message": message})
    resp = Response()
    resp.status_code = status_code
    return APIError(body, HTTPError(response=resp))


def _mock_trading_client() -> MagicMock:
    return MagicMock()


def _make_client(mock_tc: MagicMock | None = None):
    from dashboard.alpaca_client import AlpacaClient

    client = AlpacaClient.__new__(AlpacaClient)
    client._client = mock_tc or _mock_trading_client()
    return client


# ── Fake SDK models ──────────────────────────────────────────────────────


def _fake_option_contract(
    symbol: str = "AAPL250418C00200000",
    underlying: str = "AAPL",
    contract_type: str = "call",
    strike: str = "200.00",
    expiration: str = "2025-04-18",
):
    m = MagicMock()
    m.id = _UUID
    m.symbol = symbol
    m.name = f"{underlying} Option"
    m.status = MagicMock()
    m.status.value = "active"
    m.tradable = True
    m.expiration_date = date.fromisoformat(expiration)
    m.root_symbol = underlying
    m.underlying_symbol = underlying
    m.underlying_asset_id = _UUID
    m.type = MagicMock()
    m.type.value = contract_type
    m.style = MagicMock()
    m.style.value = "american"
    m.strike_price = strike
    m.size = "100"
    m.open_interest = "1500"
    m.open_interest_date = date(2026, 3, 21)
    m.close_price = "5.25"
    m.close_price_date = date(2026, 3, 21)
    return m


def _fake_option_contracts_response(contracts: list | None = None):
    m = MagicMock()
    m.option_contracts = contracts or []
    m.next_page_token = None
    return m


def _fake_order(symbol: str = "AAPL250418C00200000", status: str = "new"):
    m = MagicMock()
    m.id = _UUID
    m.symbol = symbol
    m.qty = "1"
    m.side = MagicMock()
    m.side.value = "buy"
    m.order_type = MagicMock()
    m.order_type.value = "limit"
    m.type = MagicMock()
    m.type.value = "limit"
    m.time_in_force = MagicMock()
    m.time_in_force.value = "day"
    m.status = MagicMock()
    m.status.value = status
    m.created_at = _NOW
    m.filled_at = None
    m.filled_avg_price = None
    m.limit_price = "5.00"
    m.stop_price = None
    return m


# ═════════════════════════════════════════════════════════════════════════
# OptionContractInfo dataclass
# ═════════════════════════════════════════════════════════════════════════


class TestOptionContractInfo:
    def test_create_with_defaults(self):
        oc = OptionContractInfo(
            contract_id="abc-123",
            symbol="AAPL250418C00200000",
            underlying_symbol="AAPL",
            expiration_date="2025-04-18",
            strike_price=200.0,
            contract_type="call",
        )
        assert oc.symbol == "AAPL250418C00200000"
        assert oc.contract_type == "call"
        assert oc.strike_price == 200.0
        assert oc.style == "american"
        assert oc.tradable is False
        assert oc.open_interest == 0
        assert oc.close_price == 0.0
        assert oc.size == 100

    def test_create_put(self):
        oc = OptionContractInfo(
            contract_id="def-456",
            symbol="AAPL250418P00180000",
            underlying_symbol="AAPL",
            expiration_date="2025-04-18",
            strike_price=180.0,
            contract_type="put",
            open_interest=2500,
            close_price=3.10,
        )
        assert oc.contract_type == "put"
        assert oc.open_interest == 2500
        assert oc.close_price == 3.10


# ═════════════════════════════════════════════════════════════════════════
# AlpacaClient.get_option_contracts
# ═════════════════════════════════════════════════════════════════════════


class TestGetOptionContracts:
    def test_success(self):
        tc = _mock_trading_client()
        tc.get_option_contracts.return_value = _fake_option_contracts_response(
            [
                _fake_option_contract("AAPL250418C00200000", "AAPL", "call", "200.00"),
                _fake_option_contract("AAPL250418P00200000", "AAPL", "put", "200.00"),
            ]
        )
        client = _make_client(tc)

        result = client.get_option_contracts("AAPL")
        assert len(result) == 2
        assert all(isinstance(c, OptionContractInfo) for c in result)
        assert result[0].contract_type == "call"
        assert result[1].contract_type == "put"
        tc.get_option_contracts.assert_called_once()

    def test_empty_chain(self):
        tc = _mock_trading_client()
        tc.get_option_contracts.return_value = _fake_option_contracts_response([])
        client = _make_client(tc)

        result = client.get_option_contracts("AAPL")
        assert result == []

    def test_with_filters(self):
        tc = _mock_trading_client()
        tc.get_option_contracts.return_value = _fake_option_contracts_response(
            [_fake_option_contract()]
        )
        client = _make_client(tc)

        result = client.get_option_contracts(
            "AAPL",
            expiration_date="2025-04-18",
            contract_type="call",
            strike_price_gte=190.0,
            strike_price_lte=210.0,
        )
        assert len(result) == 1
        tc.get_option_contracts.assert_called_once()

    def test_invalid_symbol(self):
        tc = _mock_trading_client()
        tc.get_option_contracts.side_effect = _make_api_error(
            404, 40410000, "asset not found"
        )
        client = _make_client(tc)

        with pytest.raises(Exception):
            client.get_option_contracts("XYZZZZ")

    def test_rate_limit(self):
        tc = _mock_trading_client()
        tc.get_option_contracts.side_effect = _make_api_error(
            429, 42900000, "rate limit"
        )
        client = _make_client(tc)

        with pytest.raises(Exception):
            client.get_option_contracts("AAPL")


# ═════════════════════════════════════════════════════════════════════════
# AlpacaClient.get_option_contract (single)
# ═════════════════════════════════════════════════════════════════════════


class TestGetOptionContract:
    def test_success(self):
        tc = _mock_trading_client()
        tc.get_option_contract.return_value = _fake_option_contract()
        client = _make_client(tc)

        result = client.get_option_contract("AAPL250418C00200000")
        assert isinstance(result, OptionContractInfo)
        assert result.symbol == "AAPL250418C00200000"
        assert result.strike_price == 200.0

    def test_not_found(self):
        tc = _mock_trading_client()
        tc.get_option_contract.side_effect = _make_api_error(
            404, 40410000, "could not find asset"
        )
        client = _make_client(tc)

        with pytest.raises(Exception):
            client.get_option_contract("INVALID000000")


# ═════════════════════════════════════════════════════════════════════════
# AlpacaClient.submit_option_order (single-leg)
# ═════════════════════════════════════════════════════════════════════════


class TestSubmitOptionOrder:
    def test_buy_call_limit(self):
        tc = _mock_trading_client()
        tc.submit_order.return_value = _fake_order("AAPL250418C00200000", "new")
        client = _make_client(tc)

        result = client.submit_option_order(
            symbol="AAPL250418C00200000",
            qty=1,
            side="buy",
            order_type="limit",
            time_in_force="day",
            position_intent="buy_to_open",
            limit_price=5.00,
        )
        from dashboard.alpaca_models import OrderInfo

        assert isinstance(result, OrderInfo)
        assert result.symbol == "AAPL250418C00200000"
        tc.submit_order.assert_called_once()

    def test_sell_put_market(self):
        tc = _mock_trading_client()
        tc.submit_order.return_value = _fake_order("AAPL250418P00180000", "new")
        client = _make_client(tc)

        result = client.submit_option_order(
            symbol="AAPL250418P00180000",
            qty=2,
            side="sell",
            order_type="market",
            time_in_force="day",
            position_intent="sell_to_close",
        )
        from dashboard.alpaca_models import OrderInfo

        assert isinstance(result, OrderInfo)
        tc.submit_order.assert_called_once()

    def test_market_closed(self):
        tc = _mock_trading_client()
        tc.submit_order.side_effect = _make_api_error(
            403, 40310000, "market is not open for trading"
        )
        client = _make_client(tc)

        with pytest.raises(Exception):
            client.submit_option_order(
                symbol="AAPL250418C00200000",
                qty=1,
                side="buy",
                order_type="limit",
                time_in_force="day",
                position_intent="buy_to_open",
                limit_price=5.00,
            )

    def test_insufficient_buying_power(self):
        tc = _mock_trading_client()
        tc.submit_order.side_effect = _make_api_error(
            403, 40310000, "insufficient buying power"
        )
        client = _make_client(tc)

        with pytest.raises(Exception):
            client.submit_option_order(
                symbol="AAPL250418C00200000",
                qty=100,
                side="buy",
                order_type="limit",
                time_in_force="day",
                position_intent="buy_to_open",
                limit_price=50.00,
            )


# ═════════════════════════════════════════════════════════════════════════
# AlpacaClient.submit_multi_leg_order
# ═════════════════════════════════════════════════════════════════════════


class TestSubmitMultiLegOrder:
    def test_vertical_spread(self):
        tc = _mock_trading_client()
        tc.submit_order.return_value = _fake_order("AAPL250418C00200000", "new")
        client = _make_client(tc)

        legs = [
            {"symbol": "AAPL250418C00200000", "ratio_qty": 1, "side": "buy"},
            {"symbol": "AAPL250418C00210000", "ratio_qty": 1, "side": "sell"},
        ]
        result = client.submit_multi_leg_order(
            legs=legs,
            qty=1,
            order_type="limit",
            time_in_force="day",
            limit_price=2.50,
        )
        from dashboard.alpaca_models import OrderInfo

        assert isinstance(result, OrderInfo)
        tc.submit_order.assert_called_once()

    def test_straddle(self):
        tc = _mock_trading_client()
        tc.submit_order.return_value = _fake_order("AAPL250418C00200000", "new")
        client = _make_client(tc)

        legs = [
            {"symbol": "AAPL250418C00200000", "ratio_qty": 1, "side": "buy"},
            {"symbol": "AAPL250418P00200000", "ratio_qty": 1, "side": "buy"},
        ]
        result = client.submit_multi_leg_order(
            legs=legs,
            qty=1,
            order_type="limit",
            time_in_force="day",
            limit_price=10.00,
        )
        from dashboard.alpaca_models import OrderInfo

        assert isinstance(result, OrderInfo)

    def test_too_many_legs(self):
        client = _make_client()

        legs = [
            {"symbol": f"AAPL250418C0020{i}000", "ratio_qty": 1, "side": "buy"}
            for i in range(5)
        ]
        with pytest.raises(ValueError, match="2 to 4 legs"):
            client.submit_multi_leg_order(
                legs=legs, qty=1, order_type="limit", time_in_force="day"
            )

    def test_too_few_legs(self):
        client = _make_client()

        legs = [{"symbol": "AAPL250418C00200000", "ratio_qty": 1, "side": "buy"}]
        with pytest.raises(ValueError, match="2 to 4 legs"):
            client.submit_multi_leg_order(
                legs=legs, qty=1, order_type="limit", time_in_force="day"
            )

    def test_market_closed(self):
        tc = _mock_trading_client()
        tc.submit_order.side_effect = _make_api_error(
            403, 40310000, "market is not open for trading"
        )
        client = _make_client(tc)

        legs = [
            {"symbol": "AAPL250418C00200000", "ratio_qty": 1, "side": "buy"},
            {"symbol": "AAPL250418C00210000", "ratio_qty": 1, "side": "sell"},
        ]
        with pytest.raises(Exception):
            client.submit_multi_leg_order(
                legs=legs, qty=1, order_type="limit", time_in_force="day"
            )


# ═════════════════════════════════════════════════════════════════════════
# Options chain logic (options_chain.py)
# ═════════════════════════════════════════════════════════════════════════


def _sample_contracts() -> list[OptionContractInfo]:
    """Build a realistic set of contracts for filtering tests."""
    return [
        OptionContractInfo(
            contract_id="1",
            symbol="AAPL250418C00190000",
            underlying_symbol="AAPL",
            expiration_date="2025-04-18",
            strike_price=190.0,
            contract_type="call",
            tradable=True,
            open_interest=1200,
            close_price=15.30,
        ),
        OptionContractInfo(
            contract_id="2",
            symbol="AAPL250418C00200000",
            underlying_symbol="AAPL",
            expiration_date="2025-04-18",
            strike_price=200.0,
            contract_type="call",
            tradable=True,
            open_interest=3400,
            close_price=8.50,
        ),
        OptionContractInfo(
            contract_id="3",
            symbol="AAPL250418P00200000",
            underlying_symbol="AAPL",
            expiration_date="2025-04-18",
            strike_price=200.0,
            contract_type="put",
            tradable=True,
            open_interest=2800,
            close_price=5.25,
        ),
        OptionContractInfo(
            contract_id="4",
            symbol="AAPL250425C00200000",
            underlying_symbol="AAPL",
            expiration_date="2025-04-25",
            strike_price=200.0,
            contract_type="call",
            tradable=True,
            open_interest=900,
            close_price=10.10,
        ),
        OptionContractInfo(
            contract_id="5",
            symbol="AAPL250418P00190000",
            underlying_symbol="AAPL",
            expiration_date="2025-04-18",
            strike_price=190.0,
            contract_type="put",
            tradable=False,
            open_interest=0,
            close_price=2.00,
        ),
    ]


class TestFilterContracts:
    def test_filter_by_type_call(self):
        from dashboard.options_chain import filter_contracts

        result = filter_contracts(_sample_contracts(), contract_type="call")
        assert all(c.contract_type == "call" for c in result)
        assert len(result) == 3

    def test_filter_by_type_put(self):
        from dashboard.options_chain import filter_contracts

        result = filter_contracts(_sample_contracts(), contract_type="put")
        assert all(c.contract_type == "put" for c in result)
        assert len(result) == 2

    def test_filter_by_expiration(self):
        from dashboard.options_chain import filter_contracts

        result = filter_contracts(_sample_contracts(), expiration_date="2025-04-18")
        assert all(c.expiration_date == "2025-04-18" for c in result)
        assert len(result) == 4

    def test_filter_by_strike_range(self):
        from dashboard.options_chain import filter_contracts

        result = filter_contracts(
            _sample_contracts(), strike_min=195.0, strike_max=205.0
        )
        assert all(195.0 <= c.strike_price <= 205.0 for c in result)
        assert len(result) == 3

    def test_filter_combined(self):
        from dashboard.options_chain import filter_contracts

        result = filter_contracts(
            _sample_contracts(),
            contract_type="call",
            expiration_date="2025-04-18",
            strike_min=195.0,
        )
        assert len(result) == 1
        assert result[0].strike_price == 200.0

    def test_filter_empty_input(self):
        from dashboard.options_chain import filter_contracts

        result = filter_contracts([], contract_type="call")
        assert result == []

    def test_no_filters_returns_all(self):
        from dashboard.options_chain import filter_contracts

        contracts = _sample_contracts()
        result = filter_contracts(contracts)
        assert len(result) == len(contracts)


class TestGetExpirations:
    def test_unique_sorted(self):
        from dashboard.options_chain import get_expirations

        result = get_expirations(_sample_contracts())
        assert result == ["2025-04-18", "2025-04-25"]

    def test_empty(self):
        from dashboard.options_chain import get_expirations

        assert get_expirations([]) == []


class TestGetStrikes:
    def test_unique_sorted(self):
        from dashboard.options_chain import get_strikes

        result = get_strikes(_sample_contracts())
        assert result == [190.0, 200.0]

    def test_empty(self):
        from dashboard.options_chain import get_strikes

        assert get_strikes([]) == []


class TestContractsToDataframe:
    def test_basic(self):
        from dashboard.options_chain import contracts_to_dataframe

        df = contracts_to_dataframe(_sample_contracts()[:2])
        assert len(df) == 2
        assert "Symbol" in df.columns
        assert "Strike" in df.columns
        assert "Type" in df.columns
        assert "Open Interest" in df.columns
        assert "Last Price" in df.columns

    def test_empty(self):
        from dashboard.options_chain import contracts_to_dataframe

        df = contracts_to_dataframe([])
        assert len(df) == 0


# ═════════════════════════════════════════════════════════════════════════
# Options order builder logic (options_orders.py)
# ═════════════════════════════════════════════════════════════════════════


class TestBuildVerticalSpreadLegs:
    def test_bull_call_spread(self):
        from dashboard.options_orders import build_vertical_spread_legs

        legs = build_vertical_spread_legs(
            long_symbol="AAPL250418C00200000",
            short_symbol="AAPL250418C00210000",
        )
        assert len(legs) == 2
        assert legs[0]["side"] == "buy"
        assert legs[1]["side"] == "sell"
        assert legs[0]["ratio_qty"] == 1
        assert legs[1]["ratio_qty"] == 1

    def test_bear_put_spread(self):
        from dashboard.options_orders import build_vertical_spread_legs

        legs = build_vertical_spread_legs(
            long_symbol="AAPL250418P00210000",
            short_symbol="AAPL250418P00200000",
        )
        assert legs[0]["symbol"] == "AAPL250418P00210000"
        assert legs[0]["side"] == "buy"


class TestBuildStraddleLegs:
    def test_straddle(self):
        from dashboard.options_orders import build_straddle_legs

        legs = build_straddle_legs(
            call_symbol="AAPL250418C00200000",
            put_symbol="AAPL250418P00200000",
        )
        assert len(legs) == 2
        assert legs[0]["side"] == "buy"
        assert legs[1]["side"] == "buy"
        assert legs[0]["symbol"].endswith("C00200000")
        assert legs[1]["symbol"].endswith("P00200000")


class TestBuildStrangleLegs:
    def test_strangle(self):
        from dashboard.options_orders import build_strangle_legs

        legs = build_strangle_legs(
            call_symbol="AAPL250418C00210000",
            put_symbol="AAPL250418P00190000",
        )
        assert len(legs) == 2
        assert legs[0]["side"] == "buy"
        assert legs[1]["side"] == "buy"


class TestValidateOptionOrder:
    def test_valid_single_leg(self):
        from dashboard.options_orders import validate_option_order

        errors = validate_option_order(
            symbol="AAPL250418C00200000",
            qty=1,
            side="buy",
            order_type="limit",
            limit_price=5.00,
        )
        assert errors == []

    def test_missing_symbol(self):
        from dashboard.options_orders import validate_option_order

        errors = validate_option_order(
            symbol="", qty=1, side="buy", order_type="market"
        )
        assert any("symbol" in e.lower() for e in errors)

    def test_zero_qty(self):
        from dashboard.options_orders import validate_option_order

        errors = validate_option_order(
            symbol="AAPL250418C00200000", qty=0, side="buy", order_type="market"
        )
        assert any("quantity" in e.lower() for e in errors)

    def test_limit_order_no_price(self):
        from dashboard.options_orders import validate_option_order

        errors = validate_option_order(
            symbol="AAPL250418C00200000",
            qty=1,
            side="buy",
            order_type="limit",
            limit_price=None,
        )
        assert any("limit price" in e.lower() for e in errors)

    def test_market_order_no_price_ok(self):
        from dashboard.options_orders import validate_option_order

        errors = validate_option_order(
            symbol="AAPL250418C00200000",
            qty=1,
            side="buy",
            order_type="market",
        )
        assert errors == []
