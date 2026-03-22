"""Singleton wrapper around the Alpaca ``TradingClient``.

All dashboard modules call these methods — never the SDK directly.
Each method returns foundation dataclasses, never raw Alpaca objects.
"""

from __future__ import annotations

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, TimeInForce
from alpaca.trading.requests import (
    CreateWatchlistRequest,
    GetOptionContractsRequest,
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    OptionLegRequest,
    StopLimitOrderRequest,
    StopOrderRequest,
)

from dashboard.alpaca_models import (
    AccountSummary,
    AssetInfo,
    OptionContractInfo,
    OrderInfo,
    PositionInfo,
    WatchlistInfo,
)

# ── Enum mappings ──────────────────────────────────────────────────────

_SIDE_MAP = {"buy": OrderSide.BUY, "sell": OrderSide.SELL}

_TIF_MAP = {
    "day": TimeInForce.DAY,
    "gtc": TimeInForce.GTC,
    "ioc": TimeInForce.IOC,
    "fok": TimeInForce.FOK,
}

_POSITION_INTENT_MAP = {
    "buy_to_open": PositionIntent.BUY_TO_OPEN,
    "buy_to_close": PositionIntent.BUY_TO_CLOSE,
    "sell_to_open": PositionIntent.SELL_TO_OPEN,
    "sell_to_close": PositionIntent.SELL_TO_CLOSE,
}


# ── Converters (SDK model → foundation dataclass) ─────────────────────


def _to_account(acct) -> AccountSummary:
    return AccountSummary(
        account_id=str(acct.id),
        equity=float(acct.equity),
        cash=float(acct.cash),
        buying_power=float(acct.buying_power),
        portfolio_value=float(acct.portfolio_value),
        pattern_day_trader=bool(acct.pattern_day_trader),
        trading_blocked=bool(acct.trading_blocked),
        account_blocked=bool(acct.account_blocked),
        daytrade_count=int(acct.daytrade_count or 0),
        currency=str(acct.currency or "USD"),
        status=str(getattr(acct.status, "value", acct.status)),
    )


def _to_position(pos) -> PositionInfo:
    return PositionInfo(
        asset_id=str(pos.asset_id),
        symbol=pos.symbol,
        qty=float(pos.qty),
        side=str(getattr(pos.side, "value", pos.side)),
        avg_entry_price=float(pos.avg_entry_price),
        current_price=float(pos.current_price or 0),
        market_value=float(pos.market_value or 0),
        cost_basis=float(pos.cost_basis or 0),
        unrealized_pl=float(pos.unrealized_pl or 0),
        unrealized_plpc=float(pos.unrealized_plpc or 0),
        change_today=float(pos.change_today or 0),
    )


def _to_order(order) -> OrderInfo:
    order_type = getattr(order, "type", None) or getattr(order, "order_type", None)
    return OrderInfo(
        order_id=str(order.id),
        symbol=order.symbol or "",
        qty=float(order.qty or 0),
        side=str(getattr(order.side, "value", order.side)),
        order_type=str(getattr(order_type, "value", order_type)),
        time_in_force=str(getattr(order.time_in_force, "value", order.time_in_force)),
        status=str(getattr(order.status, "value", order.status)),
        created_at=order.created_at.isoformat() if order.created_at else "",
        filled_at=order.filled_at.isoformat() if order.filled_at else None,
        filled_avg_price=float(order.filled_avg_price) if order.filled_avg_price else None,
        limit_price=float(order.limit_price) if order.limit_price else None,
        stop_price=float(order.stop_price) if order.stop_price else None,
    )


def _to_asset(asset) -> AssetInfo:
    return AssetInfo(
        asset_id=str(asset.id),
        symbol=asset.symbol,
        name=asset.name or "",
        asset_class=str(getattr(asset.asset_class, "value", asset.asset_class)),
        exchange=str(getattr(asset.exchange, "value", asset.exchange)),
        tradable=bool(asset.tradable),
        fractionable=bool(asset.fractionable),
        status=str(getattr(asset.status, "value", asset.status)),
    )


def _to_watchlist(wl) -> WatchlistInfo:
    symbols = [a.symbol for a in (wl.assets or [])]
    return WatchlistInfo(
        watchlist_id=str(wl.id),
        name=wl.name,
        created_at=wl.created_at.isoformat() if wl.created_at else "",
        updated_at=wl.updated_at.isoformat() if wl.updated_at else "",
        symbols=symbols,
    )


