"""Tests for dashboard.alpaca_models dataclasses."""

from __future__ import annotations

from dataclasses import asdict

from dashboard.alpaca_models import (
    AccountSummary,
    AssetInfo,
    OrderInfo,
    PositionInfo,
    WatchlistInfo,
)


class TestAccountSummary:
    def test_create_with_all_fields(self):
        acct = AccountSummary(
            account_id="abc-123",
            equity=100_000.0,
            cash=25_000.0,
            buying_power=50_000.0,
            portfolio_value=100_000.0,
            pattern_day_trader=False,
            trading_blocked=False,
            account_blocked=False,
            daytrade_count=2,
            currency="USD",
            status="ACTIVE",
        )
        assert acct.equity == 100_000.0
        assert acct.pattern_day_trader is False
        assert acct.daytrade_count == 2

    def test_defaults(self):
        acct = AccountSummary(
            account_id="x",
            equity=0,
            cash=0,
            buying_power=0,
            portfolio_value=0,
        )
        assert acct.pattern_day_trader is False
        assert acct.trading_blocked is False
        assert acct.account_blocked is False
        assert acct.daytrade_count == 0
        assert acct.currency == "USD"
        assert acct.status == "ACTIVE"

    def test_serializable_to_dict(self):
        acct = AccountSummary(
            account_id="x", equity=1.0, cash=2.0, buying_power=3.0, portfolio_value=4.0
        )
        d = asdict(acct)
        assert d["equity"] == 1.0
        assert isinstance(d, dict)


class TestPositionInfo:
    def test_create_with_all_fields(self):
        pos = PositionInfo(
            asset_id="id-1",
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
        assert pos.symbol == "AAPL"
        assert pos.unrealized_pl == 100.0

    def test_defaults(self):
        pos = PositionInfo(
            asset_id="id-1",
            symbol="MSFT",
            qty=5.0,
            side="long",
            avg_entry_price=300.0,
        )
        assert pos.current_price == 0.0
        assert pos.market_value == 0.0
        assert pos.cost_basis == 0.0
        assert pos.unrealized_pl == 0.0
        assert pos.unrealized_plpc == 0.0
        assert pos.change_today == 0.0


class TestOrderInfo:
    def test_create_with_all_fields(self):
        order = OrderInfo(
            order_id="ord-1",
            symbol="GOOG",
            qty=3.0,
            side="buy",
            order_type="market",
            time_in_force="day",
            status="filled",
            created_at="2026-03-22T10:00:00Z",
            filled_at="2026-03-22T10:00:01Z",
            filled_avg_price=140.0,
            limit_price=None,
            stop_price=None,
        )
        assert order.symbol == "GOOG"
        assert order.status == "filled"

    def test_defaults(self):
        order = OrderInfo(
            order_id="ord-2",
            symbol="TSLA",
            qty=1.0,
            side="sell",
            order_type="limit",
            time_in_force="gtc",
            status="new",
        )
        assert order.created_at == ""
        assert order.filled_at is None
        assert order.filled_avg_price is None
        assert order.limit_price is None
        assert order.stop_price is None


class TestAssetInfo:
    def test_create(self):
        asset = AssetInfo(
            asset_id="a-1",
            symbol="AAPL",
            name="Apple Inc.",
            asset_class="us_equity",
            exchange="NASDAQ",
            tradable=True,
            fractionable=True,
            status="active",
        )
        assert asset.tradable is True
        assert asset.name == "Apple Inc."

    def test_defaults(self):
        asset = AssetInfo(
            asset_id="a-2",
            symbol="XYZ",
        )
        assert asset.name == ""
        assert asset.asset_class == "us_equity"
        assert asset.exchange == ""
        assert asset.tradable is False
        assert asset.fractionable is False
        assert asset.status == "active"


class TestWatchlistInfo:
    def test_create_with_all_fields(self):
        wl = WatchlistInfo(
            watchlist_id="wl-1",
            name="Tech Stocks",
            created_at="2026-03-22T10:00:00Z",
            updated_at="2026-03-22T10:00:00Z",
            symbols=["AAPL", "GOOG", "MSFT"],
        )
        assert wl.name == "Tech Stocks"
        assert len(wl.symbols) == 3

    def test_defaults(self):
        wl = WatchlistInfo(watchlist_id="wl-2", name="Empty")
        assert wl.created_at == ""
        assert wl.updated_at == ""
        assert wl.symbols == []

    def test_symbols_default_is_independent(self):
        """Each instance gets its own list, not a shared mutable default."""
        wl1 = WatchlistInfo(watchlist_id="a", name="A")
        wl2 = WatchlistInfo(watchlist_id="b", name="B")
        wl1.symbols.append("AAPL")
        assert wl2.symbols == []