def _to_option_contract(oc) -> OptionContractInfo:
    return OptionContractInfo(
        contract_id=str(oc.id),
        symbol=oc.symbol,
        name=oc.name or "",
        underlying_symbol=oc.underlying_symbol or oc.root_symbol or "",
        expiration_date=str(oc.expiration_date) if oc.expiration_date else "",
        strike_price=float(oc.strike_price) if oc.strike_price else 0.0,
        contract_type=str(getattr(oc.type, "value", oc.type)),
        style=str(getattr(oc.style, "value", oc.style)) if oc.style else "american",
        tradable=bool(oc.tradable),
        status=str(getattr(oc.status, "value", oc.status)),
        size=int(oc.size) if oc.size else 100,
        open_interest=int(oc.open_interest) if oc.open_interest else 0,
        close_price=float(oc.close_price) if oc.close_price else 0.0,
    )


# ── Client class ──────────────────────────────────────────────────────


class AlpacaClient:
    """Thin, testable wrapper around ``TradingClient``.

    Create via ``AlpacaClient(api_key, secret_key)`` or inject a mock
    ``TradingClient`` for testing.
    """

    def __init__(self, api_key: str, secret_key: str) -> None:
        self._client = TradingClient(api_key=api_key, secret_key=secret_key, paper=True)

    # ── Account ───────────────────────────────────────────────────────

    def get_account(self) -> AccountSummary:
        raw = self._client.get_account()
        return _to_account(raw)

    # ── Positions ─────────────────────────────────────────────────────

    def get_positions(self) -> list[PositionInfo]:
        raw = self._client.get_all_positions()
        return [_to_position(p) for p in raw]

    # ── Orders ────────────────────────────────────────────────────────

    def submit_order(
        self,
        symbol: str,
        qty: float,
        side: str,
        order_type: str,
        time_in_force: str,
        limit_price: float | None = None,
        stop_price: float | None = None,
    ) -> OrderInfo:
        sd = _SIDE_MAP[side.lower()]
        tif = _TIF_MAP[time_in_force.lower()]

        if order_type == "market":
            req = MarketOrderRequest(symbol=symbol, qty=qty, side=sd, time_in_force=tif)
        elif order_type == "limit":
            req = LimitOrderRequest(
                symbol=symbol, qty=qty, side=sd, time_in_force=tif, limit_price=limit_price
            )
        elif order_type == "stop":
            req = StopOrderRequest(
                symbol=symbol, qty=qty, side=sd, time_in_force=tif, stop_price=stop_price
            )
        elif order_type == "stop_limit":
            req = StopLimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=sd,
                time_in_force=tif,
                limit_price=limit_price,
                stop_price=stop_price,
            )
        else:
            raise ValueError(f"Unsupported order type: {order_type}")

        raw = self._client.submit_order(req)
        return _to_order(raw)

    def get_orders(self, status: str | None = None, limit: int = 100) -> list[OrderInfo]:
        if status:
            params = GetOrdersRequest(status=status, limit=limit)
        else:
            params = GetOrdersRequest(limit=limit)
        raw = self._client.get_orders(params)
        return [_to_order(o) for o in raw]

    def cancel_order(self, order_id: str) -> None:
        self._client.cancel_order_by_id(order_id)

    # ── Assets ────────────────────────────────────────────────────────

    def get_asset(self, symbol: str) -> AssetInfo:
        raw = self._client.get_asset(symbol)
        return _to_asset(raw)

    # ── Positions management ──────────────────────────────────────────

    def close_position(self, symbol: str) -> OrderInfo:
        raw = self._client.close_position(symbol)
        return _to_order(raw)

    # ── Watchlists ────────────────────────────────────────────────────

    def get_watchlists(self) -> list[WatchlistInfo]:
        raw = self._client.get_watchlists()
        return [_to_watchlist(w) for w in raw]

    def create_watchlist(
        self, name: str, symbols: list[str] | None = None
    ) -> WatchlistInfo:
        req = CreateWatchlistRequest(name=name, symbols=symbols or [])
        raw = self._client.create_watchlist(req)
        return _to_watchlist(raw)

    def add_to_watchlist(self, watchlist_id: str, symbol: str) -> WatchlistInfo:
        raw = self._client.add_asset_to_watchlist_by_id(watchlist_id, symbol)
        return _to_watchlist(raw)

    def remove_from_watchlist(self, watchlist_id: str, symbol: str) -> WatchlistInfo:
        raw = self._client.remove_asset_from_watchlist_by_id(watchlist_id, symbol)
        return _to_watchlist(raw)

    def delete_watchlist(self, watchlist_id: str) -> None:
        self._client.delete_watchlist_by_id(watchlist_id)

    # ── Options ─────────────────────────────────────────────────────

    def get_option_contracts(
        self,
        underlying_symbol: str,
        expiration_date: str | None = None,
        contract_type: str | None = None,
        strike_price_gte: float | None = None,
        strike_price_lte: float | None = None,
        limit: int = 100,
    ) -> list[OptionContractInfo]:
        kwargs: dict = {
            "underlying_symbols": [underlying_symbol],
            "limit": limit,
        }
        if expiration_date:
            kwargs["expiration_date"] = expiration_date
        if contract_type:
            from alpaca.trading.enums import ContractType

            kwargs["type"] = (
                ContractType.CALL if contract_type.lower() == "call" else ContractType.PUT
            )
        if strike_price_gte is not None:
            kwargs["strike_price_gte"] = str(strike_price_gte)
        if strike_price_lte is not None:
            kwargs["strike_price_lte"] = str(strike_price_lte)

        req = GetOptionContractsRequest(**kwargs)
        raw = self._client.get_option_contracts(req)
        return [_to_option_contract(c) for c in (raw.option_contracts or [])]

    def get_option_contract(self, symbol_or_id: str) -> OptionContractInfo:
        raw = self._client.get_option_contract(symbol_or_id)
        return _to_option_contract(raw)

    def submit_option_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        order_type: str,
        time_in_force: str,
        position_intent: str,
        limit_price: float | None = None,
    ) -> OrderInfo:
        sd = _SIDE_MAP[side.lower()]
        tif = _TIF_MAP[time_in_force.lower()]
        pi = _POSITION_INTENT_MAP[position_intent.lower()]

        if order_type == "market":
            req = MarketOrderRequest(
                symbol=symbol, qty=qty, side=sd, time_in_force=tif, position_intent=pi
            )
        elif order_type == "limit":
            req = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=sd,
                time_in_force=tif,
                limit_price=limit_price,
                position_intent=pi,
            )
        else:
            raise ValueError(f"Unsupported option order type: {order_type}")

        raw = self._client.submit_order(req)
        return _to_order(raw)

    def submit_multi_leg_order(
        self,
        legs: list[dict],
        qty: int,
        order_type: str,
        time_in_force: str,
        limit_price: float | None = None,
    ) -> OrderInfo:
        if not (2 <= len(legs) <= 4):
            raise ValueError(f"Multi-leg orders require 2 to 4 legs, got {len(legs)}")

        tif = _TIF_MAP[time_in_force.lower()]
        option_legs = [
            OptionLegRequest(
                symbol=leg["symbol"],
                ratio_qty=leg["ratio_qty"],
                side=_SIDE_MAP[leg["side"].lower()],
            )
            for leg in legs
        ]

        if order_type == "market":
            req = MarketOrderRequest(
                qty=qty,
                time_in_force=tif,
                order_class=OrderClass.MLEG,
                legs=option_legs,
            )
        elif order_type == "limit":
            req = LimitOrderRequest(
                qty=qty,
                time_in_force=tif,
                order_class=OrderClass.MLEG,
                legs=option_legs,
                limit_price=limit_price,
            )
        else:
            raise ValueError(f"Unsupported multi-leg order type: {order_type}")

        raw = self._client.submit_order(req)
        return _to_order(raw)

    # ── Connection test ───────────────────────────────────────────────

    def test_connection(self) -> tuple[bool, str]:
        """Verify API keys by calling get_account.

        Returns (True, message) on success, (False, message) on failure.
        """
        try:
            acct = self.get_account()
            return True, f"Connected — paper account {acct.account_id[:8]}…"
        except Exception as exc:
            from dashboard.alpaca_errors import classify_alpaca_error

            classified = classify_alpaca_error(exc)
            return False, classified.user_message
